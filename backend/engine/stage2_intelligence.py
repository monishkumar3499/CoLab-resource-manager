"""
stage2_intelligence.py — Candidate Intelligence Engine

Takes raw retrieval results (Stage 1) and enriches each candidate
with a complete business profile drawn from all four datacubes.

Output: EnrichedCandidate — a rich business object passed to all downstream stages.
No scoring or filtering here. Pure enrichment and signal extraction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG
from .loader import DataStore
from .stage1_retrieval import CandidateMatch, RetrievalResult
from .stage2_inference import CapabilityInferenceEngine, InferredCapability

TODAY = pd.Timestamp(date.today())


# ─────────────────────────────────────────────────────────────────────────────
# ENRICHED CANDIDATE DATA CLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CurrentProjectContext:
    project_id: str
    health_score: float
    extension_risk: float
    ramp_down_signal: float
    schedule_status: str
    days_until_end: float
    team_size: int
    is_safe_to_pull: bool           # engine verdict on whether pulling is safe
    pull_risk_reason: str           # human-readable reason if not safe


@dataclass
class EnrichedCandidate:
    # ── Identity ──────────────────────────────────────────────────────────────
    employee_id: str
    job_name: str = ""
    role: str = ""
    location: str = ""
    geo_cluster: str = ""
    seniority_tier: int = 3
    tenure_years: float = 0.0
    department: str = ""

    # ── Retrieval signal ─────────────────────────────────────────────────────
    semantic_score: float = 0.0
    embedding_distance: float = 0.0
    matched_skills: List[str] = field(default_factory=list)
    matched_competencies: List[str] = field(default_factory=list)

    # ── Skill signals ────────────────────────────────────────────────────────
    primary_coe: str = ""
    all_coes: List[str] = field(default_factory=list)
    avg_skill_score: float = 0.0
    max_skill_score: float = 0.0
    avg_exp_years: float = 0.0
    skill_breadth: int = 0
    top_skills: List[Dict] = field(default_factory=list)          # [{Skill, SubSkill, Score, exp_years}]
    skill_text: str = ""
    has_skill_data: bool = False
    skill_confidence: float = 0.0         # 0-1 derived from skill coverage + scores

    # ── Competency signals ───────────────────────────────────────────────────
    avg_competency_score: float = 0.0
    competency_profile: Dict[str, int] = field(default_factory=dict)
    has_competency_data: bool = False
    competency_confidence: float = 0.0    # 0-1

    # ── Availability signals ─────────────────────────────────────────────────
    util_pct: float = 0.0
    available_capacity_pct: float = 0.0
    effective_availability: float = 0.0
    is_fully_available: bool = False
    is_partially_available: bool = False
    is_busy: bool = False
    soon_free: bool = False
    ramp_down_flag: bool = False
    ramp_down_bonus: float = 0.0
    days_to_soonest_end: float = 0.0

    # ── Project health signals ───────────────────────────────────────────────
    active_project_ids: List[str] = field(default_factory=list)
    avg_project_health: float = 0.0
    on_red_project: bool = False
    on_amber_project: bool = False
    health_penalty: float = 0.0
    current_project_contexts: List[CurrentProjectContext] = field(default_factory=list)

    # ── Swap / mobility signals ──────────────────────────────────────────────
    swap_eligible: bool = False
    is_bau_only: bool = False

    # ── Experience / history signals ─────────────────────────────────────────
    client_history: List[str] = field(default_factory=list)
    similar_project_count: int = 0      # count of past projects matching pipeline COE
    domain_experience_score: float = 0.0  # 0-1 derived from COE match + past projects
    client_experience_score: float = 0.0  # 0-1: has worked with this pipeline client before

    # ── Predicted availability ───────────────────────────────────────────────
    predicted_available_date: Optional[str] = None  # ISO date string or None
    predicted_available_pct: float = 0.0           # capacity expected to free up

    # ── Business impact estimate ────────────────────────────────────────────
    daily_rate_estimate: float = 0.0       # from config tier-based rate table
    potential_revenue_impact: float = 0.0  # if not allocated: lost revenue per week

    # ── Role match metadata ──────────────────────────────────────────────────
    role_name: str = ""
    required_role: str = ""
    required_tier: int = 0
    required_allocation_pct: float = 0.0
    tier_delta: int = 0                  # abs(candidate_tier - required_tier)
    capacity_gap_pct: float = 0.0          # required - available (negative = surplus)

    # ── Capability inference metadata ────────────────────────────────────────
    # Populated only when has_skill_data=False and inference was run
    inference_metadata: Optional[InferredCapability] = None


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL COMPUTATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _skill_confidence(person: Dict, top_skills: List[Dict]) -> float:
    """
    Skill confidence: 0-1.
    Based on:
      - avg_skill_score (0-5 normalised)
      - skill_breadth (diversity of skills)
      - max_skill_score (depth in at least one area)
    """
    avg = float(person.get("avg_skill_score") or 0) / 5.0
    breadth = min(1.0, int(person.get("skill_breadth") or 0) / 20.0)
    depth = float(person.get("max_skill_score") or 0) / 5.0
    return round(0.50 * avg + 0.25 * breadth + 0.25 * depth, 4)


def _competency_confidence(person: Dict) -> float:
    """Competency confidence: 0-1 from avg_competency_score / 5."""
    val = float(person.get("avg_competency_score") or 0)
    if val == 0:
        return 0.5  # neutral when no data
    return round(min(1.0, val / 5.0), 4)


def _domain_experience(person: Dict, pipeline_coe: Optional[str]) -> float:
    """
    Domain experience: 0-1.
    1.0 if primary_coe matches pipeline tech COE.
    0.6 if pipeline COE is in all_coes.
    0.3 otherwise (some transferable experience assumed).
    """
    primary = str(person.get("primary_coe") or "").strip().lower()
    all_coes = [c.lower() for c in (person.get("all_coes") or [])]
    if not pipeline_coe:
        return 0.5
    target = pipeline_coe.lower()
    if primary == target:
        return 1.0
    # Partial match (e.g. "data science" in "Data Science & AI")
    if any(target in c or c in target for c in all_coes):
        return 0.7
    if any(target in c or c in target for c in [primary]):
        return 0.8
    return 0.3


def _client_experience(person: Dict, pipeline_client_id: Optional[str]) -> float:
    """1.0 if employee has billed to this client before, 0.0 otherwise."""
    if not pipeline_client_id:
        return 0.0
    history = person.get("client_history") or []
    return 1.0 if pipeline_client_id in history else 0.0


def _similar_project_count(person: Dict, projects_df: pd.DataFrame, pipeline_coe: Optional[str]) -> int:
    """Count of past projects the employee worked on with matching tech_coe."""
    proj_ids = person.get("active_project_ids") or []
    if not proj_ids or not pipeline_coe:
        return 0
    target = pipeline_coe.lower()
    count = 0
    for pid in proj_ids:
        if pid in projects_df.index:
            p_coe = str(projects_df.loc[pid, "tech_coe"]).lower()
            if target in p_coe or p_coe in target:
                count += 1
    return count


def _predicted_availability(person: Dict, required_pct: float) -> Tuple[Optional[str], float]:
    """
    Predict when this employee will have the required capacity free.
    Returns (date_string_or_None, predicted_free_pct).
    """
    avail = float(person.get("effective_availability") or 0)
    if avail >= required_pct:
        return None, avail  # available now

    soonest_end = person.get("soonest_end_date")
    days = person.get("days_to_soonest_end")
    if soonest_end is not None and not pd.isna(soonest_end):
        # After project ends, employee could be up to 100% free
        try:
            end_str = pd.Timestamp(soonest_end).strftime("%Y-%m-%d")
            return end_str, min(100.0, avail + 50.0)  # conservative estimate
        except Exception:
            pass
    return None, avail


def _build_project_context(project_id: str, ds: DataStore, cfg: EngineConfig) -> CurrentProjectContext:
    """Build a CurrentProjectContext for one active project."""
    p = ds.get_project(project_id)
    if not p:
        return CurrentProjectContext(
            project_id=project_id,
            health_score=0.65, extension_risk=0.0, ramp_down_signal=0.0,
            schedule_status="NO_COLOR", days_until_end=float("nan"),
            team_size=0, is_safe_to_pull=True, pull_risk_reason=""
        )

    health = float(p.get("health_score") or 0.65)
    ext_risk = float(p.get("extension_risk") or 0.0)
    ramp = float(p.get("ramp_down_signal") or 0.0)
    schedule = str(p.get("latest_schedule") or "NO_COLOR")
    days_end = float(p.get("days_until_end") or float("nan"))
    team_size = int(p.get("total_slots") or 0)

    # Safe-to-pull logic
    safe = True
    reason = ""
    if ext_risk >= cfg.rules.extension_risk_no_pull:
        safe = False
        reason = f"Extension risk {ext_risk:.0%} — project likely to extend"
    elif health < cfg.impact.critical_health_floor:
        safe = False
        reason = f"Project health {health:.2f} is critical — pulling would destabilise delivery"
    elif schedule == "RED" and team_size <= 2:
        safe = False
        reason = "Project schedule is RED and team is small — cannot afford a departure"
    elif ramp >= 0.4:
        safe = True
        reason = f"Project is ramping down (signal {ramp:.0%}) — safe to release"

    return CurrentProjectContext(
        project_id=project_id,
        health_score=health,
        extension_risk=ext_risk,
        ramp_down_signal=ramp,
        schedule_status=schedule,
        days_until_end=days_end,
        team_size=team_size,
        is_safe_to_pull=safe,
        pull_risk_reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2 — INTELLIGENCE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class CandidateIntelligenceEngine:
    """
    Stage 2: Enrich every raw candidate with full business context.
    """

    def __init__(self, ds: DataStore, cfg: EngineConfig = DEFAULT_CONFIG):
        self.ds = ds
        self.cfg = cfg
        self._inference_engine = CapabilityInferenceEngine(ds, cfg)

    def enrich(
        self,
        retrieval: RetrievalResult,
        pipeline_project: pd.Series,
    ) -> List[EnrichedCandidate]:
        """
        Enrich all candidates in a retrieval result.
        Returns a list of EnrichedCandidate objects.
        """
        enriched = []
        pipeline_coe = str(pipeline_project.get("solution") or "").strip()
        pipeline_client = str(pipeline_project.get("client") or "")
        # Derive a CLIENT_ID approximation from the client name
        # The client history uses CLIENT_XXX IDs, but pipeline uses names.
        # We do a name-based match (imperfect — close enough for scoring).
        pipeline_client_upper = pipeline_client.upper().replace(" ", "_")

        for match in retrieval.candidates:
            eid = match.employee_id
            person = self.ds.get_person(eid)
            if not person:
                continue

            # ── Parse stored JSON fields ──────────────────────────────────────
            top_skills = person.get("top_skills") or []
            if isinstance(top_skills, str):
                try:
                    top_skills = json.loads(top_skills)
                except Exception:
                    top_skills = []

            comp_profile = person.get("competency_profile") or {}
            if isinstance(comp_profile, str):
                try:
                    comp_profile = json.loads(comp_profile)
                except Exception:
                    comp_profile = {}

            all_coes = person.get("all_coes") or []
            if isinstance(all_coes, str):
                try:
                    all_coes = json.loads(all_coes)
                except Exception:
                    all_coes = []

            client_history = person.get("client_history") or []
            if isinstance(client_history, str):
                try:
                    client_history = json.loads(client_history)
                except Exception:
                    client_history = []

            active_proj_ids = person.get("active_project_ids") or []
            if isinstance(active_proj_ids, str):
                try:
                    active_proj_ids = json.loads(active_proj_ids)
                except Exception:
                    active_proj_ids = []

            # ── Build project contexts ────────────────────────────────────────
            proj_contexts = [
                _build_project_context(pid, self.ds, self.cfg)
                for pid in active_proj_ids
            ]

            # ── Derived signals ───────────────────────────────────────────
            has_skill_data = float(person.get("avg_skill_score") or 0) > 0
            skill_conf_raw = _skill_confidence(person, top_skills)

            # Run capability inference when explicit skill data is missing
            inferred_cap: Optional[InferredCapability] = None
            if not has_skill_data:
                from .config import standardize_role as _std
                _role_std = _std(str(person.get("job_name") or ""))
                _all_coes_inf = all_coes if isinstance(all_coes, list) else []
                _active_pids = active_proj_ids if isinstance(active_proj_ids, list) else []
                inferred_cap = self._inference_engine.infer(
                    employee_id=eid,
                    role_std=_role_std,
                    primary_coe=str(person.get("primary_coe") or "Unknown"),
                    all_coes=_all_coes_inf,
                    active_project_ids=_active_pids,
                    semantic_score=match.semantic_score,
                    pipeline_solution=pipeline_coe,
                )
                skill_conf = inferred_cap.confidence if inferred_cap else skill_conf_raw
            else:
                skill_conf = skill_conf_raw

            comp_conf = _competency_confidence(person)
            domain_exp = _domain_experience(person, pipeline_coe)
            client_exp = _client_experience(person, pipeline_client_upper)
            sim_proj_count = _similar_project_count(person, self.ds.projects, pipeline_coe)
            pred_date, pred_pct = _predicted_availability(
                person, retrieval.allocation_pct
            )

            from .config import standardize_role
            cand_role = standardize_role(str(person.get("job_name") or ""))
            req_role = standardize_role(retrieval.required_role)

            ROLE_TO_TIER = {
                "AP": 7, "P": 6, "TA": 5, "M": 5, "SC": 4, "C": 3, "AC": 2, "SSE": 3, "SE": 2, "ASE": 1, "DS": 4
            }
            tier = ROLE_TO_TIER.get(cand_role, int(person.get("seniority_tier") or 3))
            required_tier = ROLE_TO_TIER.get(req_role, 3)
            avail = float(person.get("effective_availability") or 0)
            daily_rate = self.cfg.impact.daily_rate_by_role.get(cand_role, 600)

            enriched.append(EnrichedCandidate(
                # Identity
                employee_id=eid,
                job_name=str(person.get("job_name") or ""),
                role=cand_role,
                location=str(person.get("location") or ""),
                geo_cluster=str(person.get("geo_cluster") or "Unknown"),
                seniority_tier=tier,
                tenure_years=float(person.get("tenure_years") or 0),
                department=str(person.get("department_name") or ""),
                # Retrieval
                semantic_score=match.semantic_score,
                embedding_distance=match.embedding_distance,
                matched_skills=match.matched_skills,
                matched_competencies=match.matched_competencies,
                # Skills
                primary_coe=str(person.get("primary_coe") or "Unknown"),
                all_coes=all_coes,
                avg_skill_score=float(person.get("avg_skill_score") or 0),
                max_skill_score=float(person.get("max_skill_score") or 0),
                avg_exp_years=float(person.get("avg_exp_years") or 0),
                skill_breadth=int(person.get("skill_breadth") or 0),
                top_skills=top_skills,
                skill_text=str(person.get("skill_text") or ""),
                has_skill_data=bool(person.get("has_skill_data", False)),
                skill_confidence=skill_conf,
                # Competency
                avg_competency_score=float(person.get("avg_competency_score") or 0),
                competency_profile=comp_profile,
                has_competency_data=bool(person.get("has_competency_data", False)),
                competency_confidence=comp_conf,
                # Availability
                util_pct=float(person.get("util_pct") or 0),
                available_capacity_pct=float(person.get("available_capacity_pct") or 0),
                effective_availability=avail,
                is_fully_available=bool(person.get("is_fully_available", False)),
                is_partially_available=bool(person.get("is_partially_available", False)),
                is_busy=bool(person.get("is_busy", False)),
                soon_free=bool(person.get("soon_free", False)),
                ramp_down_flag=bool(person.get("ramp_down_flag", False)),
                ramp_down_bonus=float(person.get("ramp_down_bonus") or 0),
                days_to_soonest_end=float(person.get("days_to_soonest_end") or float("nan")),
                # Project health
                active_project_ids=active_proj_ids,
                avg_project_health=float(person.get("avg_project_health") or 0.65),
                on_red_project=bool(person.get("on_red_project", False)),
                on_amber_project=bool(person.get("on_amber_project", False)),
                health_penalty=float(person.get("health_penalty") or 0),
                current_project_contexts=proj_contexts,
                # Swap
                swap_eligible=bool(person.get("swap_eligible", True)),
                is_bau_only=bool(person.get("is_bau_only", False)),
                # Experience
                client_history=client_history,
                similar_project_count=sim_proj_count,
                domain_experience_score=domain_exp,
                client_experience_score=client_exp,
                # Predicted availability
                predicted_available_date=pred_date,
                predicted_available_pct=pred_pct,
                # Business impact
                daily_rate_estimate=daily_rate,
                potential_revenue_impact=daily_rate * 5,  # per week
                # Role match
                role_name=retrieval.role_name,
                # Inference metadata
                inference_metadata=inferred_cap,
                required_role=retrieval.required_role,
                required_tier=required_tier,
                required_allocation_pct=retrieval.allocation_pct,
                tier_delta=abs(tier - required_tier),
                capacity_gap_pct=retrieval.allocation_pct - avail,
            ))

        return enriched

    def enrich_all(
        self,
        retrieval_results: List[RetrievalResult],
        pipeline_project: pd.Series,
    ) -> Dict[str, List[EnrichedCandidate]]:
        """
        Enrich all retrieval results for a pipeline project.
        Returns {role_id: [EnrichedCandidate, ...]}
        """
        result = {}
        for rr in retrieval_results:
            enriched = self.enrich(rr, pipeline_project)
            result[rr.role_id] = enriched
        return result
