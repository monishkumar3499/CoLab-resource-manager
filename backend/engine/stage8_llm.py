"""
stage8_llm.py — LLM Recommendation Intelligence

The LLM is the final enrichment layer only.
It receives structured output from the deterministic engine and generates:
  - Executive summary
  - Why this team was selected
  - Why alternatives were rejected
  - Business impact narrative
  - Swap chain explanation
  - Hiring justification
  - Risk and assumptions summary

The LLM NEVER changes rankings, scores, or allocations.
Every decision is made upstream by the deterministic engine.

Fallback chain (attempted in order):
  Stage 1: Configured model (LLM_MODEL env var / cfg.llm.model)
  Stage 2: nvidia/nemotron-3-ultra-550b-a55b:free  (OpenRouter — skipped if no key)
  Stage 3: nvidia/nemotron-3-super-120b-a12b:free  (OpenRouter — skipped if no key)
  Stage 4: Local Ollama (OLLAMA_MODEL / cfg.llm.model via OLLAMA_BASE_URL)
  Stage 5: Deterministic rule-based summary (always available)
"""

from __future__ import annotations

import json
import os
import textwrap
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import EngineConfig, DEFAULT_CONFIG
from .stage7_plans import ProjectStaffingPlan, RoleStaffingPlan, StaffingOption


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMEnrichedOutput:
    pipeline_id: str
    client: str

    # Narrative outputs
    executive_summary: str
    recommendation_rationale: str
    alternative_rejection_summary: str
    business_impact_narrative: str
    swap_chain_explanation: str
    hiring_justification: str
    risks_and_assumptions: str
    rm_action_notes: str             # concise bullet list for the RM team

    # Plan comparison table (structured text)
    plan_comparison_table: str

    # Fallback flag: True if LLM was unavailable and rule-based text was used
    is_fallback: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_prompt(plan: ProjectStaffingPlan) -> str:
    """
    Build a structured prompt for the LLM.
    The LLM receives a JSON summary of the plan — not raw data.
    It is explicitly told not to change any decision.
    """

    # Build compact role summaries
    role_summaries = []
    for rp in plan.role_plans:
        opt = rp.recommended_option
        req_role = rp.required_role if hasattr(rp, "required_role") else None
        if opt:
            role_summaries.append({
                "role": rp.role_name,
                "required_role": req_role,
                "tier": rp.seniority_tier,
                "allocation_pct": rp.required_pct,
                "plan": opt.plan_label,
                "employee": opt.recommended_employee_id or "HIRE",
                "score": opt.composite_score,
                "score_breakdown": opt.score_breakdown,
                "confidence": opt.confidence_band,
                "delay_days": opt.estimated_delay_days,
                "swap_steps": opt.swap_chain_summary,
                "business_impact": opt.business_impact_summary,
                "complexity": opt.implementation_complexity,
            })
        else:
            role_summaries.append({
                "role": rp.role_name,
                "required_role": req_role,
                "tier": rp.seniority_tier,
                "allocation_pct": rp.required_pct,
                "plan": "No option found",
                "employee": None,
                "score": 0.0,
                "confidence": "LOW",
                "gap_reason": rp.gap_reason,
            })

    plan_summary = {
        "pipeline_id": plan.pipeline_id,
        "client": plan.client,
        "client_priority": plan.client_priority,
        "request_priority": plan.request_priority,
        "sow_signed": plan.sow_signed,
        "likely_start": plan.likely_start_date,
        "total_roles": plan.total_roles,
        "roles_immediate": plan.roles_filled_immediate,
        "roles_via_swap": plan.roles_filled_via_swap,
        "roles_via_wait": plan.roles_filled_via_wait,
        "roles_needing_hire": plan.roles_needing_hire,
        "roles_unfilled": plan.roles_unfilled,
        "coverage_pct": plan.coverage_pct,
        "overall_complexity": plan.overall_complexity,
        "extend_start_recommended": plan.extend_start_date_recommended,
        "recommended_start": plan.recommended_start_date,
        "hire_by_tier": plan.hire_headcount_by_tier,
        "hire_by_role": plan.hire_headcount_by_role if hasattr(plan, "hire_headcount_by_role") else {},
        "implementation_sequence": plan.implementation_sequence,
        "composite_confidence": plan.composite_confidence,
        "role_plans": role_summaries,
    }

    prompt = f"""You are the AI Copilot for CoLab's Resource Management Group (RMG).

The deterministic recommendation engine has already decided WHO gets allocated to which role.
Your job is ONLY to explain those decisions in clear business language.
Do NOT suggest different people. Do NOT change any scores or plans.

Here is the complete staffing plan produced by the engine:

```json
{json.dumps(plan_summary, indent=2)}
```

Please produce the following outputs in this exact JSON structure:
{{
  "executive_summary": "2-3 sentence summary of the overall staffing plan for senior leadership.",
  "recommendation_rationale": "Explain why the engine selected these specific candidates for each role. Reference skill match, availability, COE alignment, and geo routing where relevant.",
  "alternative_rejection_summary": "Explain why alternatives (swaps, waits, hires) were or were not chosen. Be specific about trade-offs.",
  "business_impact_narrative": "Describe the business impact: revenue contribution, project health effects, and delivery risks if any swap chains are executed.",
  "swap_chain_explanation": "In plain English, explain any swap chains — who moves where, who replaces them, and why this is safe. If no swaps, say so.",
  "hiring_justification": "If any hire signals were raised, explain the business case: which tier, why no internal match existed, urgency level. If no hires needed, say so.",
  "risks_and_assumptions": "List 3-5 key risks and assumptions in the plan. Include: SOW status, health of source projects, replacement confidence, and any single points of failure.",
  "rm_action_notes": "Concise bullet-point action list for the Resource Manager to execute today. Max 6 bullets.",
  "plan_comparison_table": "A plain-text table comparing all staffing options considered (Plan A/B/C/D) with columns: Role | Plan | Employee | Score | Delay | Complexity | Notes."
}}

Respond ONLY with valid JSON. No preamble, no markdown fences.
"""
    return prompt


# ─────────────────────────────────────────────────────────────────────────────
# FALLBACK TEXT GENERATOR (when LLM is unavailable)
# ─────────────────────────────────────────────────────────────────────────────

def _build_fallback(plan: ProjectStaffingPlan) -> LLMEnrichedOutput:
    """Generate rule-based text summaries without an LLM."""

    # Executive summary
    exec_summary = (
        f"Staffing plan for {plan.client} ({plan.client_priority} client, "
        f"{plan.request_priority} priority). "
        f"{plan.roles_filled_immediate} of {plan.total_roles} roles can be filled immediately. "
        f"Coverage: {plan.coverage_pct:.0f}%. "
        f"Overall complexity: {plan.overall_complexity}."
    )

    # Rationale
    rationale_parts = []
    for rp in plan.role_plans:
        opt = rp.recommended_option
        if opt and opt.recommended_employee_id:
            rationale_parts.append(
                f"• {rp.role_name}: {opt.recommended_employee_id} "
                f"(score {opt.composite_score:.2f}, {opt.confidence_band} confidence, "
                f"{opt.plan_label})."
            )
        elif rp.hire_signal:
            role_display = rp.required_role if hasattr(rp, "required_role") else f"Tier {rp.seniority_tier}"
            rationale_parts.append(
                f"• {rp.role_name}: No internal candidate — hire required "
                f"({role_display}, urgency {rp.hire_urgency})."
            )
        else:
            rationale_parts.append(f"• {rp.role_name}: No feasible option found.")
    rationale = "\n".join(rationale_parts)

    # Swap explanation
    swap_parts = []
    for rp in plan.role_plans:
        opt = rp.recommended_option
        if opt and opt.plan_type == "B_SWAP" and opt.swap_chain_summary:
            swap_parts.extend(opt.swap_chain_summary)
    swap_explanation = "\n".join(swap_parts) if swap_parts else "No swaps required."

    # Hire justification
    hire_parts = []
    for rp in plan.role_plans:
        if rp.hire_signal:
            role_display = rp.required_role if hasattr(rp, "required_role") else f"Tier {rp.seniority_tier}"
            hire_parts.append(
                f"• {role_display} ({rp.role_name}): "
                f"{rp.hire_urgency} — {rp.gap_reason or 'No internal match found.'}"
            )
    hire_justification = (
        "\n".join(hire_parts) if hire_parts
        else "No external hiring required — all roles can be filled internally."
    )

    # Risks
    risks = []
    if not plan.sow_signed:
        risks.append("SOW is not yet signed — allocations should not be finalised until SOW is confirmed.")
    for rp in plan.role_plans:
        opt = rp.recommended_option
        if opt and opt.plan_type == "B_SWAP":
            risks.append(
                f"Swap chain for {rp.role_name} has complexity {opt.implementation_complexity} — "
                f"manager approval required."
            )
    if plan.roles_needing_hire > 0:
        risks.append(
            f"{plan.roles_needing_hire} role(s) require external hire — "
            f"this introduces a delivery risk if hiring takes longer than expected."
        )
    if plan.extend_start_date_recommended:
        risks.append(
            f"Start date extension recommended to {plan.recommended_start_date} "
            f"to allow key candidates to become available."
        )
    risks.append("Utilisation assumptions are based on 8-week timesheet lookback and may not reflect very recent changes.")
    risks_text = "\n".join(f"• {r}" for r in risks[:5]) if risks else "• No major risks identified."

    # Plan comparison table
    header = f"{'Role':<30} {'Plan':<15} {'Employee':<12} {'Score':<7} {'Delay':<7} {'Complexity':<12} Notes"
    sep = "-" * len(header)
    rows = [header, sep]
    for rp in plan.role_plans:
        opt = rp.recommended_option
        if opt:
            rows.append(
                f"{rp.role_name[:29]:<30} "
                f"{opt.plan_type[:14]:<15} "
                f"{(opt.recommended_employee_id or 'HIRE')[:11]:<12} "
                f"{opt.composite_score:<7.2f} "
                f"{opt.estimated_delay_days:<7} "
                f"{opt.implementation_complexity:<12} "
                f"{opt.business_impact_summary[:60]}"
            )
        else:
            rows.append(
                f"{rp.role_name[:29]:<30} {'NO_OPTION':<15} {'—':<12} "
                f"{'0.00':<7} {'—':<7} {'—':<12} {rp.gap_reason or ''}"
            )
    table = "\n".join(rows)

    return LLMEnrichedOutput(
        pipeline_id=plan.pipeline_id,
        client=plan.client,
        executive_summary=exec_summary,
        recommendation_rationale=rationale,
        alternative_rejection_summary="LLM unavailable — see plan options above.",
        business_impact_narrative="\n".join(
            opt.business_impact_summary
            for rp in plan.role_plans
            if (opt := rp.recommended_option) and opt.business_impact_summary
        ),
        swap_chain_explanation=swap_explanation,
        hiring_justification=hire_justification,
        risks_and_assumptions=risks_text,
        rm_action_notes="\n".join(plan.implementation_sequence),
        plan_comparison_table=table,
        is_fallback=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 8 — LLM ENRICHMENT
# ─────────────────────────────────────────────────────────────────────────────

import threading
from collections import defaultdict
from typing import Any, Dict

# Global locks to prevent concurrent requests to the same model
MODEL_LOCKS = defaultdict(threading.Lock)

def _extract_json(raw: str) -> Dict[str, Any]:
    """Robust helper to extract JSON content even with thinking tags or conversational text around it."""
    # Strip thinking tags if present
    if "<think>" in raw and "</think>" in raw:
        parts = raw.split("</think>")
        raw = parts[-1].strip()

    # Try direct parse
    try:
        return json.loads(raw)
    except Exception:
        pass

    # Try finding the first '{' and last '}'
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            return json.loads(raw[start:end+1])
    except Exception:
        pass

    raise ValueError("Response does not contain valid JSON content.")


class LLMIntelligence:

    def __init__(self, cfg: EngineConfig = DEFAULT_CONFIG):
        self.cfg = cfg

    def _get_client_instance(self, api_base: str, api_key: str):
        """Lazy-load and construct OpenAI compatible client instance."""
        try:
            import httpx
            from openai import OpenAI
            return OpenAI(
                base_url=api_base,
                api_key=api_key,
                http_client=httpx.Client(),
            )
        except ImportError:
            return None

    def enrich(self, plan: ProjectStaffingPlan) -> LLMEnrichedOutput:
        """
        Send the plan to the LLM and return enriched outputs.

        Attempts up to 4 model stages before falling back to rule-based output:
          Stage 1: Configured model (LLM_MODEL / cfg.llm.model)
          Stage 2: nvidia/nemotron-3-ultra-550b-a55b:free  (OpenRouter — skipped if no key)
          Stage 3: nvidia/nemotron-3-super-120b-a12b:free  (OpenRouter — skipped if no key)
          Stage 4: Local Ollama (OLLAMA_MODEL / cfg.llm.model via OLLAMA_BASE_URL)
          Stage 5: Deterministic rule-based summary

        Concurrent requests to the same model are serialised with per-model locks.
        """
        do_stage8 = os.getenv("STAGE8_LLM", "False").strip().lower() in ("true", "1", "yes")
        if not do_stage8:
            return _build_fallback(plan)

        OPENROUTER_BASE = "https://openrouter.ai/api/v1"

        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        primary_model   = os.getenv("LLM_MODEL") or self.cfg.llm.model
        ollama_base     = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip()
        ollama_model    = os.getenv("OLLAMA_MODEL", "").strip() or self.cfg.llm.model

        # Determine Stage 1 routing: use OpenRouter if key is present or model is a free-tier one.
        stage1_is_openrouter = bool(openrouter_key) or ":free" in primary_model
        stage1_base = OPENROUTER_BASE if stage1_is_openrouter else self.cfg.llm.api_base
        stage1_key  = openrouter_key if stage1_is_openrouter else (openrouter_key or "ollama")

        # Build ordered list of (label, model, base_url, api_key)
        # Stages 2 & 3 are only added when an OpenRouter key is available.
        stages: List[tuple] = [
            ("Stage 1", primary_model, stage1_base, stage1_key),
        ]
        if openrouter_key:
            stages.append(("Stage 2", "nvidia/nemotron-3-ultra-550b-a55b:free", OPENROUTER_BASE, openrouter_key))
            stages.append(("Stage 3", "nvidia/nemotron-3-super-120b-a12b:free", OPENROUTER_BASE, openrouter_key))
        else:
            print("[LLM] OPENROUTER_API_KEY not set — skipping Stage 2 and Stage 3.")

        # Stage 4 — Local Ollama (no API key required)
        stages.append(("Stage 4", ollama_model, ollama_base, "ollama"))

        prompt = _build_prompt(plan)

        for label, model, base_url, api_key in stages:
            # Acquire lock to serialise concurrent requests to the same model
            lock = MODEL_LOCKS[model]
            with lock:
                print(f"[LLM] Attempting {label} ({model}) via {base_url}...")
                client = self._get_client_instance(base_url, api_key)
                if not client:
                    print(f"[LLM] {label}: client initialisation failed for {model}.")
                    continue

                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=self.cfg.llm.max_tokens,
                        temperature=self.cfg.llm.temperature,
                        timeout=self.cfg.llm.timeout_seconds,
                        extra_headers={
                            "HTTP-Referer": "https://colab.internal",
                            "X-Title": "CoLab RMG Copilot",
                        },
                    )
                    raw = response.choices[0].message.content.strip()

                    # Strip markdown fences if present
                    if raw.startswith("```"):
                        raw = "\n".join(raw.split("\n")[1:])
                    if raw.endswith("```"):
                        raw = "\n".join(raw.split("\n")[:-1])

                    data = _extract_json(raw)

                    print(f"[LLM] {label} succeeded.")
                    return LLMEnrichedOutput(
                        pipeline_id=plan.pipeline_id,
                        client=plan.client,
                        executive_summary=data.get("executive_summary", ""),
                        recommendation_rationale=data.get("recommendation_rationale", ""),
                        alternative_rejection_summary=data.get("alternative_rejection_summary", ""),
                        business_impact_narrative=data.get("business_impact_narrative", ""),
                        swap_chain_explanation=data.get("swap_chain_explanation", ""),
                        hiring_justification=data.get("hiring_justification", ""),
                        risks_and_assumptions=data.get("risks_and_assumptions", ""),
                        rm_action_notes=data.get("rm_action_notes", ""),
                        plan_comparison_table=data.get("plan_comparison_table", ""),
                        is_fallback=False,
                    )
                except Exception as e:
                    print(f"[LLM] {label} ({model}) failed: {type(e).__name__}: {e}")

        print("[LLM] All stages failed (Stage 1–4). Using Stage 5: rule-based fallback.")
        return _build_fallback(plan)
