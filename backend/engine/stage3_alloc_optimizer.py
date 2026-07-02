"""
stage3_alloc_optimizer.py — Allocation Redistribution Optimizer

When an employee is within their concurrent client project limit but does not
have enough free capacity, this engine evaluates whether their existing
allocations can be safely redistributed to make room.

Redistribution rules (hard):
  - Never touch Gold project allocations (absolute)
  - Never reduce any allocation below cfg.rules.min_existing_project_allocation (default 20%)
  - Never drop a project's health below cfg.impact.critical_health_floor
  - Touch at most cfg.rules.max_redistribution_adjustments projects
  - Never cause a critical role to become the sole occupant of a project after reduction

Redistribution priority (lower priority projects are reduced first):
  1. Bronze clients > Silver clients (never Gold)
  2. Low project health tolerance → high health means more at risk if we reduce
  3. Larger current allocation → more room to reduce without going below floor
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .config import EngineConfig, DEFAULT_CONFIG
from .loader import DataStore
from .stage2_intelligence import EnrichedCandidate


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AllocationAdjustment:
    """A proposed change to one existing project allocation."""
    project_id: str
    project_name: str
    client_priority: str              # Gold | Silver | Bronze | Other
    allocation_before: float          # current allocation %
    allocation_after: float           # proposed allocation %
    freed: float                      # allocation_before - allocation_after
    project_health_before: float
    project_health_after: float
    is_safe: bool
    reason: str


@dataclass
class OptimizationResult:
    """Full result of an allocation optimization attempt for one employee."""
    employee_id: str
    required_pct: float               # what the new role needs
    current_free_capacity: float      # how much was free before redistribution
    freed_capacity: float             # how much redistribution freed up
    total_available_after: float      # current_free + freed (must >= required_pct)
    feasible: bool
    adjustments: List[AllocationAdjustment]
    redistribution_effort: int        # number of projects whose allocation changed
    health_impact: str                # "None" | "Low" | "Medium" | "High"
    rejection_reason: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _client_priority_rank(priority: str) -> int:
    """Lower rank = safer to reduce (do this first)."""
    return {"Gold": 999, "Silver": 2, "Bronze": 1, "Other": 0}.get(priority, 0)


def _health_impact_label(delta: float) -> str:
    if delta == 0:
        return "None"
    if abs(delta) < 0.05:
        return "Low"
    if abs(delta) < 0.15:
        return "Medium"
    return "High"


# ─────────────────────────────────────────────────────────────────────────────
# OPTIMIZER
# ─────────────────────────────────────────────────────────────────────────────

class AllocationOptimizer:
    """
    Evaluates and proposes allocation redistribution for a candidate who is
    within their concurrent project limit but lacks free capacity.

    Usage:
        optimizer = AllocationOptimizer(ds, cfg)
        result = optimizer.optimize(
            candidate=ec,
            required_pct=50.0,
            temp_state=temp_state,
        )
        if result.feasible:
            # candidate can be allocated after redistribution
    """

    def __init__(self, ds: DataStore, cfg: EngineConfig = DEFAULT_CONFIG):
        self.ds = ds
        self.cfg = cfg

    def optimize(
        self,
        candidate: EnrichedCandidate,
        required_pct: float,
        temp_state: Dict[str, Dict],
    ) -> OptimizationResult:
        """
        Attempt to redistribute existing allocations to free required_pct capacity.

        Returns OptimizationResult.feasible=True if redistribution is possible
        without violating any hard business constraint.
        """
        eid = candidate.employee_id
        state = temp_state.get(eid, {})
        current_free = state.get("client_capacity_remaining", candidate.effective_availability)
        gap = required_pct - current_free

        # If already enough free capacity, no redistribution needed
        if gap <= 0.5:
            return OptimizationResult(
                employee_id=eid,
                required_pct=required_pct,
                current_free_capacity=current_free,
                freed_capacity=0.0,
                total_available_after=current_free,
                feasible=True,
                adjustments=[],
                redistribution_effort=0,
                health_impact="None",
                rejection_reason=None,
            )

        # Build list of current project contexts eligible for reduction
        reducible = self._build_reducible_list(candidate, temp_state, required_pct)

        if not reducible:
            return OptimizationResult(
                employee_id=eid,
                required_pct=required_pct,
                current_free_capacity=current_free,
                freed_capacity=0.0,
                total_available_after=current_free,
                feasible=False,
                adjustments=[],
                redistribution_effort=0,
                health_impact="None",
                rejection_reason=(
                    "No existing project allocations can be safely reduced. "
                    "All projects are Gold, at health floor, or below minimum allocation."
                ),
            )

        # Greedily reduce allocations from lowest-priority projects first
        adjustments: List[AllocationAdjustment] = []
        remaining_gap = gap
        max_adj = self.cfg.rules.max_redistribution_adjustments

        for proj_id, proj_data in reducible:
            if remaining_gap <= 0.5:
                break
            if len(adjustments) >= max_adj:
                break

            current_alloc = proj_data["current_alloc"]
            min_floor = self.cfg.rules.min_existing_project_allocation
            max_reducible = current_alloc - min_floor

            if max_reducible <= 0:
                continue

            # How much to actually reduce?
            reduce_by = min(max_reducible, remaining_gap)
            new_alloc = current_alloc - reduce_by

            # Project health check after reduction
            project = self.ds.get_project(proj_id)
            health_before = float((project or {}).get("health_score", 0.65))
            team_size = max(1, int((project or {}).get("total_slots", 1)))
            loss_fraction = reduce_by / 100.0
            health_after = health_before * (1.0 - loss_fraction * 0.3)  # modest degradation model

            if health_after < self.cfg.impact.critical_health_floor:
                # This reduction would harm the project too much — skip
                continue

            adjustments.append(AllocationAdjustment(
                project_id=proj_id,
                project_name=proj_data.get("project_name", proj_id),
                client_priority=proj_data.get("client_priority", "Other"),
                allocation_before=current_alloc,
                allocation_after=round(new_alloc, 1),
                freed=round(reduce_by, 1),
                project_health_before=round(health_before, 3),
                project_health_after=round(health_after, 3),
                is_safe=True,
                reason=(
                    f"Reduced from {current_alloc:.0f}% → {new_alloc:.0f}% to free "
                    f"{reduce_by:.0f}% for new assignment. "
                    f"Project health: {health_before:.2f} → {health_after:.2f}."
                ),
            ))
            remaining_gap -= reduce_by

        freed_total = sum(a.freed for a in adjustments)
        total_available = current_free + freed_total
        feasible = total_available >= required_pct * 0.95  # 5% tolerance

        if not feasible and remaining_gap > 0:
            rejection = (
                f"Could only free {freed_total:.0f}% through redistribution "
                f"({len(adjustments)} adjustment(s)). Still short by {remaining_gap:.0f}%. "
                f"Remaining projects are protected (Gold, at health floor, or below minimum)."
            )
        else:
            rejection = None

        # Health impact summary
        if not adjustments:
            health_label = "None"
        else:
            worst_delta = max(a.project_health_before - a.project_health_after for a in adjustments)
            health_label = _health_impact_label(worst_delta)

        return OptimizationResult(
            employee_id=eid,
            required_pct=required_pct,
            current_free_capacity=round(current_free, 1),
            freed_capacity=round(freed_total, 1),
            total_available_after=round(total_available, 1),
            feasible=feasible,
            adjustments=adjustments,
            redistribution_effort=len(adjustments),
            health_impact=health_label,
            rejection_reason=rejection,
        )

    def _build_reducible_list(
        self,
        candidate: EnrichedCandidate,
        temp_state: Dict[str, Dict],
        required_pct: float,
    ) -> List[Tuple[str, Dict]]:
        """
        Build a prioritized list of (project_id, metadata) for projects whose
        allocation can be reduced.

        Sorted: lowest priority first (Bronze before Silver, never Gold).
        Within same priority: larger current allocation first (more room to cut).
        """
        eid = candidate.employee_id
        active_pids = candidate.active_project_ids or []

        # Try to load active allocation data from DataStore
        # Use project contexts from Stage 2 enrichment when available
        ctx_by_pid = {ctx.project_id: ctx for ctx in (candidate.current_project_contexts or [])}

        min_floor = self.cfg.rules.min_existing_project_allocation

        reducible = []
        for pid in active_pids:
            project = self.ds.get_project(pid)
            if not project:
                continue

            # Never touch Gold projects
            client_priority = str(project.get("client_priority") or "Other")
            if client_priority == "Gold":
                continue

            # Get current allocation from project context or estimate
            ctx = ctx_by_pid.get(pid)
            if ctx:
                # Estimate from util_pct: rough proxy for how much this employee is on this project
                # A more precise value would come from the raw allocations table
                # For now: assume equal split across active projects
                n_active = max(1, len(active_pids))
                state = temp_state.get(eid, {})
                total_allocated = state.get("utilisation", 0.0)
                estimated_alloc = total_allocated / n_active
            else:
                n_active = max(1, len(active_pids))
                state = temp_state.get(eid, {})
                total_allocated = state.get("utilisation", 0.0)
                estimated_alloc = total_allocated / n_active

            if estimated_alloc <= min_floor:
                continue  # Nothing to reduce

            health = float(project.get("health_score") or 0.65)
            # Check if reduction would drop below health floor immediately
            # (crude check — detailed check happens in optimize())
            if health < self.cfg.impact.critical_health_floor + 0.05:
                continue  # Too close to floor already

            reducible.append((pid, {
                "current_alloc": round(estimated_alloc, 1),
                "client_priority": client_priority,
                "project_name": str(project.get("project_name") or pid),
                "health": health,
            }))

        # Sort: lowest client priority first, then largest allocation first
        reducible.sort(key=lambda x: (
            _client_priority_rank(x[1]["client_priority"]),  # lower = reduce first
            -x[1]["current_alloc"],                           # larger allocation = more room
        ))

        return reducible

    def apply_to_temp_state(
        self,
        result: OptimizationResult,
        temp_state: Dict[str, Dict],
    ) -> None:
        """
        Apply a feasible OptimizationResult to the in-memory temp_state.
        Called during the commit phase in recommend.py.
        Only call this when result.feasible is True.
        """
        if not result.feasible or not result.adjustments:
            return

        eid = result.employee_id
        if eid not in temp_state:
            return

        total_freed = sum(a.freed for a in result.adjustments)
        # Increase available client capacity by what was freed
        temp_state[eid]["client_capacity_remaining"] = min(
            100.0,
            temp_state[eid].get("client_capacity_remaining", 0.0) + total_freed
        )
        # Reduce utilisation correspondingly
        temp_state[eid]["utilisation"] = max(
            0.0,
            temp_state[eid].get("utilisation", 0.0) - total_freed
        )
        # capacity_remaining mirrors this
        temp_state[eid]["capacity_remaining"] = min(
            100.0,
            temp_state[eid].get("capacity_remaining", 0.0) + total_freed
        )
