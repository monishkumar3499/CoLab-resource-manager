"""
stage7_plans.py — Recommendation Plan Generator

Instead of returning a single engineer, this stage generates multiple
executable staffing strategies per role slot, and then assembles them
into a complete project-level staffing plan.

Plans produced per role:
  Plan A — Immediate Allocation   (candidate available now)
  Plan B — Smart Swap             (candidate busy, replacement chain feasible)
  Plan C — Soft Commit / Wait     (candidate ramping down, start delay acceptable)
  Plan D — Hire New Resource      (no internal match; triggers hire signal)

Project-level output:
  - Overall staffing coverage %
  - Roles filled / partially filled / unfilled
  - Extend-start-date recommendation (if ≥50% roles are "wait" type)
  - Hire headcount summary by tier
  - Implementation complexity score
  - Recommended execution sequence
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG
from .loader import DataStore
from .stage4_swap import BackfillResult, SwapPlan
from .stage5_6_impact_ranking import RankedCandidate, ImpactEstimate
from .stage3_alloc_optimizer import OptimizationResult


# ────────────────────────────────────────────────────────────────────────────────
# RECOMMENDATION EXPLANATION
# ────────────────────────────────────────────────────────────────────────────────

@dataclass
class RecommendationExplanation:
    """
    Human-readable explanation for every staffing recommendation.
    Designed for Resource Managers who need to understand WHY a candidate was selected.
    Every field uses plain English with specific evidence, never generic labels.
    """
    headline: str                        # One-line summary: who, what level, why
    capability_summary: str              # Explicit skill data or inference evidence
    project_experience: str             # Matching past project experience
    client_experience: str              # Prior billing to this client
    domain_match: str                   # COE alignment with pipeline solution
    geo_coe_match: str                  # Geo cluster and COE routing verdict
    capacity_status: str                # Remaining capacity vs required
    business_rules_passed: List[str]    # Rules that this candidate satisfies
    business_rules_flagged: List[str]   # Soft warnings (not hard failures)
    project_impact: str                 # Source project health and client impact
    confidence_rationale: str           # Score breakdown in plain language
    is_inferred: bool                   # True if skill data was inferred, not explicit
    inference_source: Optional[str]     # e.g. "project_tech_history", None if explicit


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StaffingOption:
    """One concrete staffing option for a single role slot."""
    # ── Core plan type (6-level hierarchy) ────────────────────────────────────
    # A_EXACT        Level 1 — Exact Match
    # A_STRONG       Level 2 — Strong Partial Match
    # A_TRANSFERABLE Level 3 — Transferable Skills Match
    # A_AVAILABILITY Level 4 — Availability-Based Recommendation
    # E_EXTEND_START Level 5 — Extend Project Start Date
    # D_HIRE         Level 6 — Hire Recommendation (last resort)
    plan_type: str
    plan_label: str                       # Human-readable label
    match_level: int                      # 1–6 (hierarchy level)
    match_label: str                      # e.g. "Exact Match"
    recommended_employee_id: Optional[str]
    job_name: Optional[str]
    location: Optional[str]
    seniority_tier: Optional[int]
    role: Optional[str]                   # standard role code
    available_capacity_pct: float
    composite_score: float
    confidence_band: str
    swap_chain_summary: List[str]         # Plain-English description of each swap hop
    expected_start_date: Optional[str]
    estimated_delay_days: int
    implementation_complexity: str        # LOW | MEDIUM | HIGH
    business_impact_summary: str
    revenue_contribution_weekly: float
    hire_tier_needed: Optional[int]
    hire_role_needed: Optional[str]       # standard role code
    score_breakdown: Dict[str, float]
    # ── Level 5 extension fields ──────────────────────────────────────────────
    extend_start_suggested_date: Optional[str] = None   # ISO date for revised project start
    extend_start_delay_weeks: Optional[float] = None    # how many weeks to delay
    extend_start_reason: Optional[str] = None           # human-readable explanation
    # ── Explainability ────────────────────────────────────────────────────────────
    explanation: Optional[RecommendationExplanation] = None
    redistribution_result: Optional[OptimizationResult] = None


@dataclass
class RoleStaffingPlan:
    """All options for a single role slot, plus the recommended choice."""
    role_id: str
    role_name: str
    seniority_tier: int
    required_role: str                    # standard role code
    required_pct: float
    recommended_option: Optional[StaffingOption]   # best executable option
    all_options: List[StaffingOption]               # A/B/C/D in order of preference
    gap_detected: bool
    gap_reason: Optional[str]
    hire_signal: bool
    hire_urgency: str


@dataclass
class ProjectStaffingPlan:
    """Complete staffing plan for one pipeline project."""
    pipeline_id: str
    client: str
    client_priority: str
    request_priority: str
    sow_signed: bool
    likely_start_date: Optional[str]
    total_roles: int
    roles_filled_immediate: int           # Plan A
    roles_filled_via_swap: int            # Plan B
    roles_filled_via_wait: int            # Plan C
    roles_needing_hire: int               # Plan D
    roles_unfilled: int                   # no option found
    coverage_pct: float                   # (filled + swap + wait) / total × 100
    extend_start_date_recommended: bool
    recommended_start_date: Optional[str]
    hire_headcount_by_tier: Dict[int, int]
    hire_headcount_by_role: Dict[str, int]
    overall_complexity: str               # LOW | MEDIUM | HIGH | CRITICAL
    implementation_sequence: List[str]    # ordered action list
    role_plans: List[RoleStaffingPlan]
    composite_confidence: float           # average composite score across filled roles


# ─────────────────────────────────────────────────────────────────────────────
# OPTION BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _complexity(plan: SwapPlan, impact: ImpactEstimate) -> str:
    if plan.depth == 0 and plan.candidate_type == "DIRECT":
        return "LOW"
    if plan.depth <= 1 and impact.client_impact in ("None", "Low"):
        return "MEDIUM"
    return "HIGH"


def _swap_chain_summary(plan: SwapPlan) -> List[str]:
    if not plan.chain:
        return []
    lines = []
    for i, link in enumerate(plan.chain):
        if link.replacement_employee_id:
            lines.append(
                f"Step {i+1}: Move {link.employee_id} from {link.from_project_id} → pipeline. "
                f"Replace with {link.replacement_employee_id} "
                f"(confidence {link.replacement_confidence:.0%})."
            )
        else:
            lines.append(
                f"Step {i+1}: Release {link.employee_id} from {link.from_project_id} "
                f"(project ramping down — no backfill needed)."
            )
    return lines


def _business_impact_summary(plan: SwapPlan, impact: ImpactEstimate, candidate: RankedCandidate) -> str:
    parts = []
    if impact.source_project_health_delta < -0.05:
        parts.append(
            f"Source project health decreases by {abs(impact.source_project_health_delta):.0%}."
        )
    if impact.team_loss_fraction > 0:
        parts.append(
            f"Source team loses {impact.team_loss_fraction:.0%} of capacity."
        )
    if impact.client_impact != "None":
        parts.append(f"Client impact: {impact.client_impact}.")
    if impact.estimated_start_delay_days > 0:
        parts.append(f"Start delay: ~{impact.estimated_start_delay_days} days.")
    parts.append(
        f"Weekly revenue contribution: £{impact.weekly_revenue_contribution:,.0f}."
    )
    return " ".join(parts) if parts else "No significant impact on existing projects."


def _build_explanation(
    rc: RankedCandidate,
    plan,
    impact,
    match_level: int,
    level_label: str,
) -> RecommendationExplanation:
    """
    Build a structured human-readable explanation for a ranked candidate.
    Draws from the candidate's enriched profile, impact estimate, and score components.
    """
    c = rc.validated.candidate if hasattr(rc.validated, "candidate") else None
    inf = getattr(c, "inference_metadata", None) if c else None
    is_inferred = bool(inf and inf.is_inferred)
    inference_source = inf.evidence_source if inf else None

    # Capability summary
    if is_inferred and inf:
        cap_summary = (
            f"Inferred capability (no explicit skill data). Evidence: {inf.reason} "
            f"Inferred skills: {', '.join(inf.inferred_skills[:5]) or 'domain-general'}. "
            f"Confidence: {inf.confidence:.2f}."
        )
    elif c and c.has_skill_data:
        cap_summary = (
            f"Explicit skill data available. Avg skill score: {c.avg_skill_score:.1f}/5 across "
            f"{c.skill_breadth} skill(s). Top skills: "
            f"{', '.join(s.get('SubSkill') or s.get('Skill', '') for s in (c.top_skills or [])[:4]) or 'N/A'}."
        )
    else:
        cap_summary = "Skill data not available. Role and COE compatibility used for scoring."

    # Project experience
    proj_exp = (
        f"{c.similar_project_count} prior project(s) with matching "
        f"'{c.primary_coe}' COE."
        if c and c.similar_project_count > 0
        else "No prior project experience in matching COE found in history."
    )

    # Client experience
    if c and c.client_experience_score >= 1.0:
        client_exp = "Has previously billed to this client — repeat engagement."
    else:
        client_exp = "No prior billing history with this client."

    # Domain match
    if c:
        domain_score = c.domain_experience_score
        if domain_score >= 0.9:
            domain_str = f"Primary COE '{c.primary_coe}' is an exact match to pipeline solution."
        elif domain_score >= 0.6:
            domain_str = f"Secondary COE match — '{c.primary_coe}' is adjacent to pipeline solution."
        else:
            domain_str = f"No direct COE match. Domain experience score: {domain_score:.2f}."
    else:
        domain_str = "Domain information not available."

    # Geo/COE routing
    geo = getattr(c, "geo_cluster", "Unknown") if c else "Unknown"
    tier = getattr(c, "seniority_tier", 3) if c else 3
    loc_score = rc.score_components.get("location", 0.0)
    coe_score = rc.score_components.get("coe_match", 0.0)
    if loc_score >= 0.8:
        geo_verdict = "optimal"
    elif loc_score >= 0.5:
        geo_verdict = "acceptable"
    else:
        geo_verdict = "suboptimal"
    geo_str = (
        f"{geo} engineer, Tier {tier} role. Geo routing: {geo_verdict} "
        f"(score {loc_score:.2f}). COE routing score: {coe_score:.2f}."
    )

    # Capacity status
    avail = getattr(c, "effective_availability", 0.0) if c else 0.0
    req_pct = getattr(c, "required_allocation_pct", 100.0) if c else 100.0
    if avail >= req_pct:
        cap_str = f"Fully available: {avail:.0f}% capacity remaining. Role requires {req_pct:.0f}%."
    else:
        gap = req_pct - avail
        cap_str = (
            f"Partially available: {avail:.0f}% of {req_pct:.0f}% required. "
            f"Gap: {gap:.0f}%. "
            + ("Swap planned to free up remaining capacity." if plan.candidate_type == "SWAP"
               else "Soft commit — capacity frees up soon." if plan.candidate_type == "SOFT_COMMIT"
               else "")
        )

    # Business rules
    rules_passed = []
    rules_flagged = []
    if c:
        rules_passed.append(f"HR-4: Not BAU-only — eligible for client projects.")
        if avail > 0:
            rules_passed.append(f"HR-1: Allocation within 100% ceiling ({avail:.0f}% remaining).")
        if not c.on_red_project:
            rules_passed.append("SR-4: Not on a RED/at-risk project.")
        else:
            rules_flagged.append("SR-4: Employee is on a RED project — health penalty applied.")
        if c.ramp_down_flag:
            rules_passed.append("SR-4: Project is ramping down — safe to release, ramp-down bonus applied.")
        if loc_score < 0.5:
            rules_flagged.append(f"SR-1: Geo routing suboptimal ({geo} employee, Tier {tier} role).")

    # Project impact
    if impact.client_impact == "None" and impact.source_project_health_delta >= -0.05:
        impact_str = "No significant impact on existing projects."
    else:
        parts_imp = []
        if impact.source_project_health_delta < -0.01:
            parts_imp.append(
                f"Source project health decreases by {abs(impact.source_project_health_delta):.0%}."
            )
        if impact.team_loss_fraction > 0:
            parts_imp.append(f"Source team loses {impact.team_loss_fraction:.0%} of capacity.")
        if impact.client_impact != "None":
            parts_imp.append(f"Client impact on source project: {impact.client_impact}.")
        impact_str = " ".join(parts_imp) or "Minimal impact."

    # Confidence rationale
    comp = rc.composite_score
    cap_sc = rc.score_components.get("capability_score", 0.0)
    ops_sc = rc.score_components.get("operational_score", 0.0)
    bonuses = rc.score_components.get("business_bonuses", 0.0)
    penalties = rc.score_components.get("business_penalties", 0.0)
    conf_str = (
        f"Final score: {comp:.3f} ({rc.confidence_band}). "
        f"Capability: {cap_sc:.3f} (80% weight) | Operational: {ops_sc:.3f} (20% weight). "
        + (f"Bonuses: +{bonuses:.3f}. " if bonuses > 0 else "")
        + (f"Penalties: -{penalties:.3f}. " if penalties > 0 else "")
        + f"Match level: {level_label}."
        + (f" Capability inferred from {inference_source}." if is_inferred else "")
    )

    return RecommendationExplanation(
        headline=(
            f"[{level_label}] {rc.job_name} ({rc.employee_id}) — "
            f"{rc.geo_cluster}, Tier {rc.seniority_tier}, "
            f"{rc.available_capacity_pct:.0f}% available. Score: {comp:.3f} ({rc.confidence_band})."
        ),
        capability_summary=cap_summary,
        project_experience=proj_exp,
        client_experience=client_exp,
        domain_match=domain_str,
        geo_coe_match=geo_str,
        capacity_status=cap_str,
        business_rules_passed=rules_passed,
        business_rules_flagged=rules_flagged,
        project_impact=impact_str,
        confidence_rationale=conf_str,
        is_inferred=is_inferred,
        inference_source=inference_source,
    )


def _build_option_from_ranked(
    rc: RankedCandidate,
    pipeline_start: Optional[str],
    match_level: int = 1,
) -> StaffingOption:
    """
    Build a StaffingOption from a ranked candidate, tagging it with the
    correct 6-level hierarchy plan_type and match_level.
    """
    from .stage3_rules import (
        MATCH_LEVEL_LABELS, MATCH_LEVEL_PLAN_TYPES, MATCH_LEVEL_CONFIDENCE,
        MATCH_LEVEL_1_EXACT,
    )
    plan = rc.plan
    impact = rc.impact

    # Determine plan_type from the match level (internal candidates: L1-L4)
    # Swap and soft-commit override to their own plan types
    if plan.candidate_type == "SWAP":
        plan_type = "B_SWAP"
        label = "Smart Swap — " + MATCH_LEVEL_LABELS.get(match_level, "Internal Redeployment")
        level = match_level
        level_label = MATCH_LEVEL_LABELS.get(match_level, "Internal")
        conf_band = rc.confidence_band
    elif plan.candidate_type == "SOFT_COMMIT":
        plan_type = "C_WAIT"
        label = "Soft Commit / Wait — " + MATCH_LEVEL_LABELS.get(match_level, "Soon Available")
        level = match_level
        level_label = MATCH_LEVEL_LABELS.get(match_level, "Internal")
        conf_band = rc.confidence_band
    else:
        # DIRECT: use the match level to drive plan type and label
        plan_type = MATCH_LEVEL_PLAN_TYPES.get(match_level, "A_EXACT")
        label = MATCH_LEVEL_LABELS.get(match_level, "Exact Match")
        level = match_level
        level_label = label
        conf_band = MATCH_LEVEL_CONFIDENCE.get(match_level, rc.confidence_band)

    # Expected start date
    if plan.estimated_start_delay_days > 0 and pipeline_start:
        try:
            start_dt = pd.Timestamp(pipeline_start) + pd.Timedelta(days=plan.estimated_start_delay_days)
            start_str = start_dt.strftime("%Y-%m-%d")
        except Exception:
            start_str = None
    else:
        start_str = pipeline_start

    # Build explanation
    explanation = _build_explanation(rc, plan, impact, level, level_label)

    return StaffingOption(
        plan_type=plan_type,
        plan_label=label,
        match_level=level,
        match_label=level_label,
        recommended_employee_id=rc.employee_id,
        job_name=rc.job_name,
        location=rc.location,
        seniority_tier=rc.seniority_tier,
        role=rc.validated.candidate.role if hasattr(rc.validated, "candidate") else None,
        available_capacity_pct=rc.available_capacity_pct,
        composite_score=rc.composite_score,
        confidence_band=conf_band,
        swap_chain_summary=_swap_chain_summary(plan),
        expected_start_date=start_str,
        estimated_delay_days=plan.estimated_start_delay_days,
        implementation_complexity=_complexity(plan, impact),
        business_impact_summary=_business_impact_summary(plan, impact, rc),
        revenue_contribution_weekly=impact.weekly_revenue_contribution,
        hire_tier_needed=None,
        hire_role_needed=None,
        score_breakdown=rc.score_components,
        explanation=explanation,
        redistribution_result=getattr(rc.validated.candidate, "redistribution_result", None),
    )


def _build_hire_option(required_role: str, required_pct: float, backfill: BackfillResult) -> StaffingOption:
    """Level 6 — Hire Recommendation (absolute last resort)."""
    urgency_label = {
        "IMMEDIATE": "Hire Immediately (Gold/SOW Client)",
        "URGENT": "Urgent Hire (Silver Client)",
        "PLANNED": "Planned Hire",
        "NONE": "Hire (Optional)",
    }.get(backfill.hire_urgency, "Hire Recommendation")

    ROLE_TO_TIER = {
        "AP": 7, "P": 6, "TA": 5, "M": 5, "SC": 4, "C": 3, "AC": 2, "SSE": 3, "SE": 2, "ASE": 1, "DS": 4
    }
    tier = ROLE_TO_TIER.get(required_role, 3)

    return StaffingOption(
        plan_type="D_HIRE",
        plan_label=urgency_label,
        match_level=6,
        match_label="Hire Recommendation",
        recommended_employee_id=None,
        job_name=None,
        location=None,
        seniority_tier=tier,
        role=required_role,
        available_capacity_pct=0.0,
        composite_score=0.0,
        confidence_band="LOW",
        swap_chain_summary=[],
        expected_start_date=None,
        estimated_delay_days=90,   # typical hire takes ~3 months
        implementation_complexity="HIGH",
        business_impact_summary=(
            f"No internal candidate found for role {required_role} after exhausting all internal "
            f"redeployment, transferable skills, and availability extension options. "
            f"External hire required at level {required_role}."
        ),
        revenue_contribution_weekly=0.0,
        hire_tier_needed=tier,
        hire_role_needed=required_role,
        score_breakdown={},
    )


def _build_extend_start_option(
    required_role: str,
    required_pct: float,
    candidate_id: str,
    job_name: str,
    location: str,
    seniority_tier: int,
    days_to_available: float,
    current_project_end: Optional[str],
    pipeline_start: Optional[str],
    composite_score: float,
    confidence_band: str,
    daily_rate: float = 600.0,
) -> StaffingOption:
    """
    Level 5 — Extend Project Start Date.
    Recommends delaying the project start until a strong internal candidate
    becomes available. Does NOT commit the employee to the state DB.
    """
    delay_weeks = round(days_to_available / 7.0, 1)

    # Compute suggested revised start date
    suggested_start = None
    if pipeline_start:
        try:
            orig_dt = pd.Timestamp(pipeline_start)
            suggested_dt = orig_dt + pd.Timedelta(days=int(days_to_available) + 7)  # +1 week buffer
            suggested_start = suggested_dt.strftime("%Y-%m-%d")
        except Exception:
            suggested_start = None

    reason = (
        f"{job_name} ({candidate_id}) will complete their current engagement "
        f"in ~{delay_weeks:.0f} weeks"
        + (f" (around {current_project_end})" if current_project_end else "")
        + f". Delaying the project start by {delay_weeks:.0f} weeks avoids unnecessary external "
          f"recruitment while preserving delivery quality. This is the strongest internal match."
    )

    return StaffingOption(
        plan_type="E_EXTEND_START",
        plan_label=f"Extend Start Date by {delay_weeks:.0f} Weeks",
        match_level=5,
        match_label="Extend Start Date",
        recommended_employee_id=candidate_id,
        job_name=job_name,
        location=location,
        seniority_tier=seniority_tier,
        role=required_role,
        available_capacity_pct=0.0,   # not yet available
        composite_score=composite_score,
        confidence_band=confidence_band,
        swap_chain_summary=[],
        expected_start_date=suggested_start,
        estimated_delay_days=int(days_to_available) + 7,
        implementation_complexity="LOW",
        business_impact_summary=reason,
        revenue_contribution_weekly=daily_rate * 5,
        hire_tier_needed=None,
        hire_role_needed=None,
        score_breakdown={},
        extend_start_suggested_date=suggested_start,
        extend_start_delay_weeks=delay_weeks,
        extend_start_reason=reason,
    )


def _build_extend_start_fallback_option(
    required_role: str,
    required_pct: float,
    pipeline_start: Optional[str],
) -> StaffingOption:
    """
    Construct a placeholder start date extension when no Level 5 candidates are found.
    Default delay is 4 weeks (28 days).
    """
    delay_weeks = 4.0
    suggested_start = None
    if pipeline_start:
        try:
            orig_dt = pd.Timestamp(pipeline_start)
            suggested_dt = orig_dt + pd.Timedelta(days=28)
            suggested_start = suggested_dt.strftime("%Y-%m-%d")
        except Exception:
            pass
            
    reason = "Extend start date by 4 weeks to wait for available internal resources."
    
    return StaffingOption(
        plan_type="E_EXTEND_START",
        plan_label="Extend Start Date by 4 Weeks",
        match_level=5,
        match_label="Extend Start Date",
        recommended_employee_id=None,
        job_name=None,
        location=None,
        seniority_tier=3,
        role=required_role,
        available_capacity_pct=0.0,
        composite_score=0.0,
        confidence_band="LOW",
        swap_chain_summary=[],
        expected_start_date=suggested_start,
        estimated_delay_days=28,
        implementation_complexity="MEDIUM",
        business_impact_summary=reason,
        revenue_contribution_weekly=0.0,
        hire_tier_needed=None,
        hire_role_needed=None,
        score_breakdown={},
        extend_start_suggested_date=suggested_start,
        extend_start_delay_weeks=4.0,
        extend_start_reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 7 — PLAN GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

class RecommendationPlanGenerator:

    def __init__(self, ds: DataStore, cfg: EngineConfig = DEFAULT_CONFIG):
        self.ds = ds
        self.cfg = cfg

    def build_role_plan(
        self,
        role_id: str,
        ranked: List[RankedCandidate],
        backfill: BackfillResult,
        pipeline_start: Optional[str],
    ) -> RoleStaffingPlan:
        """Build the full set of options for one role slot."""

        all_options: List[StaffingOption] = []
        seen_types = set()

        for rc in ranked[:5]:   # top 5 ranked candidates → generate options
            opt = _build_option_from_ranked(rc, pipeline_start)
            if opt.plan_type not in seen_types:
                all_options.append(opt)
                seen_types.add(opt.plan_type)
            if len(all_options) >= 3:
                break

        # Always include hire option if needed
        if backfill.hire_signal or not all_options:
            hire_opt = _build_hire_option(
                backfill.required_role, backfill.required_pct, backfill
            )
            if "D_HIRE" not in seen_types:
                all_options.append(hire_opt)

        # Recommend: prefer A → B → C → D
        type_priority = {"A_IMMEDIATE": 0, "B_SWAP": 1, "C_WAIT": 2, "D_HIRE": 3}
        all_options.sort(key=lambda o: (type_priority.get(o.plan_type, 9), -o.composite_score))
        recommended = all_options[0] if all_options else None

        return RoleStaffingPlan(
            role_id=role_id,
            role_name=backfill.role_name,
            seniority_tier=backfill.required_tier,
            required_role=backfill.required_role,
            required_pct=backfill.required_pct,
            recommended_option=recommended,
            all_options=all_options,
            gap_detected=backfill.gap_reason is not None,
            gap_reason=backfill.gap_reason,
            hire_signal=backfill.hire_signal,
            hire_urgency=backfill.hire_urgency,
        )

    def build_project_plan(
        self,
        pipeline_project: pd.Series,
        role_plans: List[RoleStaffingPlan],
    ) -> ProjectStaffingPlan:
        """Assemble the project-level staffing plan from all role plans."""

        pipeline_id = str(pipeline_project.get("pipeline_id") or "")
        client = str(pipeline_project.get("client") or "")
        client_prio = str(pipeline_project.get("client_priority") or "Other")
        req_prio = str(pipeline_project.get("priority") or "Low")
        sow = bool(pipeline_project.get("sow_signed", False))
        start_str = str(pipeline_project.get("likely_start_str") or "") or None
        total = len(role_plans)

        # Count plan types
        counts = {"A_IMMEDIATE": 0, "B_SWAP": 0, "C_WAIT": 0, "D_HIRE": 0, "NONE": 0}
        hire_by_tier: Dict[int, int] = {}
        hire_by_role: Dict[str, int] = {}
        scores = []

        for rp in role_plans:
            opt = rp.recommended_option
            if opt is None:
                counts["NONE"] += 1
            else:
                p_type = opt.plan_type
                if p_type.startswith("A_"):
                    p_type = "A_IMMEDIATE"
                counts[p_type] = counts.get(p_type, 0) + 1
                if opt.plan_type == "D_HIRE":
                    if opt.hire_tier_needed:
                        hire_by_tier[opt.hire_tier_needed] = hire_by_tier.get(opt.hire_tier_needed, 0) + 1
                    if opt.hire_role_needed:
                        hire_by_role[opt.hire_role_needed] = hire_by_role.get(opt.hire_role_needed, 0) + 1
                if opt.composite_score > 0:
                    scores.append(opt.composite_score)

        filled_count = counts["A_IMMEDIATE"] + counts["B_SWAP"] + counts["C_WAIT"]
        coverage = (filled_count / total * 100) if total > 0 else 0.0

        # Extend-start-date recommendation:
        # If ≥50% roles are Plan C (wait) and no Plan D, suggest extending start date
        wait_fraction = counts["C_WAIT"] / total if total > 0 else 0.0
        hire_fraction = counts["D_HIRE"] / total if total > 0 else 0.0
        extend_recommended = wait_fraction >= 0.5 and hire_fraction < 0.5

        # Find earliest recommended start across all C_WAIT plans
        wait_dates = []
        for rp in role_plans:
            opt = rp.recommended_option
            if opt and opt.plan_type == "C_WAIT" and opt.expected_start_date:
                try:
                    wait_dates.append(pd.Timestamp(opt.expected_start_date))
                except Exception:
                    pass
        recommended_start = (
            max(wait_dates).strftime("%Y-%m-%d") if wait_dates else start_str
        )

        # Overall complexity
        hire_ratio = counts["D_HIRE"] / total if total > 0 else 0.0
        swap_ratio = counts["B_SWAP"] / total if total > 0 else 0.0
        if hire_ratio > 0.4 or counts["NONE"] > 0:
            overall_complexity = "CRITICAL"
        elif hire_ratio > 0.2 or swap_ratio > 0.5:
            overall_complexity = "HIGH"
        elif swap_ratio > 0.2:
            overall_complexity = "MEDIUM"
        else:
            overall_complexity = "LOW"

        # Implementation sequence (ordered action list)
        sequence = _build_sequence(role_plans, sow, client_prio)

        composite_confidence = round(sum(scores) / len(scores), 3) if scores else 0.0

        return ProjectStaffingPlan(
            pipeline_id=pipeline_id,
            client=client,
            client_priority=client_prio,
            request_priority=req_prio,
            sow_signed=sow,
            likely_start_date=start_str,
            total_roles=total,
            roles_filled_immediate=counts["A_IMMEDIATE"],
            roles_filled_via_swap=counts["B_SWAP"],
            roles_filled_via_wait=counts["C_WAIT"],
            roles_needing_hire=counts["D_HIRE"],
            roles_unfilled=counts["NONE"],
            coverage_pct=round(coverage, 1),
            extend_start_date_recommended=extend_recommended,
            recommended_start_date=recommended_start,
            hire_headcount_by_tier=hire_by_tier,
            hire_headcount_by_role=hire_by_role,
            overall_complexity=overall_complexity,
            implementation_sequence=sequence,
            role_plans=role_plans,
            composite_confidence=composite_confidence,
        )


def _build_sequence(role_plans: List[RoleStaffingPlan], sow_signed: bool, client_prio: str) -> List[str]:
    """Generate an ordered plain-English action list for the RM team."""
    actions = []

    if not sow_signed:
        actions.append("⚠️  SOW not yet signed — confirm SOW before finalising allocations.")

    immediate = [rp for rp in role_plans if rp.recommended_option and (rp.recommended_option.plan_type == "A_IMMEDIATE" or rp.recommended_option.plan_type.startswith("A_"))]
    swaps = [rp for rp in role_plans if rp.recommended_option and rp.recommended_option.plan_type == "B_SWAP"]
    waits = [rp for rp in role_plans if rp.recommended_option and rp.recommended_option.plan_type == "C_WAIT"]
    hires = [rp for rp in role_plans if rp.hire_signal]

    if immediate:
        eids = [rp.recommended_option.recommended_employee_id for rp in immediate]
        actions.append(f"✅  Allocate immediately: {', '.join(eids)} ({len(immediate)} role(s)).")

    for rp in swaps:
        opt = rp.recommended_option
        chain_str = " → ".join(s.split(":")[0] for s in (opt.swap_chain_summary or []))
        actions.append(
            f"🔄  Arrange swap for {rp.role_name}: {chain_str or 'see swap details'}."
        )

    if waits:
        actions.append(
            f"⏳  Soft-commit {len(waits)} candidate(s) — confirm availability after "
            f"current project ramp-down."
        )

    if hires:
        role_summary = {}
        for rp in hires:
            r = rp.required_role
            role_summary[r] = role_summary.get(r, 0) + 1
        role_str = ", ".join(f"{r}: {n}" for r, n in sorted(role_summary.items()))
        urgency = "IMMEDIATELY" if client_prio == "Gold" else "as soon as possible"
        actions.append(f"🧑‍💼  Raise hire request {urgency}: {role_str}.")

    if not actions:
        actions.append("No actions required — project can be fully staffed.")

    return actions
