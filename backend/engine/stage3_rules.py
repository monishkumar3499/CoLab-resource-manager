"""
stage3_rules.py — Business Rule Validation

Applies all hard and soft enterprise constraints to the enriched candidate list.

Hard rules  → candidate removed immediately if violated
Soft rules  → candidate kept but penalised (signals passed to ranking engine)

Rule classes implemented:
  HR-1   Allocation ceiling (100%)
  HR-2   Seniority tier ±1 (±2 for Urgent)
  HR-3   Capacity floor (must have ≥50% of required capacity)
  HR-4   BAU/Internal-only exclusion
  HR-5   Managed service exclusion
  HR-6   Client-specific restrictions
  HR-7   No double allocation (same person in two roles of same project)
  SR-1   Geo-cluster routing (UK-first for UK clients)
  SR-2   India COE+cluster match priority
  SR-3   Location preference (onsite projects)
  SR-4   Ramp-down bonus
  SR-5   Health penalty propagation
  SR-6   Extension risk signal
  SR-7   Future reservation check
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG
from .loader import DataStore
from .stage2_intelligence import EnrichedCandidate


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION RESULT DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

class RuleOutcome(Enum):
    PASS = "PASS"
    FAIL_HARD = "FAIL_HARD"    # candidate removed
    WARN_SOFT = "WARN_SOFT"    # candidate kept, signal added


@dataclass
class RuleViolation:
    rule_id: str
    outcome: RuleOutcome
    message: str
    penalty: float = 0.0       # soft penalty on composite score
    bonus: float = 0.0         # soft bonus


@dataclass
class ValidatedCandidate:
    candidate: EnrichedCandidate
    passed: bool                       # False = hard rule violation → excluded
    violations: List[RuleViolation]    # all rule results
    soft_penalty: float                # total soft penalty from all soft rules
    soft_bonus: float                  # total soft bonus from all soft rules
    geo_routing_score: float           # 0-1: how well candidate satisfies geo routing
    coe_routing_score: float           # 0-1: COE match strength
    failure_reason: Optional[str]      # first hard failure message


# ─────────────────────────────────────────────────────────────────────────────
# GEO ROUTING LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def _infer_pipeline_geo(pipeline_project: pd.Series) -> str:
    """
    Infer whether a pipeline project is UK-facing or India-facing.
    Logic:
      - If cluster ∈ {1,2,3,4,5} and no explicit override → check client priority
      - UK-facing: Gold/Silver clients, EM field populated with UK-sounding names
      - India-facing: primarily where Cluster + COE match India COE pool
    Since we don't have explicit geo tagging in pipeline, we use a heuristic:
      All clusters are UK-facing (the company is UK-HQ).
      India resources are the delivery engine; UK staff are senior/client-facing.
    Therefore:
      - Tier ≥ 5 roles → prefer UK geo (senior/client-facing)
      - Tier ≤ 3 roles → prefer India geo (delivery/engineering)
      - Tier 4 → neutral
    This matches the problem statement: "UK cluster-to-cluster first priority"
    means senior roles go to UK employees; India fills engineering roles.
    """
    # Not enough data to determine geo from pipeline alone — return "UK" as default
    # The per-role routing logic will handle tier-based preferences
    return "UK"


def _geo_routing_score(
    candidate: EnrichedCandidate,
    role_tier: int,
    pipeline_project: pd.Series,
    cfg: EngineConfig,
) -> float:
    """
    Returns 0-1 geo routing compatibility score.

    Rules:
      UK employees:
        - Tier ≥ 5 (senior/client-facing): score = 1.0
        - Tier 4: score = 0.8
        - Tier ≤ 3: score = 0.5  (still valid, just not preferred)

      India employees:
        - Tier ≤ 3: score = 1.0
        - Tier 4: score = 0.7
        - Tier ≥ 5: score = 0.4  (can do, but UK preferred)

      US employees:
        - Treated like UK for routing purposes
    """
    geo = candidate.geo_cluster
    if geo in ("UK", "US"):
        if role_tier >= 5:
            return 1.0
        elif role_tier == 4:
            return 0.8
        else:
            return 0.5
    elif geo == "India":
        if role_tier <= 3:
            return 1.0
        elif role_tier == 4:
            return 0.7
        else:
            return 0.4
    return 0.5  # Unknown geo → neutral


def _coe_routing_score(candidate: EnrichedCandidate, pipeline_project: pd.Series) -> float:
    """
    India-COE matching: how well does the candidate's COE match the pipeline solution type?
    """
    solution = str(pipeline_project.get("solution") or "").lower()
    primary_coe = candidate.primary_coe.lower()
    all_coes = [c.lower() for c in candidate.all_coes]

    # Solution-to-COE mapping
    SOL_COE_MAP = {
        "value creation":    ["consulting", "power bi & consulting"],
        "core reporting":    ["power bi & consulting", "data engineering"],
        "data advisory":     ["consulting", "data science & ai"],
        "due diligence":     ["consulting", "data science & ai"],
        "exit support":      ["consulting"],
        "ai":               ["data science & ai", "full stack"],
        "data platform":     ["data engineering"],
        "full stack":        ["full stack"],
        "techops":           ["techops & automation"],
    }

    preferred_coes = []
    for key, coes in SOL_COE_MAP.items():
        if key in solution:
            preferred_coes.extend(coes)

    if not preferred_coes:
        return 0.5  # No mapping → neutral

    if primary_coe in preferred_coes:
        return 1.0
    if any(c in preferred_coes for c in all_coes):
        return 0.7
    return 0.3


# ─────────────────────────────────────────────────────────────────────────────
# RULE IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

def _rule_hr1_allocation_ceiling(
    c: EnrichedCandidate, required_pct: float, cfg: EngineConfig
) -> RuleViolation:
    """HR-1: Total allocation after assignment must not exceed 100%."""
    is_red = bool(c.on_red_project)
    is_amber = bool(c.on_amber_project)
    
    current_util = c.util_pct
    projected = current_util + required_pct
    if projected > cfg.rules.max_allocation_pct:
        gap = c.effective_availability
        min_required = required_pct * cfg.rules.min_available_for_role
        if gap < min_required:
            if is_red or is_amber:
                return RuleViolation("HR-1", RuleOutcome.FAIL_HARD,
                    f"Allocation ceiling: {current_util:.0f}% + {required_pct:.0f}% = "
                    f"{projected:.0f}% > 100%. Only {gap:.0f}% available on RED/AMBER project.")
            else:
                return RuleViolation("HR-1", RuleOutcome.WARN_SOFT,
                    f"Candidate requires swapping to free up capacity (currently {current_util:.0f}% utilised).",
                    penalty=0.05)
        else:
            return RuleViolation("HR-1", RuleOutcome.WARN_SOFT,
                f"Partially available: {gap:.0f}% of {required_pct:.0f}% required.",
                penalty=0.05)
    return RuleViolation("HR-1", RuleOutcome.PASS, "Allocation OK")


def _rule_hr8_concurrent_client_projects(
    candidate: EnrichedCandidate,
    temp_state: Optional[Dict[str, Dict]],
    required_pct: float,
    cfg: EngineConfig,
) -> RuleViolation:
    """
    HR-8: Enforce role-based concurrent client project policy.
    SE/SSE: max 1 client project.
    Enabler: max 2 client projects.
    SC/Manager: max 3 client projects.
    TA/Principal/AP/Partner: max 5 client projects.
    Internal projects never count toward the limit.
    """
    from .role_policy import ROLE_POLICIES, get_policy
    eid = candidate.employee_id
    
    if temp_state and eid in temp_state:
        cat = temp_state[eid].get("role_category")
        policy = ROLE_POLICIES.get(cat) if cat else None
        if not policy:
            policy = get_policy(candidate.job_name)
        client_project_count = temp_state[eid].get("client_project_count", 0)
        client_capacity_remaining = temp_state[eid].get("client_capacity_remaining", 100.0)
    else:
        policy = get_policy(candidate.job_name)
        client_project_count = 0
        client_capacity_remaining = candidate.effective_availability

    # Check concurrent client project limit and capacity floor
    is_red = bool(candidate.on_red_project)
    is_amber = bool(candidate.on_amber_project)
    
    # If on RED or AMBER project, enforce strict concurrent limit and capacity floor
    if is_red or is_amber:
        if client_project_count >= policy.max_client_projects:
            return RuleViolation(
                rule_id="HR-8",
                outcome=RuleOutcome.FAIL_HARD,
                message=f"Concurrent client project limit reached ({client_project_count}/{policy.max_client_projects}) for category {policy.category} on RED/AMBER project."
            )
        min_cap = required_pct * cfg.rules.min_available_for_role
        if client_capacity_remaining < min_cap:
            return RuleViolation(
                rule_id="HR-8",
                outcome=RuleOutcome.FAIL_HARD,
                message=f"Insufficient client capacity floor ({client_capacity_remaining:.1f}% < {min_cap:.1f}%) on RED/AMBER project."
            )
    else:
        # Candidate is on a GREEN/Good client project, or is shadow/internal/bench.
        # They are eligible for allocation/swapping.
        pass

    return RuleViolation(
        rule_id="HR-8",
        outcome=RuleOutcome.PASS,
        message="Role allocation policy constraints passed (candidate is on a Green/Good project or shadow/internal/bench)."
    )


def _rule_hr2_seniority(
    c: EnrichedCandidate, required_tier: int, is_urgent: bool, cfg: EngineConfig
) -> RuleViolation:
    """HR-2: Seniority must be within ±1 (or ±2 for Urgent)."""
    flex = cfg.rules.tier_flex_urgent if is_urgent else cfg.rules.tier_flex
    delta = abs(c.seniority_tier - required_tier)
    if delta > flex:
        return RuleViolation("HR-2", RuleOutcome.FAIL_HARD,
            f"Tier mismatch: candidate tier {c.seniority_tier}, "
            f"required {required_tier} (±{flex} allowed).")
    if delta == 1:
        return RuleViolation("HR-2", RuleOutcome.WARN_SOFT,
            f"Tier offset by 1 (candidate {c.seniority_tier} vs required {required_tier}).",
            penalty=0.03)
    if delta == 2 and is_urgent:
        return RuleViolation("HR-2", RuleOutcome.WARN_SOFT,
            f"Tier offset by 2 (urgent exception applied).", penalty=0.08)
    return RuleViolation("HR-2", RuleOutcome.PASS, "Seniority OK")


def _rule_hr3_capacity(
    c: EnrichedCandidate, required_pct: float, cfg: EngineConfig
) -> RuleViolation:
    """HR-3: Candidate must have at least 50% of required capacity (hard floor)."""
    is_red = bool(c.on_red_project)
    is_amber = bool(c.on_amber_project)
    
    min_cap = required_pct * cfg.rules.min_available_for_role
    if c.effective_availability < min_cap:
        if is_red or is_amber:
            return RuleViolation("HR-3", RuleOutcome.FAIL_HARD,
                f"Insufficient capacity: {c.effective_availability:.0f}% available, "
                f"{min_cap:.0f}% minimum needed for {required_pct:.0f}% role on RED/AMBER project.")
        else:
            return RuleViolation("HR-3", RuleOutcome.WARN_SOFT,
                f"Candidate requires swapping to satisfy capacity floor (only {c.effective_availability:.0f}% available).",
                penalty=0.02)
    return RuleViolation("HR-3", RuleOutcome.PASS, "Capacity OK")


def _rule_hr4_bau_exclusion(c: EnrichedCandidate, cfg: EngineConfig) -> RuleViolation:
    """HR-4: BAU-only employees cannot be allocated to client projects."""
    if c.is_bau_only:
        return RuleViolation("HR-4", RuleOutcome.FAIL_HARD,
            "Employee is BAU-only — allocated exclusively to internal/BAU projects.")
    return RuleViolation("HR-4", RuleOutcome.PASS, "Not BAU-only")


def _rule_hr5_managed_service(c: EnrichedCandidate, cfg: EngineConfig) -> RuleViolation:
    """HR-5: Managed service employees can be recommended but flagged for manager approval."""
    # Not a hard exclusion — managed service is a soft flag for manager awareness
    # They can potentially be reassigned; the swap planner handles this
    return RuleViolation("HR-5", RuleOutcome.PASS, "MS OK")


def _rule_hr6_client_restriction(
    c: EnrichedCandidate, pipeline_client: str, cfg: EngineConfig
) -> RuleViolation:
    """HR-6: Do not reallocate critical resources away from restricted key clients."""
    restricted_emps = cfg.rules.restricted_clients.get(pipeline_client, [])
    if c.employee_id in restricted_emps:
        return RuleViolation("HR-6", RuleOutcome.FAIL_HARD,
            f"Employee {c.employee_id} is key resource restricted to client {pipeline_client}.")
    return RuleViolation("HR-6", RuleOutcome.PASS, "Client restriction OK")


def _rule_hr7_double_allocation(
    c: EnrichedCandidate, already_assigned: Set[str]
) -> RuleViolation:
    """HR-7: Employee cannot be allocated to two roles in the same project team."""
    if c.employee_id in already_assigned:
        return RuleViolation("HR-7", RuleOutcome.FAIL_HARD,
            f"Employee {c.employee_id} is already allocated to another role in this project.")
    return RuleViolation("HR-7", RuleOutcome.PASS, "Double allocation check OK")


def _rule_sr1_geo_routing(
    c: EnrichedCandidate, role_tier: int, pipeline_project: pd.Series, cfg: EngineConfig
) -> RuleViolation:
    """
    SR-1: Geo-cluster routing guidelines (soft match).
    UK client project roles:
      - Tier >= 5: strongly prefer UK resources (WARN soft if not UK)
      - Tier <= 3: strongly prefer India resources (WARN soft if not India)
    """
    geo_score = _geo_routing_score(c, role_tier, pipeline_project, cfg)
    if geo_score >= 0.8:
        return RuleViolation("SR-1", RuleOutcome.PASS, "Optimal geo routing")
    elif geo_score >= 0.5:
        return RuleViolation("SR-1", RuleOutcome.WARN_SOFT,
            f"Suboptimal geo routing (geo={c.geo_cluster}, tier={role_tier})", bonus=0.0)
    else:
        return RuleViolation("SR-1", RuleOutcome.WARN_SOFT,
            f"Poor geo routing guideline match (geo={c.geo_cluster}, tier={role_tier})", penalty=0.03)


def _rule_sr2_india_coe_priority(
    c: EnrichedCandidate, pipeline_project: pd.Series, cfg: EngineConfig
) -> RuleViolation:
    """SR-2: Prefer India resources from the primary matching CoE (soft guide)."""
    if c.geo_cluster != "India":
        return RuleViolation("SR-2", RuleOutcome.PASS, "Non-India resource (SR-2 not applicable)")
    coe_score = _coe_routing_score(c, pipeline_project)
    if coe_score >= 0.8:
        return RuleViolation("SR-2", RuleOutcome.WARN_SOFT, "Primary CoE Match", bonus=0.03)
    elif coe_score >= 0.5:
        return RuleViolation("SR-2", RuleOutcome.PASS, "Secondary CoE Match")
    return RuleViolation("SR-2", RuleOutcome.WARN_SOFT, "COE mismatch penalty", penalty=0.02)


def _rule_sr3_location_preference(
    c: EnrichedCandidate, role_row: pd.Series, cfg: EngineConfig
) -> RuleViolation:
    """SR-3: Soft preference matching physical location of client if specified (onsite/hybrid)."""
    req_loc = role_row.get("location_notes") or ""
    if not req_loc or pd.isna(req_loc):
        return RuleViolation("SR-3", RuleOutcome.PASS, "No location constraints specified")
    
    cand_loc = str(c.location).lower().strip()
    req_loc_clean = str(req_loc).lower().strip()
    
    if cand_loc == req_loc_clean:
        return RuleViolation("SR-3", RuleOutcome.WARN_SOFT, "Exact location match", bonus=0.02)
    return RuleViolation("SR-3", RuleOutcome.PASS, "Location neutral")


def _rule_sr4_health(c: EnrichedCandidate, cfg: EngineConfig) -> RuleViolation:
    """SR-4: Health penalty for candidates on at-risk projects."""
    if c.health_penalty > 0:
        return RuleViolation("SR-4", RuleOutcome.WARN_SOFT,
            f"Health penalty {c.health_penalty:.2f}: "
            f"candidate is on {'RED' if c.on_red_project else 'AMBER/poor'} project.",
            penalty=c.health_penalty)
    return RuleViolation("SR-4", RuleOutcome.PASS, "No health penalty")


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 3 — BUSINESS RULE VALIDATOR
# ─────────────────────────────────────────────────────────────────────────────

class BusinessRuleValidator:

    def __init__(self, ds: DataStore, cfg: EngineConfig = DEFAULT_CONFIG):
        self.ds = ds
        self.cfg = cfg

    def validate_business_constraints(
        self,
        candidates: List[EnrichedCandidate],
        required_pct: float,
        pipeline_project: pd.Series,
        already_assigned: Set[str],
        temp_state: Optional[Dict[str, Dict]] = None,
    ) -> List[EnrichedCandidate]:
        """
        Stage 2: Non-negotiable hard business rules.
        Filters candidate pool and returns only those passing constraints.
        """
        pipeline_client = str(pipeline_project.get("client") or "")
        passing = []
        for c in candidates:
            eid = c.employee_id
            
            # HR-7: Double allocation
            if c.employee_id in already_assigned:
                continue

            # HR-4: BAU-only employees cannot be allocated to client projects
            if c.is_bau_only:
                continue

            # HR-6: Client-specific restrictions
            restricted = self.cfg.rules.restricted_clients.get(pipeline_client, [])
            if c.employee_id in restricted:
                continue

            # Check if candidate is a main/key resource on any active project
            is_main_resource = False
            for ctx in c.current_project_contexts:
                if not ctx.is_safe_to_pull:
                    is_main_resource = True
                    break

            if is_main_resource:
                # Unsafe to pull (main resource)
                continue

            passing.append(c)
        return passing

    def validate_technical_eligibility(
        self,
        candidates: List[EnrichedCandidate],
        required_role: str,
        role_row: pd.Series,
    ) -> List[EnrichedCandidate]:
        """
        Stage 3: Verify compatible role, mandatory skills, certifications, and competencies.
        """
        from .config import standardize_role
        req_role_std = standardize_role(required_role)
        compatibles = self.cfg.rules.role_compatibility.get(req_role_std, [req_role_std])

        passing = []
        for c in candidates:
            # 1. Compatible role check
            cand_role_std = standardize_role(c.job_name)
            if cand_role_std not in compatibles:
                continue

            # 4. Competency check
            if req_role_std in ("AP", "P", "TA", "M"):
                if c.has_competency_data and c.avg_competency_score < 2.5:
                    continue

            passing.append(c)
        return passing

    def validate(
        self,
        candidates: List[EnrichedCandidate],
        role_tier: int,
        required_pct: float,
        pipeline_project: pd.Series,
        already_assigned: Optional[Set[str]] = None,
    ) -> Tuple[List[ValidatedCandidate], List[ValidatedCandidate]]:
        """Legacy compatibility method."""
        assigned = already_assigned or set()
        passing_candidates = self.validate_business_constraints(candidates, required_pct, pipeline_project, assigned)
        # Determine required_role from candidates if present, else fallback
        required_role = candidates[0].required_role if candidates else "C"
        
        # Build a dummy role_row for skillset notes compatibility
        dummy_role_row = pd.Series({"role_abbr": required_role, "skillset_notes": ""})
        
        eligible_candidates = self.validate_technical_eligibility(passing_candidates, required_role, dummy_role_row)
        
        passing_v = []
        failing_v = []
        
        eligible_set = {c.employee_id for c in eligible_candidates}
        
        for c in candidates:
            vc = ValidatedCandidate(
                candidate=c,
                passed=c.employee_id in eligible_set,
                violations=[],
                soft_penalty=0.0,
                soft_bonus=0.0,
                geo_routing_score=1.0,
                coe_routing_score=1.0,
                failure_reason=None if c.employee_id in eligible_set else "Failed business constraints or technical eligibility",
            )
            if vc.passed:
                passing_v.append(vc)
            else:
                failing_v.append(vc)
                
        return passing_v, failing_v

    def validate_for_project(
        self,
        enriched_by_role: Dict[str, List[EnrichedCandidate]],
        pipeline_project: pd.Series,
    ) -> Dict[str, Tuple[List[ValidatedCandidate], List[ValidatedCandidate]]]:
        """Legacy compatibility method."""
        results = {}
        globally_assigned: Set[str] = set()
        
        for role_id, candidates in enriched_by_role.items():
            if not candidates:
                results[role_id] = ([], [])
                continue
            c0 = candidates[0]
            role_tier = c0.required_tier
            required_pct = c0.required_allocation_pct
            
            passing, failing = self.validate(
                candidates, role_tier, required_pct,
                pipeline_project, globally_assigned
            )
            results[role_id] = (passing, failing)
            if passing:
                globally_assigned.add(passing[0].candidate.employee_id)
                
        return results


# ─────────────────────────────────────────────────────────────────────────────
# 6-LEVEL MATCH CLASSIFIER
# ─────────────────────────────────────────────────────────────────────────────

# Match level constants
MATCH_LEVEL_1_EXACT = 1
MATCH_LEVEL_2_STRONG = 2
MATCH_LEVEL_3_TRANSFERABLE = 3
MATCH_LEVEL_4_AVAILABILITY = 4

MATCH_LEVEL_LABELS = {
    MATCH_LEVEL_1_EXACT:         "Exact Match",
    MATCH_LEVEL_2_STRONG:        "Strong Partial Match",
    MATCH_LEVEL_3_TRANSFERABLE:  "Transferable Skills Match",
    MATCH_LEVEL_4_AVAILABILITY:  "Availability-Based Recommendation",
}

MATCH_LEVEL_PLAN_TYPES = {
    MATCH_LEVEL_1_EXACT:         "A_EXACT",
    MATCH_LEVEL_2_STRONG:        "A_STRONG",
    MATCH_LEVEL_3_TRANSFERABLE:  "A_TRANSFERABLE",
    MATCH_LEVEL_4_AVAILABILITY:  "A_AVAILABILITY",
}

MATCH_LEVEL_CONFIDENCE = {
    MATCH_LEVEL_1_EXACT:         "HIGH",
    MATCH_LEVEL_2_STRONG:        "MEDIUM",
    MATCH_LEVEL_3_TRANSFERABLE:  "MEDIUM-LOW",
    MATCH_LEVEL_4_AVAILABILITY:  "LOW",
}


def classify_match_level(
    candidate: EnrichedCandidate,
    required_pct: float,
    cfg: EngineConfig = DEFAULT_CONFIG,
) -> int:
    """
    Classify an eligible candidate into one of four internal match levels.

    Level 1: Exact Match:
        skill_confidence >= exact_match_skill_threshold (0.85)
        AND competency_confidence >= exact_match_competency_threshold (0.70)
        AND effective_availability >= required_pct (full capacity available now)

    Level 2: Strong Partial Match:
        skill_confidence >= strong_partial_skill_threshold (0.65)
        AND effective_availability >= required_pct

    Level 3: Transferable Skills:
        semantic_score >= transferable_semantic_threshold (0.40)
        OR domain_experience_score >= transferable_domain_threshold (0.60)

    Level 4: Availability-Based:
        Everything else - business role match + some capacity.
    """
    r = cfg.rules
    avail = candidate.effective_availability
    skill = candidate.skill_confidence
    comp = candidate.competency_confidence
    sem = candidate.semantic_score
    domain = candidate.domain_experience_score

    # Level 1 — Exact Match
    if (
        skill >= r.exact_match_skill_threshold
        and comp >= r.exact_match_competency_threshold
        and avail >= required_pct
    ):
        return MATCH_LEVEL_1_EXACT

    # Level 2 — Strong Partial (has the skills, has the capacity)
    if skill >= r.strong_partial_skill_threshold and avail >= required_pct:
        return MATCH_LEVEL_2_STRONG

    # Level 3 — Transferable (semantically related or domain-adjacent)
    if sem >= r.transferable_semantic_threshold or domain >= r.transferable_domain_threshold:
        return MATCH_LEVEL_3_TRANSFERABLE

    # Level 4 — Availability-Based fallback
    return MATCH_LEVEL_4_AVAILABILITY
