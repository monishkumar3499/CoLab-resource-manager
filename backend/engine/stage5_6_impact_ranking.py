"""
stage5_impact.py — Project Impact Simulator
stage6_ranking.py — Multi-objective Ranking Engine

Kept in one file to reduce folder count (they share data structures).

Stage 5: Simulate the business impact of each staffing plan before it is accepted.
Stage 6: Combine all signals into a final composite score and produce a ranked list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG, RankingWeights
from .loader import DataStore
from .stage2_intelligence import EnrichedCandidate
from .stage3_rules import ValidatedCandidate
from .stage4_swap import BackfillResult, SwapPlan


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 5 — IMPACT SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImpactEstimate:
    employee_id: str
    plan_type: str                     # DIRECT / SWAP / SOFT_COMMIT / HOLD / HIRE
    # Project-level impact
    source_project_health_delta: float  # change in source project health (negative = degraded)
    source_project_risk_increase: float
    team_loss_fraction: float           # fraction of source team being pulled
    knowledge_loss_score: float         # 0-1 (higher = more critical knowledge risk)
    # Financial
    daily_rate: float
    weekly_revenue_contribution: float
    revenue_at_risk_weekly: float       # if plan fails and hire is needed
    # Delivery
    estimated_start_delay_days: int
    client_impact: str                  # "None" | "Low" | "Medium" | "High"
    delivery_risk_change: float         # delta in delivery risk score
    # Resource
    utilization_change_pct: float       # how utilization changes for source project team
    # Overall verdict
    acceptable: bool
    rejection_reason: Optional[str]


def simulate_impact(
    candidate: EnrichedCandidate,
    plan: SwapPlan,
    ds: DataStore,
    cfg: EngineConfig,
) -> ImpactEstimate:
    """
    Simulate the business impact of assigning this candidate via this plan.
    """
    from .config import standardize_role
    role_std = standardize_role(candidate.job_name)
    daily_rate = cfg.impact.daily_rate_by_role.get(role_std, 600)
    weekly_rev = daily_rate * 5

    # Defaults for DIRECT / no-swap plans
    source_health_delta = 0.0
    risk_increase = 0.0
    team_loss = 0.0
    knowledge_loss = 0.0
    revenue_at_risk = 0.0
    delay = plan.estimated_start_delay_days
    util_change = 0.0

    for link in plan.chain:
        p = ds.get_project(link.from_project_id)
        if not p:
            continue
        health_before = float(p.get("health_score") or 0.65)
        health_after = link.source_project_health_after
        delta = health_after - health_before
        source_health_delta = min(source_health_delta, delta)  # keep worst delta
        team_size = max(1, int(p.get("total_slots") or 1))
        loss_frac = 1.0 / team_size
        team_loss = max(team_loss, loss_frac)
        # Knowledge loss: higher for senior employees on small teams
        kl = (candidate.seniority_tier / 8.0) * (1.0 - min(1.0, team_size / 5.0))
        knowledge_loss = max(knowledge_loss, kl)
        # Utilization change for remaining team
        util_change += loss_frac * 25.0  # rough: 25% more work for remaining team per person lost

    # Extension risk: if source project already has high extension risk, losing a person worsens it
    if candidate.on_red_project or candidate.on_amber_project:
        risk_increase = 0.15
    elif plan.chain:
        risk_increase = 0.05

    # Revenue at risk: if plan fails and we need to hire
    if plan.candidate_type == "HIRE":
        revenue_at_risk = weekly_rev * 4  # 4 weeks to hire = lost 4 weeks revenue

    # Client impact
    if source_health_delta < -0.2 or team_loss > 0.4:
        client_impact = "High"
    elif source_health_delta < -0.1 or team_loss > 0.25:
        client_impact = "Medium"
    elif source_health_delta < -0.05:
        client_impact = "Low"
    else:
        client_impact = "None"

    # Delivery risk change
    delivery_risk_delta = -source_health_delta * 0.5 + risk_increase

    # Acceptability gate
    acceptable = True
    rejection_reason = None
    if team_loss > cfg.impact.max_team_loss_fraction:
        acceptable = False
        rejection_reason = f"Team loss {team_loss:.0%} exceeds maximum {cfg.impact.max_team_loss_fraction:.0%}"
    elif health_after := (0.65 + source_health_delta):
        if health_after < cfg.impact.critical_health_floor:
            acceptable = False
            rejection_reason = f"Source project health would drop to {health_after:.2f}"

    return ImpactEstimate(
        employee_id=candidate.employee_id,
        plan_type=plan.candidate_type,
        source_project_health_delta=round(source_health_delta, 3),
        source_project_risk_increase=round(risk_increase, 3),
        team_loss_fraction=round(team_loss, 3),
        knowledge_loss_score=round(knowledge_loss, 3),
        daily_rate=daily_rate,
        weekly_revenue_contribution=round(weekly_rev, 2),
        revenue_at_risk_weekly=round(revenue_at_risk, 2),
        estimated_start_delay_days=delay,
        client_impact=client_impact,
        delivery_risk_change=round(delivery_risk_delta, 3),
        utilization_change_pct=round(util_change, 1),
        acceptable=acceptable,
        rejection_reason=rejection_reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 6 — MULTI-OBJECTIVE RANKING
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RankedCandidate:
    rank: int
    employee_id: str
    composite_score: float
    confidence_band: str           # HIGH / MEDIUM / LOW
    score_components: Dict[str, float]  # {signal_name: contribution}
    validated: ValidatedCandidate
    plan: SwapPlan
    impact: ImpactEstimate
    # Pre-computed for display
    job_name: str
    location: str
    geo_cluster: str
    seniority_tier: int
    available_capacity_pct: float
    primary_coe: str
    avg_skill_score: float
    top_skills_display: List[str]
    role_name: str
    required_tier: int
    required_pct: float


def _score_candidate(
    vc: ValidatedCandidate,
    plan: SwapPlan,
    impact: ImpactEstimate,
    cfg: EngineConfig,
) -> Tuple[float, Dict[str, float]]:
    """
    Compute composite score for a candidate using Stage 5 and Stage 6 logic.
    Ranking Score (Stage 5) = 80% Capability (Semantic, Skill, Comp) + 20% Operational (Avail, Domain, Client, Util)
    Final Score (Stage 6) = Ranking Score + Business Bonuses - Business Penalties
    """
    w = cfg.weights
    c = vc.candidate

    # ── STAGE 5: Multi-Objective Ranking ──────────────────────────────────────
    
    # 1. Capability components (dominate the score)
    sem = c.semantic_score
    skill = c.skill_confidence
    comp = c.competency_confidence

    cap_score = (
        w.semantic_similarity  * sem
        + w.skill_confidence     * skill
        + w.competency_confidence * comp
    )
    sum_cap = w.semantic_similarity + w.skill_confidence + w.competency_confidence
    norm_cap = cap_score / sum_cap if sum_cap > 0 else 0.0

    # 2. Operational components (secondary)
    req_pct = c.required_allocation_pct
    avail_score = min(1.0, c.effective_availability / max(req_pct, 1.0))
    domain = c.domain_experience_score
    client_exp = c.client_experience_score
    sim_proj = min(1.0, c.similar_project_count / 5.0)

    util = c.util_pct
    if util <= 50:
        util_fit = util / 50.0
    elif util <= 75:
        util_fit = 1.0
    elif util <= 100:
        util_fit = 1.0 - (util - 75) / 25.0 * 0.3
    else:
        util_fit = 0.0
    util_fit = max(0.0, util_fit)

    # 3. Location/COE preference routing scores from validation
    loc_pref = vc.geo_routing_score
    coe_pref = vc.coe_routing_score

    ops_score = (
        w.availability         * avail_score
        + w.domain_experience    * domain
        + w.client_experience    * client_exp
        + w.similar_project_exp  * sim_proj
        + w.utilization_fit      * util_fit
        + w.location_preference  * loc_pref
        + w.coe_preference       * coe_pref
    )
    sum_ops = w.availability + w.domain_experience + w.client_experience + w.similar_project_exp + w.utilization_fit + w.location_preference + w.coe_preference
    norm_ops = ops_score / sum_ops if sum_ops > 0 else 0.0

    # Final Ranking Score (Stage 5)
    ranking_score = 0.80 * norm_cap + 0.20 * norm_ops

    # ── STAGE 6: Business Optimizer (Adjustments) ─────────────────────────────
    
    # Positive adjustments (Bonuses)
    geo_bonus = w.same_geo_boost if loc_pref >= 0.8 else 0.0
    coe_bonus = w.coe_match_boost if coe_pref >= 0.8 else 0.0
    cluster_bonus = w.cluster_match_boost if loc_pref >= 0.5 else 0.0
    ramp_down_bonus = w.ramp_down_bonus if c.ramp_down_flag else 0.0

    total_bonuses = geo_bonus + coe_bonus + cluster_bonus + ramp_down_bonus

    # Negative adjustments (Penalties)
    health_penalty = c.health_penalty
    swap_penalty = w.swap_complexity_penalty * plan.depth if plan.depth > 0 else 0.0
    
    delivery_penalty = 0.0
    if plan.estimated_start_delay_days > 0:
        delay_weeks = plan.estimated_start_delay_days / 7.0
        delivery_penalty += min(0.15, delay_weeks * 0.01)
    if impact.client_impact == "High":
        delivery_penalty += 0.05
    if not impact.acceptable:
        delivery_penalty += 0.20

    total_penalties = health_penalty + swap_penalty + delivery_penalty

    # Final Recommendation Score
    composite = max(0.0, min(1.0, ranking_score + total_bonuses - total_penalties))

    components = {
        "ranking_score":       round(ranking_score, 4),
        "capability_score":    round(norm_cap, 4),
        "operational_score":   round(norm_ops, 4),
        "business_bonuses":    round(total_bonuses, 4),
        "business_penalties":  round(total_penalties, 4),
        "composite":           round(composite, 4),
        "semantic_similarity": round(c.semantic_score, 4),
        "skill_confidence":    round(c.skill_confidence, 4),
        "coe_match":           round(coe_pref, 4),
        "location":            round(loc_pref, 4),
    }
    return composite, components


def _confidence_band(score: float) -> str:
    if score >= 0.72:
        return "HIGH"
    elif score >= 0.50:
        return "MEDIUM"
    return "LOW"


class MultiObjectiveRanker:

    def __init__(self, ds: DataStore, cfg: EngineConfig = DEFAULT_CONFIG):
        self.ds = ds
        self.cfg = cfg

    def rank(
        self,
        backfill: BackfillResult,
        pipeline_project: pd.Series,
    ) -> List[RankedCandidate]:
        """
        Score and rank all passing candidates for a role.
        Returns a sorted list of RankedCandidate (best first).
        """
        passing = backfill.passing_candidates
        if not passing:
            return []

        # Build (vc, plan, impact) triples
        scored: List[Tuple[float, Dict, ValidatedCandidate, SwapPlan, ImpactEstimate]] = []

        for vc in passing:
            # Find corresponding plan (match by employee_id)
            plan = None
            if backfill.primary_plan and backfill.primary_plan.target_employee_id == vc.candidate.employee_id:
                plan = backfill.primary_plan
            else:
                for alt in backfill.alternative_plans:
                    if alt.target_employee_id == vc.candidate.employee_id:
                        plan = alt
                        break
            if plan is None:
                # No plan found for this candidate → create a minimal direct plan
                avail = vc.candidate.effective_availability
                req = vc.candidate.required_allocation_pct
                plan = SwapPlan(
                    target_employee_id=vc.candidate.employee_id,
                    chain=[], depth=0,
                    overall_confidence=1.0 if avail >= req else 0.6,
                    estimated_start_delay_days=0, is_feasible=avail >= req * 0.5,
                    infeasible_reason=None if avail >= req * 0.5 else "Insufficient capacity",
                    candidate_type="DIRECT" if avail >= req else "HOLD",
                )

            impact = simulate_impact(vc.candidate, plan, self.ds, self.cfg)
            composite, components = _score_candidate(vc, plan, impact, self.cfg)
            scored.append((composite, components, vc, plan, impact))

        # Sort descending by composite score
        scored.sort(key=lambda x: x[0], reverse=True)

        # Build output list
        result = []
        for rank_idx, (composite, components, vc, plan, impact) in enumerate(scored):
            c = vc.candidate
            top_skills_display = [
                f"{s.get('SubSkill') or s.get('Skill', '')} ({s.get('Score', 0)}/5)"
                for s in (c.top_skills or [])[:5]
            ]
            result.append(RankedCandidate(
                rank=rank_idx + 1,
                employee_id=c.employee_id,
                composite_score=composite,
                confidence_band=_confidence_band(composite),
                score_components=components,
                validated=vc,
                plan=plan,
                impact=impact,
                job_name=c.job_name,
                location=c.location,
                geo_cluster=c.geo_cluster,
                seniority_tier=c.seniority_tier,
                available_capacity_pct=c.effective_availability,
                primary_coe=c.primary_coe,
                avg_skill_score=c.avg_skill_score,
                top_skills_display=top_skills_display,
                role_name=c.role_name,
                required_tier=c.required_tier,
                required_pct=c.required_allocation_pct,
            ))

        return result
