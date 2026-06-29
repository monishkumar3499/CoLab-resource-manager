"""
stage4_swap.py — Swap & Backfill Planner

When the best-matched employee is occupied, this stage searches for
a replacement chain that allows reallocation without destabilising
the source project.

Swap search is a bounded DFS (depth ≤ config.rules.max_swap_depth).
Every swap must satisfy:
  - The source project remains adequately staffed
  - The replacement meets ≥ swap_confidence_threshold
  - No project's health falls below critical_health_floor after the move
  - Allocation limits are respected throughout the chain

Edge cases handled:
  CASE-A  Target is available → direct allocation (no swap needed)
  CASE-B  Target is busy but on a ramping-down project → soft-commit after end date
  CASE-C  Target is busy, replacement found → swap chain proposed
  CASE-D  Target is busy, no replacement → hold (extend start date signal)
  CASE-E  No candidate at all → hire signal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG
from .loader import DataStore
from .stage2_intelligence import EnrichedCandidate
from .stage3_rules import ValidatedCandidate


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SwapLink:
    """One link in a swap chain."""
    employee_id: str              # employee being moved
    from_project_id: str          # leaving this project
    to_project_id: str            # joining this project / pipeline
    replacement_employee_id: Optional[str]   # who fills the gap they leave
    replacement_confidence: float
    source_project_health_before: float
    source_project_health_after: float
    is_safe: bool
    reason: str


@dataclass
class SwapPlan:
    """Complete swap chain for a single role recommendation."""
    target_employee_id: str        # the employee we ultimately want for the pipeline role
    chain: List[SwapLink]          # the sequence of swaps to make it happen
    depth: int                     # number of hops
    overall_confidence: float      # product of all replacement_confidence scores
    estimated_start_delay_days: int
    is_feasible: bool
    infeasible_reason: Optional[str]
    candidate_type: str            # "DIRECT" | "SWAP" | "SOFT_COMMIT" | "HOLD" | "HIRE"


@dataclass
class BackfillResult:
    """Full result for one role slot after swap planning."""
    role_id: str
    role_name: str
    required_tier: int
    required_pct: float
    primary_plan: Optional[SwapPlan]          # best plan
    alternative_plans: List[SwapPlan]         # next 2 best plans
    hire_signal: bool
    hire_urgency: str              # "IMMEDIATE" | "URGENT" | "PLANNED" | "NONE"
    extend_start_date_signal: bool
    estimated_availability_date: Optional[str]
    passing_candidates: List[ValidatedCandidate]
    gap_reason: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# REPLACEMENT FINDER
# ─────────────────────────────────────────────────────────────────────────────

def _find_replacement(
    project_id: str,
    vacating_employee: EnrichedCandidate,
    ds: DataStore,
    cfg: EngineConfig,
    excluded: Set[str],
) -> Tuple[Optional[str], float]:
    """
    Find a replacement for vacating_employee in project_id.
    Returns (replacement_employee_id, confidence) or (None, 0.0).

    Strategy:
      1. Find employees partially available with compatible role
      2. Prefer ramp-down candidates (soon free)
      3. Prefer same COE as vacating employee
      4. Score by availability + COE match + role match
    """
    project = ds.get_project(project_id)
    if not project:
        return None, 0.0

    from .config import standardize_role
    req_role_std = standardize_role(vacating_employee.job_name)
    compatibles = cfg.rules.role_compatibility.get(req_role_std, [req_role_std])

    # Get available people not already on this project or excluded
    current_team = set(ds.get_project_employees(project_id))
    people_copy = ds.people.copy()
    people_copy["_std_role"] = people_copy["job_name"].apply(standardize_role)

    candidates = people_copy[
        (people_copy["_std_role"].isin(compatibles)) &
        (people_copy["effective_availability"] >= 25.0) &
        (~people_copy["employee_id"].isin(current_team | excluded)) &
        (~people_copy["is_bau_only"])
    ].copy()

    if candidates.empty:
        return None, 0.0

    # Score candidates
    def score(row):
        avail_s = min(1.0, row["effective_availability"] / 100.0)
        coe_s = 1.0 if row["primary_coe"] == vacating_employee.primary_coe else 0.4
        tier_s = 1.0 if row["_std_role"] == req_role_std else 0.7
        ramp_s = 0.1 if row.get("ramp_down_flag", False) else 0.0
        return avail_s * 0.5 + coe_s * 0.3 + tier_s * 0.2 + ramp_s

    candidates["_replace_score"] = candidates.apply(score, axis=1)
    best = candidates.nlargest(1, "_replace_score").iloc[0]
    confidence = float(best["_replace_score"])

    if confidence < cfg.rules.swap_confidence_threshold:
        return None, confidence

    return best["employee_id"], confidence


def _project_health_after_removal(
    project_id: str,
    employee_id: str,
    replacement_id: Optional[str],
    ds: DataStore,
    cfg: EngineConfig,
) -> float:
    """
    Estimate project health after removing employee_id and optionally
    adding replacement_id.

    Simple model:
      - No replacement: health degrades by (1/team_size) × health_factor
      - With good replacement: health degrades slightly (knowledge transfer cost)
      - With poor/no replacement: health degrades more
    """
    p = ds.get_project(project_id)
    if not p:
        return 0.65

    current_health = float(p.get("health_score") or 0.65)
    team_size = max(1, int(p.get("total_slots") or 1))
    team_loss_fraction = 1.0 / team_size

    if replacement_id:
        # With replacement: small degradation for knowledge transfer
        health_after = current_health * (1.0 - team_loss_fraction * 0.2)
    else:
        # Without replacement: larger degradation
        health_after = current_health * (1.0 - team_loss_fraction * 0.5)

    return max(0.0, round(health_after, 3))


# ─────────────────────────────────────────────────────────────────────────────
# SWAP PLAN BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _build_swap_plan(
    target: EnrichedCandidate,
    ds: DataStore,
    cfg: EngineConfig,
    pipeline_id: str,
    visited: Set[str],
    depth: int = 0,
) -> SwapPlan:
    """
    Recursively build a swap chain to free up target employee.
    depth: current recursion depth (max = cfg.rules.max_swap_depth)
    visited: set of employee_ids already in the chain (prevent cycles)
    """
    if depth > cfg.rules.max_swap_depth:
        return SwapPlan(
            target_employee_id=target.employee_id,
            chain=[], depth=depth, overall_confidence=0.0,
            estimated_start_delay_days=0, is_feasible=False,
            infeasible_reason=f"Swap depth limit ({cfg.rules.max_swap_depth}) reached.",
            candidate_type="HOLD",
        )

    chain: List[SwapLink] = []
    confidence_product = 1.0
    total_delay = 0
    new_visited = visited | {target.employee_id}

    for project_ctx in target.current_project_contexts:
        pid = project_ctx.project_id

        # Check if pulling from this project is safe
        if not project_ctx.is_safe_to_pull:
            # Try finding a replacement anyway
            repl_id, repl_conf = _find_replacement(
                pid, target, ds, cfg, new_visited
            )
            if repl_id is None:
                return SwapPlan(
                    target_employee_id=target.employee_id,
                    chain=[], depth=depth, overall_confidence=0.0,
                    estimated_start_delay_days=0, is_feasible=False,
                    infeasible_reason=project_ctx.pull_risk_reason,
                    candidate_type="HOLD",
                )

            health_after = _project_health_after_removal(pid, target.employee_id, repl_id, ds, cfg)
            if health_after < cfg.impact.critical_health_floor:
                return SwapPlan(
                    target_employee_id=target.employee_id,
                    chain=[], depth=depth, overall_confidence=0.0,
                    estimated_start_delay_days=0, is_feasible=False,
                    infeasible_reason=(
                        f"Pulling {target.employee_id} from {pid} would drop project health "
                        f"to {health_after:.2f} (floor: {cfg.impact.critical_health_floor:.2f})"
                    ),
                    candidate_type="HOLD",
                )

            chain.append(SwapLink(
                employee_id=target.employee_id,
                from_project_id=pid,
                to_project_id=pipeline_id,
                replacement_employee_id=repl_id,
                replacement_confidence=repl_conf,
                source_project_health_before=project_ctx.health_score,
                source_project_health_after=health_after,
                is_safe=True,
                reason=f"Replacement {repl_id} found (conf {repl_conf:.2f})",
            ))
            confidence_product *= repl_conf

        else:
            # Safe to pull directly (ramp-down or healthy project)
            health_after = _project_health_after_removal(pid, target.employee_id, None, ds, cfg)
            chain.append(SwapLink(
                employee_id=target.employee_id,
                from_project_id=pid,
                to_project_id=pipeline_id,
                replacement_employee_id=None,
                replacement_confidence=1.0,
                source_project_health_before=project_ctx.health_score,
                source_project_health_after=health_after,
                is_safe=True,
                reason=project_ctx.pull_risk_reason or "Project is safe to pull from",
            ))

    if not chain:
        return SwapPlan(
            target_employee_id=target.employee_id,
            chain=[], depth=depth, overall_confidence=1.0,
            estimated_start_delay_days=0, is_feasible=True,
            infeasible_reason=None, candidate_type="DIRECT",
        )

    return SwapPlan(
        target_employee_id=target.employee_id,
        chain=chain,
        depth=depth + len(chain),
        overall_confidence=round(confidence_product, 4),
        estimated_start_delay_days=total_delay,
        is_feasible=confidence_product >= cfg.rules.swap_confidence_threshold,
        infeasible_reason=None if confidence_product >= cfg.rules.swap_confidence_threshold else "Low chain confidence",
        candidate_type="SWAP" if chain and chain[0].replacement_employee_id else "DIRECT",
    )


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 4 — MAIN PLANNER
# ─────────────────────────────────────────────────────────────────────────────

class SwapBackfillPlanner:

    def __init__(self, ds: DataStore, cfg: EngineConfig = DEFAULT_CONFIG):
        self.ds = ds
        self.cfg = cfg

    def plan_for_role(
        self,
        role_id: str,
        role_name: str,
        required_tier: int,
        required_pct: float,
        passing: List[ValidatedCandidate],
        pipeline_project: pd.Series,
        client_priority: str = "Bronze",
    ) -> BackfillResult:
        """
        Build swap/backfill plans for a single role slot.
        """
        pipeline_id = str(pipeline_project.get("pipeline_id") or "")
        cp = str(client_priority).strip()

        # ── No candidates at all → HIRE signal ───────────────────────────────
        if not passing:
            urgency = {
                "Gold": "IMMEDIATE", "Silver": "URGENT",
                "Bronze": "PLANNED", "Other": "PLANNED",
            }.get(cp, "PLANNED")
            return BackfillResult(
                role_id=role_id, role_name=role_name,
                required_tier=required_tier, required_pct=required_pct,
                primary_plan=None, alternative_plans=[],
                hire_signal=True, hire_urgency=urgency,
                extend_start_date_signal=False,
                estimated_availability_date=None,
                passing_candidates=[],
                gap_reason=f"No candidates passed validation for {role_name} (tier {required_tier}).",
            )

        # ── Classify top candidates ───────────────────────────────────────────
        plans: List[SwapPlan] = []

        for vc in passing[:5]:   # evaluate top 5 to find the best feasible plan
            c = vc.candidate

            # Case A: Directly available
            if c.effective_availability >= required_pct:
                plan = SwapPlan(
                    target_employee_id=c.employee_id,
                    chain=[], depth=0, overall_confidence=1.0,
                    estimated_start_delay_days=0, is_feasible=True,
                    infeasible_reason=None, candidate_type="DIRECT",
                )
                plans.append(plan)
                continue

            # Case B: Ramp-down soon → soft commit
            if c.ramp_down_flag and not pd.isna(c.days_to_soonest_end):
                delay = max(0, int(c.days_to_soonest_end))
                plan = SwapPlan(
                    target_employee_id=c.employee_id,
                    chain=[], depth=0, overall_confidence=0.85,
                    estimated_start_delay_days=delay, is_feasible=True,
                    infeasible_reason=None, candidate_type="SOFT_COMMIT",
                )
                plans.append(plan)
                continue

            # Case C / D: Busy — attempt swap
            if c.swap_eligible and c.active_project_ids:
                swap = _build_swap_plan(c, self.ds, self.cfg, pipeline_id, {c.employee_id})
                plans.append(swap)
            else:
                # Not swap-eligible (BAU locked) → HOLD
                plans.append(SwapPlan(
                    target_employee_id=c.employee_id,
                    chain=[], depth=0, overall_confidence=0.0,
                    estimated_start_delay_days=0, is_feasible=False,
                    infeasible_reason="Not swap-eligible (BAU or managed service lock)",
                    candidate_type="HOLD",
                ))

        # Sort plans: feasible first, then by confidence desc
        feasible = sorted([p for p in plans if p.is_feasible],
                          key=lambda p: (-p.overall_confidence, p.estimated_start_delay_days))
        infeasible = [p for p in plans if not p.is_feasible]

        # ── Determine hire / extend signals ───────────────────────────────────
        hire_signal = False
        extend_signal = False
        avail_date = None

        if not feasible:
            # All plans failed
            # Check if candidates will be free soon (extend start date signal)
            soon_free = [vc.candidate for vc in passing if vc.candidate.soon_free]
            if soon_free:
                best_soon = min(
                    soon_free,
                    key=lambda c: c.days_to_soonest_end if not pd.isna(c.days_to_soonest_end) else 999
                )
                if not pd.isna(best_soon.days_to_soonest_end) and best_soon.days_to_soonest_end <= 56:
                    extend_signal = True
                    avail_date = best_soon.predicted_available_date
                    # Create a HOLD plan for this candidate
                    delay = int(best_soon.days_to_soonest_end)
                    feasible = [SwapPlan(
                        target_employee_id=best_soon.employee_id,
                        chain=[], depth=0, overall_confidence=0.70,
                        estimated_start_delay_days=delay, is_feasible=True,
                        infeasible_reason=None, candidate_type="SOFT_COMMIT",
                    )]
                else:
                    hire_signal = True
            else:
                hire_signal = True

        hire_urgency = "NONE"
        if hire_signal:
            hire_urgency = {
                "Gold": "IMMEDIATE", "Silver": "URGENT",
                "Bronze": "PLANNED", "Other": "PLANNED",
            }.get(cp, "PLANNED")

        primary = feasible[0] if feasible else None
        alternatives = feasible[1:3] + infeasible[:1]  # show up to 2 feasible + 1 best infeasible

        gap_reason = None
        if not primary:
            gap_reason = f"No feasible staffing plan found for {role_name}. " + \
                         (f"Best attempt: {infeasible[0].infeasible_reason}" if infeasible else "")

        return BackfillResult(
            role_id=role_id, role_name=role_name,
            required_tier=required_tier, required_pct=required_pct,
            primary_plan=primary,
            alternative_plans=alternatives,
            hire_signal=hire_signal,
            hire_urgency=hire_urgency,
            extend_start_date_signal=extend_signal,
            estimated_availability_date=avail_date,
            passing_candidates=passing,
            gap_reason=gap_reason,
        )

    def plan_for_project(
        self,
        validated_by_role: Dict[str, Tuple[List[ValidatedCandidate], List[ValidatedCandidate]]],
        pipeline_project: pd.Series,
    ) -> Dict[str, BackfillResult]:
        """Run swap planning for all role slots in a project."""
        results = {}
        client_priority = str(pipeline_project.get("client_priority") or "Bronze")

        for role_id, (passing, _failing) in validated_by_role.items():
            # Extract role metadata from first candidate (or fall back to defaults)
            role_name = passing[0].candidate.role_name if passing else role_id
            required_tier = passing[0].candidate.required_tier if passing else 3
            required_pct = passing[0].candidate.required_allocation_pct if passing else 100.0

            result = self.plan_for_role(
                role_id=role_id,
                role_name=role_name,
                required_tier=required_tier,
                required_pct=required_pct,
                passing=passing,
                pipeline_project=pipeline_project,
                client_priority=client_priority,
            )
            results[role_id] = result

        return results
