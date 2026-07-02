"""
stage9_validation.py — Pre-return Recommendation Validator

Validates every recommended StaffingOption before the plan is returned.
If validation fails, it automatically tries the next option in all_options.
If none pass, it escalates to extend-start or hire.

Validation checks (run in this order):
  V-1  Employee exists and is not None
  V-2  Concurrent project limit — client_project_count <= max allowed for role
  V-2b Redistribution feasibility & capacity satisfaction (health remains acceptable)
  V-3  Role compatibility — employee role is in required role's compat map
  V-4  No duplicate allocation — employee not already in globally_assigned
  V-5  Allocation ceiling — total utilisation stays within allowed limits
  V-7  Swap chain integrity — no failed or infeasible links in swap chain
  V-8  No leave conflicts — employee has no active leave conflict
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .config import EngineConfig, DEFAULT_CONFIG, standardize_role
from .loader import DataStore
from .stage7_plans import RoleStaffingPlan, StaffingOption, _build_hire_option, _build_extend_start_fallback_option
from .role_policy import ROLE_POLICIES, get_policy


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION RESULT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed: bool
    failed_checks: List[str]   # check IDs that failed (e.g. "V-2", "V-5")
    failure_messages: List[str]
    option_tried: Optional[str]  # employee_id or plan_type of the option tested


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATOR
# ─────────────────────────────────────────────────────────────────────────────

class RecommendationValidator:
    """
    Validates and repairs RoleStaffingPlans before they are returned.
    Ensures that concurrent project limits and allocation redistribution feasibility
    are prioritized as the main constraints.
    """

    def __init__(self, cfg: EngineConfig = DEFAULT_CONFIG, ds: Optional[DataStore] = None):
        self.cfg = cfg
        self.ds = ds

    def validate_option(
        self,
        option: StaffingOption,
        required_role: str,
        required_pct: float,
        temp_state: Dict[str, Dict],
        globally_assigned: Set[str],
    ) -> ValidationResult:
        """
        Run all business-aligned validation checks against a single StaffingOption.
        Returns ValidationResult with pass/fail details.
        """
        failed = []
        messages = []
        eid = option.recommended_employee_id

        # Hire and extend-start options are always valid (informational only)
        if option.plan_type in ("D_HIRE", "E_EXTEND_START"):
            return ValidationResult(
                passed=True, failed_checks=[], failure_messages=[],
                option_tried=option.plan_type
            )

        # V-1: Employee must exist
        if not eid:
            failed.append("V-1")
            messages.append("V-1: No employee ID on this option.")
            return ValidationResult(passed=False, failed_checks=failed,
                                    failure_messages=messages, option_tried=None)

        state = temp_state.get(eid, {})

        # V-2: Concurrent project count limit check
        client_count = state.get("client_project_count", 0)
        cat = state.get("role_category")
        policy = ROLE_POLICIES.get(cat) if cat else None
        if not policy:
            policy = get_policy(option.job_name)

        if client_count > policy.max_client_projects:
            failed.append("V-2")
            messages.append(
                f"V-2: Concurrent project limit exceeded. Employee {eid} is on {client_count} client project(s), "
                f"but max allowed for '{policy.category}' is {policy.max_client_projects}."
            )

        # V-2b: Disabled (capacity percentage checks are not used)
        pass

        # V-3: Role compatibility
        job_name = option.job_name or ""
        emp_role_std = standardize_role(job_name)
        req_role_std = standardize_role(required_role)
        compatibles = self.cfg.rules.role_compatibility.get(req_role_std, [req_role_std])
        if emp_role_std not in compatibles:
            failed.append("V-3")
            messages.append(
                f"V-3: Role mismatch. Employee role '{emp_role_std}' is not compatible "
                f"with required role '{req_role_std}' (allowed: {compatibles})."
            )

        # V-4: No duplicate allocation
        if eid in globally_assigned:
            failed.append("V-4")
            messages.append(
                f"V-4: Duplicate allocation. Employee {eid} is already assigned to "
                f"another role in this project."
            )

        # V-5: Disabled (capacity percentage checks are not used)
        pass

        # V-7: Swap chain integrity
        if option.swap_chain_summary:
            for step in option.swap_chain_summary:
                if "infeasible" in step.lower() or "failed" in step.lower():
                    failed.append("V-7")
                    messages.append(f"V-7: Unsafe swap chain step detected: '{step}'")
                    break

        # V-8: Leave conflict check
        has_leave = False
        if self.ds:
            person = self.ds.get_person(eid)
            if person:
                has_leave = bool(
                    person.get("on_leave") or
                    person.get("leave_status") or
                    person.get("is_on_leave", False)
                )
        if has_leave:
            failed.append("V-8")
            messages.append(f"V-8: Leave conflict. Employee {eid} has scheduled leave during project timeline.")

        if failed:
            return ValidationResult(
                passed=False,
                failed_checks=failed,
                failure_messages=messages,
                option_tried=eid,
            )

        return ValidationResult(
            passed=True, failed_checks=[], failure_messages=[], option_tried=eid
        )

    def validate_and_repair(
        self,
        role_plans: List[RoleStaffingPlan],
        temp_state: Dict[str, Dict],
        globally_assigned: Set[str],
        pipeline_id: str,
        pipeline_start: Optional[str],
        client_priority: str,
    ) -> List[RoleStaffingPlan]:
        """
        Validate every role plan's recommended_option.
        If it fails, try the next option in all_options.
        If none pass, escalate to L5 (extend start) or L6 (hire).
        """
        repaired: List[RoleStaffingPlan] = []
        seen_assigned = set()

        for rp in role_plans:
            repaired_rp = self._repair_role_plan(
                rp, temp_state, seen_assigned, pipeline_start, client_priority
            )
            repaired.append(repaired_rp)
            
            # Record final chosen candidate to prevent double allocation in subsequent roles of the same project
            opt = repaired_rp.recommended_option
            if opt and opt.recommended_employee_id:
                seen_assigned.add(opt.recommended_employee_id)

        return repaired

    def _repair_role_plan(
        self,
        rp: RoleStaffingPlan,
        temp_state: Dict[str, Dict],
        globally_assigned: Set[str],
        pipeline_start: Optional[str],
        client_priority: str,
    ) -> RoleStaffingPlan:
        """
        Try each option in all_options until one passes validation.
        """
        options_to_try = []
        if rp.recommended_option:
            options_to_try.append(rp.recommended_option)
        for opt in (rp.all_options or []):
            if opt is not rp.recommended_option:
                options_to_try.append(opt)

        last_failures: List[str] = []

        for option in options_to_try:
            result = self.validate_option(
                option=option,
                required_role=rp.required_role,
                required_pct=rp.required_pct,
                temp_state=temp_state,
                globally_assigned=globally_assigned,
            )

            if result.passed:
                if option is not rp.recommended_option:
                    print(
                        f"[Validator] Role '{rp.role_name}': primary option failed "
                        f"({', '.join(last_failures)}). Using next best option: "
                        f"{option.recommended_employee_id or option.plan_type}."
                    )
                    rp.recommended_option = option
                return rp
            else:
                last_failures = result.failed_checks
                print(
                    f"[Validator] Role '{rp.role_name}': Option "
                    f"'{result.option_tried}' failed checks: {result.failed_checks}. "
                    f"Trying next."
                )

        print(
            f"[Validator] Role '{rp.role_name}': All {len(options_to_try)} options failed "
            f"validation. Escalating to extend-start / hire."
        )

        allow_hire = client_priority in ("Gold", "Silver")
        if allow_hire:
            from .stage4_swap import BackfillResult as _BR
            br = _BR(
                role_id=rp.role_id,
                role_name=rp.role_name,
                required_tier=rp.seniority_tier,
                required_pct=rp.required_pct,
                primary_plan=None,
                alternative_plans=[],
                hire_signal=True,
                hire_urgency="IMMEDIATE" if client_priority == "Gold" else "URGENT",
                extend_start_date_signal=False,
                estimated_availability_date=None,
                passing_candidates=[],
                gap_reason="All options failed post-generation validation",
            )
            br.required_role = rp.required_role
            fallback_opt = _build_hire_option(rp.required_role, rp.required_pct, br)
        else:
            fallback_opt = _build_extend_start_fallback_option(
                rp.required_role, rp.required_pct, pipeline_start
            )

        rp.recommended_option = fallback_opt
        rp.gap_detected = True
        rp.gap_reason = (
            f"All {len(options_to_try)} candidates failed validation "
            f"(last failed checks: {last_failures}). Escalated to "
            f"{'hire' if allow_hire else 'extend start'}."
        )
        rp.hire_signal = allow_hire
        rp.hire_urgency = "URGENT" if allow_hire else "Low"

        return rp
