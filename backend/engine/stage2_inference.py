"""
stage2_inference.py — Technical Capability Inference Engine

When an employee has no explicit skill data (has_skill_data=False),
this engine infers their technical capability using a deterministic
evidence hierarchy:

  Priority 1 — Project technology history
  Priority 2 — Current role
  Priority 3 — Primary COE
  Priority 4 — Role compatibility graph (borrow from similar roles)
  Priority 5 — Semantic similarity score (last fallback)

Every inferred capability carries:
  - confidence (0.0 – 0.72, capped below HIGH band to never outrank explicit data)
  - evidence_source (string enum)
  - reason (human-readable explanation for RM)
  - is_inferred (always True)

The inferred skill_confidence replaces the raw 0.0 only when
has_skill_data is False. It never overrides explicit data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import EngineConfig, DEFAULT_CONFIG

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Cap on any inferred confidence — inferred data must not outrank explicit skill data
# (explicit HIGH band starts at 0.72; inferred is capped just below)
INFERRED_CONFIDENCE_CAP = 0.68

# COE → expected skill keywords (mirrors stage1_retrieval.COE_SKILL_HINTS)
COE_SKILL_MAP: Dict[str, List[str]] = {
    "Data Engineering":        ["spark", "sql", "etl", "dbt", "databricks", "data warehouse", "pipeline"],
    "Data Science & AI":       ["python", "machine learning", "ai", "nlp", "statistics", "deep learning", "modelling"],
    "Full Stack":               ["react", "nodejs", "typescript", "api", "microservices", "backend", "frontend"],
    "TechOps & Automation":    ["devops", "kubernetes", "ci/cd", "monitoring", "automation", "infrastructure", "cloud"],
    "Power BI & Consulting":   ["power bi", "reporting", "dashboards", "analytics", "consulting", "stakeholder"],
    "Consulting":               ["business analysis", "consulting", "advisory", "stakeholder management", "strategy"],
}

# Role → expected skill keywords (mirrors stage1_retrieval.ROLE_SKILL_HINTS)
ROLE_SKILL_MAP: Dict[str, List[str]] = {
    "AP":  ["engagement management", "client leadership", "strategy", "commercial"],
    "P":   ["architecture", "technical leadership", "design", "strategy"],
    "TA":  ["solutions architecture", "design", "gtm", "strategy"],
    "M":   ["delivery oversight", "client engagement", "advisory", "leadership"],
    "AC":  ["analysis", "advisory", "development", "consulting"],
    "C":   ["consulting", "delivery", "client engagement", "advisory"],
    "SC":  ["solutions consulting", "architecture", "advisory"],
    "SSE": ["software engineering", "development", "implementation", "technical delivery"],
    "SE":  ["development", "coding", "programming", "implementation"],
    "ASE": ["development", "coding", "solutions enabler"],
    "DS":  ["machine learning", "python", "data science", "statistics", "ai"],
}

# Minimum inferred confidence per evidence source
EVIDENCE_CONFIDENCE: Dict[str, float] = {
    "project_tech_history": 0.62,  # strongest — direct evidence of delivery
    "current_role":          0.52,  # role implies a standard skill set
    "primary_coe":           0.47,  # COE membership implies domain knowledge
    "role_compatibility":    0.40,  # adjacent role with similar skill set
    "semantic_similarity":   0.35,  # weakest — language similarity only
}


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT DATA CLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InferredCapability:
    """Result of capability inference for one employee."""
    employee_id: str
    is_inferred: bool                  # always True when returned by this engine
    confidence: float                  # 0.0 – INFERRED_CONFIDENCE_CAP
    evidence_source: str               # one of EVIDENCE_CONFIDENCE keys
    reason: str                        # human-readable explanation
    inferred_skills: List[str]         # skill keywords implied by evidence
    # Secondary signals (never override primary confidence)
    semantic_boost: float              # additive boost from non-zero semantic score
    coe_confirmed: bool                # COE corroborates the inferred skill set


# ─────────────────────────────────────────────────────────────────────────────
# INFERENCE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class CapabilityInferenceEngine:
    """
    Infers technical capability for employees with missing explicit skill data.

    Usage:
        engine = CapabilityInferenceEngine(ds, cfg)
        ic = engine.infer(employee_id, role_std, primary_coe, active_project_ids,
                          semantic_score, pipeline_solution)
        if ic:
            enriched.skill_confidence = ic.confidence
            enriched.inference_metadata = ic
    """

    def __init__(self, ds, cfg: EngineConfig = DEFAULT_CONFIG):
        self.ds = ds
        self.cfg = cfg

    def infer(
        self,
        employee_id: str,
        role_std: str,
        primary_coe: str,
        all_coes: List[str],
        active_project_ids: List[str],
        semantic_score: float,
        pipeline_solution: str,
    ) -> Optional[InferredCapability]:
        """
        Run the full evidence hierarchy. Returns InferredCapability or None
        if no evidence is found (very rare — role at minimum always provides level 2).
        """

        # ── Priority 1: Project technology history ────────────────────────────
        ic = self._from_project_history(
            employee_id, active_project_ids, pipeline_solution, semantic_score
        )
        if ic:
            return ic

        # ── Priority 2: Current role ──────────────────────────────────────────
        ic = self._from_role(employee_id, role_std, semantic_score, primary_coe, pipeline_solution)
        if ic:
            return ic

        # ── Priority 3: Primary COE ───────────────────────────────────────────
        ic = self._from_coe(employee_id, primary_coe, all_coes, semantic_score, pipeline_solution)
        if ic:
            return ic

        # ── Priority 4: Role compatibility (adjacent roles with known skills) ──
        ic = self._from_role_compatibility(employee_id, role_std, semantic_score)
        if ic:
            return ic

        # ── Priority 5: Semantic similarity only ─────────────────────────────
        if semantic_score > 0.0:
            boost = min(0.10, semantic_score * 0.15)
            base = EVIDENCE_CONFIDENCE["semantic_similarity"]
            conf = min(INFERRED_CONFIDENCE_CAP, base + boost)
            return InferredCapability(
                employee_id=employee_id,
                is_inferred=True,
                confidence=round(conf, 4),
                evidence_source="semantic_similarity",
                reason=(
                    f"No explicit skill data. Semantic similarity score {semantic_score:.2f} "
                    f"suggests relevant background for this role query. Low confidence inference."
                ),
                inferred_skills=[],
                semantic_boost=boost,
                coe_confirmed=False,
            )

        # No evidence at all — return a minimal floor (not zero; zero blocks ranking entirely)
        return InferredCapability(
            employee_id=employee_id,
            is_inferred=True,
            confidence=0.25,
            evidence_source="no_evidence",
            reason="No skill data, no project history, no semantic match. Minimum floor confidence applied.",
            inferred_skills=[],
            semantic_boost=0.0,
            coe_confirmed=False,
        )

    # ── Evidence source implementations ──────────────────────────────────────

    def _from_project_history(
        self,
        employee_id: str,
        active_project_ids: List[str],
        pipeline_solution: str,
        semantic_score: float,
    ) -> Optional[InferredCapability]:
        """
        Priority 1: Employee's past project tech_coe matches the pipeline solution.
        The more matching projects, the higher the confidence.
        """
        if not active_project_ids or not pipeline_solution:
            return None

        solution_lower = pipeline_solution.lower()
        matching_projects = []
        inferred_skills: List[str] = []

        for pid in active_project_ids:
            project = self.ds.get_project(pid)
            if not project:
                continue
            tech_coe = str(project.get("tech_coe") or "").lower()
            if not tech_coe:
                continue
            # Match: pipeline solution keyword appears in project tech_coe or vice versa
            if solution_lower in tech_coe or any(
                word in tech_coe for word in solution_lower.split() if len(word) > 3
            ):
                matching_projects.append(pid)
                # Collect implied skills from the matching COE
                for coe_name, skills in COE_SKILL_MAP.items():
                    if coe_name.lower() in tech_coe or tech_coe in coe_name.lower():
                        inferred_skills.extend(skills)

        if not matching_projects:
            return None

        # Scale confidence with number of matching projects (plateau at 3+)
        project_count = len(matching_projects)
        base = EVIDENCE_CONFIDENCE["project_tech_history"]
        scale = min(1.0, 0.6 + project_count * 0.15)  # 1 proj=0.75x, 2=0.90x, 3+=1.0x
        semantic_boost = min(0.05, semantic_score * 0.08)
        conf = min(INFERRED_CONFIDENCE_CAP, base * scale + semantic_boost)

        return InferredCapability(
            employee_id=employee_id,
            is_inferred=True,
            confidence=round(conf, 4),
            evidence_source="project_tech_history",
            reason=(
                f"No explicit skill data. Inferred from {project_count} past project(s) with "
                f"matching technology COE '{pipeline_solution}' "
                f"(projects: {', '.join(matching_projects[:3])}). "
                f"Implied skills: {', '.join(sorted(set(inferred_skills))[:6]) or 'domain-general'}."
            ),
            inferred_skills=sorted(set(inferred_skills))[:10],
            semantic_boost=semantic_boost,
            coe_confirmed=True,
        )

    def _from_role(
        self,
        employee_id: str,
        role_std: str,
        semantic_score: float,
        primary_coe: str,
        pipeline_solution: str,
    ) -> Optional[InferredCapability]:
        """
        Priority 2: Current role implies a standard skill set.
        Every standardized role has a known expected skill domain.
        """
        skills = ROLE_SKILL_MAP.get(role_std)
        if not skills:
            return None

        coe_confirmed = False
        coe_boost = 0.0
        solution_lower = pipeline_solution.lower()
        coe_lower = primary_coe.lower()
        if solution_lower and (solution_lower in coe_lower or coe_lower in solution_lower):
            coe_confirmed = True
            coe_boost = 0.05

        semantic_boost = min(0.04, semantic_score * 0.07)
        base = EVIDENCE_CONFIDENCE["current_role"]
        conf = min(INFERRED_CONFIDENCE_CAP, base + coe_boost + semantic_boost)

        return InferredCapability(
            employee_id=employee_id,
            is_inferred=True,
            confidence=round(conf, 4),
            evidence_source="current_role",
            reason=(
                f"No explicit skill data. Role '{role_std}' implies standard skill set: "
                f"{', '.join(skills[:5])}."
                + (f" COE '{primary_coe}' corroborates domain alignment." if coe_confirmed else "")
            ),
            inferred_skills=skills[:8],
            semantic_boost=semantic_boost,
            coe_confirmed=coe_confirmed,
        )

    def _from_coe(
        self,
        employee_id: str,
        primary_coe: str,
        all_coes: List[str],
        semantic_score: float,
        pipeline_solution: str,
    ) -> Optional[InferredCapability]:
        """
        Priority 3: COE membership implies domain knowledge.
        """
        coe_to_check = [primary_coe] + [c for c in all_coes if c != primary_coe]
        solution_lower = pipeline_solution.lower()

        matched_coe = None
        inferred_skills: List[str] = []

        for coe in coe_to_check:
            coe_lower = coe.lower()
            # Direct COE → skills lookup
            for coe_name, skills in COE_SKILL_MAP.items():
                if coe_name.lower() in coe_lower or coe_lower in coe_name.lower():
                    if not matched_coe:
                        matched_coe = coe_name
                    inferred_skills.extend(skills)
                    break
            if matched_coe:
                break

        if not matched_coe:
            return None

        # Boost if COE actually matches the pipeline solution
        pipeline_coe_match = solution_lower and (
            solution_lower in matched_coe.lower() or matched_coe.lower() in solution_lower
        )
        domain_boost = 0.06 if pipeline_coe_match else 0.0
        semantic_boost = min(0.03, semantic_score * 0.06)
        base = EVIDENCE_CONFIDENCE["primary_coe"]
        conf = min(INFERRED_CONFIDENCE_CAP, base + domain_boost + semantic_boost)

        return InferredCapability(
            employee_id=employee_id,
            is_inferred=True,
            confidence=round(conf, 4),
            evidence_source="primary_coe",
            reason=(
                f"No explicit skill data. COE membership '{matched_coe}' implies domain knowledge: "
                f"{', '.join(sorted(set(inferred_skills))[:5])}."
                + (f" COE directly matches pipeline solution '{pipeline_solution}'." if pipeline_coe_match else "")
            ),
            inferred_skills=sorted(set(inferred_skills))[:8],
            semantic_boost=semantic_boost,
            coe_confirmed=pipeline_coe_match,
        )

    def _from_role_compatibility(
        self,
        employee_id: str,
        role_std: str,
        semantic_score: float,
    ) -> Optional[InferredCapability]:
        """
        Priority 4: Role compatibility graph — adjacent roles share similar skills.
        E.g. an SSE likely has SE-level skills.
        """
        compatible_roles = self.cfg.rules.role_compatibility.get(role_std, [])
        # Exclude the role itself; look at what compatible roles imply
        adjacent = [r for r in compatible_roles if r != role_std]
        if not adjacent:
            return None

        # Collect skills from the most similar adjacent role
        best_role = adjacent[0]
        skills = ROLE_SKILL_MAP.get(best_role, [])
        if not skills:
            return None

        semantic_boost = min(0.03, semantic_score * 0.05)
        base = EVIDENCE_CONFIDENCE["role_compatibility"]
        conf = min(INFERRED_CONFIDENCE_CAP, base + semantic_boost)

        return InferredCapability(
            employee_id=employee_id,
            is_inferred=True,
            confidence=round(conf, 4),
            evidence_source="role_compatibility",
            reason=(
                f"No explicit skill data. Role '{role_std}' is compatible with '{best_role}', "
                f"which implies related skills: {', '.join(skills[:5])}. Low-confidence inference."
            ),
            inferred_skills=skills[:6],
            semantic_boost=semantic_boost,
            coe_confirmed=False,
        )
