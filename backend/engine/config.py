"""
config.py — All configurable weights, thresholds, and business rules.
Nothing in the recommendation pipeline should contain hardcoded numbers.
Every tunable parameter lives here.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# RETRIEVAL
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RetrievalConfig:
    top_k: int = 50                    # candidates retrieved per role slot
    min_similarity: float = 0.05       # discard below this cosine similarity
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    chroma_persist_dir: str = "./chroma_store"
    collection_name: str = "jspark_profiles"


# ─────────────────────────────────────────────────────────────────────────────
# BUSINESS RULES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BusinessRuleConfig:
    # Seniority & Roles compatibility hierarchy
    role_compatibility: Dict[str, List[str]] = field(default_factory=lambda: {
        "AP": ["AP", "P"],
        "P": ["P", "AP", "TA"],
        "TA": ["TA", "P"],
        "M": ["M", "AP", "P"],
        "AC": ["AC", "C"],
        "C": ["C", "AC", "SC"],
        "SC": ["SC", "C", "AC"],
        "SSE": ["SSE", "SE"],
        "SE": ["SE", "SSE", "ASE"],
        "ASE": ["ASE", "SE"],
        "DS": ["DS", "SSE", "SE"],
    })

    # Capacity
    max_allocation_pct: float = 100.0  # hard ceiling per employee
    min_available_for_role: float = 0.5  # candidate must have ≥50% of required capacity
                                          # (engine soft-scores the gap)
    partial_alloc_threshold: float = 25.0  # below this → "available" for small roles

    # Availability tiers
    busy_threshold: float = 80.0      # util_pct ≥ this = "busy"
    ramp_down_window_days: int = 28   # if project ends within N days = ramp-down candidate
    soon_free_window_days: int = 56   # softer: "coming free soon"

    # Seniority tier flexibility
    tier_flex: int = 1                # ±1 tier allowed by default
    tier_flex_urgent: int = 2         # ±2 tier allowed for urgent projects

    # ── 6-Level Recommendation Hierarchy Thresholds ──────────────────────────

    # Level 1 — Exact Match
    exact_match_skill_threshold: float = 0.85       # skill_confidence >= this
    exact_match_competency_threshold: float = 0.70  # competency_confidence >= this

    # Level 2 — Strong Partial Match
    strong_partial_skill_threshold: float = 0.65    # skill_confidence >= this

    # Level 3 — Transferable Skills
    transferable_semantic_threshold: float = 0.40   # semantic_score >= this
    transferable_domain_threshold: float = 0.60     # OR domain_experience_score >= this

    # Level 5 — Extend Start Date window
    extend_start_date_min_weeks: int = 2    # min delay to recommend (below this: just wait)
    extend_start_date_max_weeks: int = 8    # max delay before escalating to hire

    # Level 6 — Strategic hire gate: only recommend hiring for strategically important projects
    # Hire is triggered if client_priority in strategic_client_priorities OR sow_signed == True
    strategic_client_priorities: List[str] = field(default_factory=lambda: ["Gold", "Silver"])

    # Excluded project types (employees on ONLY these are excluded)
    excluded_project_types: List[str] = field(default_factory=lambda: [
        "BAU Activity", "Internal Project", "Sales Activity"
    ])

    # Geo routing rules (applied before scoring)
    # UK pipeline requests → prefer UK employees first
    # India pipeline requests → prefer India employees with COE match first
    uk_cluster_ids: List[int] = field(default_factory=lambda: [1, 2, 3, 4, 5])
    # All clusters are considered UK-first when client is UK-based
    # India: COE match within India pool first, then any India, then global

    # Swap depth limit (prevents infinite replacement chains)
    max_swap_depth: int = 3
    swap_confidence_threshold: float = 0.55

    # Project health gates for swap
    min_project_health_after_swap: float = 0.40  # do not pull if project drops below this
    extension_risk_no_pull: float = 0.60          # do not pull from projects with high ext risk

    # Managed services / BAU
    # Employees whose ONLY active allocations are BAU/Internal are not swap-eligible
    bau_only_excludes_from_swap: bool = True

    # Client-specific restrictions (extend as needed)
    restricted_clients: Dict[str, List[str]] = field(default_factory=dict)
    # e.g. {"CLIENT_999": ["EMP001"]}  — EMP001 must never leave CLIENT_999

    # ── Allocation Redistribution ─────────────────────────────────────────────
    # Maximum number of existing project allocations that may be adjusted
    # to free capacity for a new assignment (per candidate per role)
    max_redistribution_adjustments: int = 3

    # Minimum allocation % that must remain on any existing project after redistribution.
    # Never reduce below this floor (prevents project from losing all support).
    min_existing_project_allocation: float = 20.0



# ─────────────────────────────────────────────────────────────────────────────
# RANKING WEIGHTS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RankingWeights:
    """
    All weights must sum to 1.0 before adjustments.
    Signed weights (health_penalty) are subtractive.
    """
    semantic_similarity:  float = 0.20
    skill_confidence:     float = 0.18
    competency_confidence: float = 0.12
    availability:         float = 0.12
    domain_experience:    float = 0.10
    client_experience:    float = 0.08
    similar_project_exp:  float = 0.08
    utilization_fit:      float = 0.06
    location_preference:  float = 0.03
    coe_preference:       float = 0.03

    # Adjustments (additive bonuses / subtractive penalties — not part of the 1.0 sum)
    ramp_down_bonus:      float = 0.05
    health_penalty_max:   float = 0.15   # maximum penalty for red-project exposure
    swap_complexity_penalty: float = 0.08  # per hop in swap chain

    # Geo-routing boosts (on top of base score)
    same_geo_boost:       float = 0.05
    coe_match_boost:      float = 0.05
    cluster_match_boost:  float = 0.03

    # Redistribution effort penalty (applied per allocation adjustment made)
    # Discourages unnecessary disruption when two candidates are otherwise equal
    redistribution_effort_penalty: float = 0.04


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT IMPACT THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImpactConfig:
    # Reject a swap plan if it causes a project to lose this fraction of its team
    max_team_loss_fraction: float = 0.40  # losing >40% of team = rejected
    min_billability_after: float = 0.50   # <50% billable after swap = flag
    critical_health_floor: float = 0.35   # project health must not go below this

    # Revenue impact: daily rate per business role
    daily_rate_by_role: Dict[str, float] = field(default_factory=lambda: {
        "AP": 1500, "P": 1200, "TA": 1000, "M": 900, "SC": 800,
        "C": 700, "AC": 600, "SSE": 750, "SE": 600, "ASE": 450, "DS": 900
    })


    # Knowledge loss penalty (fraction of score): applies when key person leaves
    key_person_knowledge_penalty: float = 0.10


# ─────────────────────────────────────────────────────────────────────────────
# CLIENT PRIORITY
# ─────────────────────────────────────────────────────────────────────────────

CLIENT_PRIORITY_RANK: Dict[str, int] = {
    "Gold":   4,
    "Silver": 3,
    "Bronze": 2,
    "Other":  1,
}

REQUEST_PRIORITY_RANK: Dict[str, int] = {
    "Urgent":   4,
    "High":     3,
    "Medium":   2,
    "Low":      1,
    "Complete": 0,
}

# When composite_priority >= this, apply urgent tier_flex relaxation
URGENT_COMPOSITE_THRESHOLD: int = 34   # Gold(4)×10 + High(3) = 43 → Urgent = 44


# ─────────────────────────────────────────────────────────────────────────────
# LLM
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    provider: str = "ollama"
    model: str = "llama3.2:3b"
    api_base: str = "http://localhost:11434/v1"
    max_tokens: int = 2000
    temperature: float = 0.2
    timeout_seconds: int = 30


# ─────────────────────────────────────────────────────────────────────────────
# MASTER CONFIG
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EngineConfig:
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    rules: BusinessRuleConfig = field(default_factory=BusinessRuleConfig)
    weights: RankingWeights = field(default_factory=RankingWeights)
    impact: ImpactConfig = field(default_factory=ImpactConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    datacube_dir: str = "./datacubes"
    debug: bool = False


# Default singleton used by all modules unless overridden
DEFAULT_CONFIG = EngineConfig()


# ─────────────────────────────────────────────────────────────────────────────
# ROLE MAPPINGS & HELPERS
# ─────────────────────────────────────────────────────────────────────────────

JOB_TO_ROLE = {
    "Associate Partner": "AP",
    "Partner": "AP",
    "Leadership": "AP",
    "Principal": "P",
    "Principal Architect": "P",
    "Principal Technology Architect": "TA",
    "Technical Solutions Architect": "TA",
    "Technology Solutions Architect": "TA",
    "Technology Architect": "TA",
    "GTM Architect": "TA",
    "Manager": "M",
    "Engagement Manager": "M",
    "Consultant": "C",
    "Senior Consultant": "C",
    "Senior Solution Consultant": "SC",
    "Senior Solutions Consultant": "SC",
    "Solutions Consultant": "SC",
    "Solution Consultant": "SC",
    "Senior Software Engineer": "SSE",
    "Software Engineer": "SE",
    "Solutions Enabler": "ASE",
    "Senior Associate Consultant": "AC",
    "Associate Consultant": "AC",
    "Senior Data Science SME": "DS",
}

ABBR_TO_ROLE = {
    "AP": "AP",
    "AP/P": "AP",
    "P": "P",
    "PA": "P",
    "TA": "TA",
    "GTM Architect": "TA",
    "M": "M",
    "EM": "M",
    "SC (EM)": "M",
    "SC or C - EM": "M",
    "AC": "AC",
    "AC (UK)": "AC",
    "SAC": "AC",
    "SAC/AC": "AC",
    "SAC or AC": "AC",
    "C": "C",
    "SAC - C": "C",
    "SAC - Consultant": "C",
    "C/SAC/AC": "C",
    "SC": "SC",
    "Sol Con": "SC",
    "Sr Sol Con": "SC",
    "Snr Sol Con": "SC",
    "SSE": "SSE",
    "SSE  or SE": "SSE",
    "SE": "SE",
    "Enabler": "ASE",
    "ASE": "ASE",
    "Sr DS SME": "DS",
    "DS": "DS",
}

def standardize_role(raw: str) -> str:
    if not raw or not isinstance(raw, str):
        return "C"  # Default Consultant
    raw = raw.strip()
    if raw in ABBR_TO_ROLE:
        return ABBR_TO_ROLE[raw]
    if raw in JOB_TO_ROLE:
        return JOB_TO_ROLE[raw]
    # Substring checks
    raw_lower = raw.lower()
    if "partner" in raw_lower:
        return "AP"
    if "principal" in raw_lower:
        return "P"
    if "architect" in raw_lower:
        return "TA"
    if "manager" in raw_lower or "em" == raw_lower or "em " in raw_lower:
        return "M"
    if "consultant" in raw_lower:
        if "associate" in raw_lower:
            return "AC"
        if "solutions" in raw_lower or "solution" in raw_lower:
            return "SC"
        return "C"
    if "engineer" in raw_lower:
        if "senior" in raw_lower:
            return "SSE"
        return "SE"
    if "enabler" in raw_lower:
        return "ASE"
    if "data science" in raw_lower or "ds" in raw_lower:
        return "DS"
    return "C"

