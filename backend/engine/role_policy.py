"""
role_policy.py — Role-Based Allocation Policy Engine

Loads the Hierarchy worksheet from the Pipeline Details workbook and maps
every India and UK/US job title to an internal role category.

Each role category carries an AllocationPolicy that defines:
  - max_client_projects: maximum number of concurrent CLIENT project assignments
  - max_client_allocation_pct: maximum total billable allocation (always 100%)

Internal projects (CoE, R&D, Innovation, Training, BAU, etc.) NEVER consume
client capacity and are never counted toward concurrent project limits.

Role Category Hierarchy (derived from Hierarchy sheet row order):
  Row 0 -> SE       — Software Engineer / Associate Consultant       — max 1 client project
  Row 1 -> SSE      — Senior SE / Senior Associate Consultant        — max 1 client project
  Row 2 -> ENABLER  — Solutions Enabler / Consultant                 — max 2 client projects
  Row 3 -> SC       — Solution Consultant / Senior Consultant        — max 3 client projects
  Row 4 -> SC       — Senior Solution Consultant / Manager           — max 3 client projects
  Row 5 -> TA_PLUS  — Technical Solutions Architect / Principal      — max 5 client projects
  Row 6 -> TA_PLUS  — Principal Solutions Architect / Assoc Partner  — max 5 client projects
  Row 7 -> TA_PLUS  — (blank) / Partner                             — max 5 client projects
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# ROLE CATEGORY CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

CAT_SE      = "SE"       # Software Engineer / Associate Consultant
CAT_SSE     = "SSE"      # Senior Software Engineer / Senior Associate Consultant
CAT_ENABLER = "ENABLER"  # Solutions Enabler / Consultant
CAT_SC      = "SC"       # Solution Consultant / Senior Consultant / Manager
CAT_TA_PLUS = "TA_PLUS"  # TA / Principal / Associate Partner / Partner


# ─────────────────────────────────────────────────────────────────────────────
# ALLOCATION POLICY
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AllocationPolicy:
    """Allocation rules for one role category."""
    category: str
    max_client_projects: int           # hard ceiling on concurrent CLIENT project count
    max_client_allocation_pct: float   # always 100.0 — full billable capacity
    description: str

    def allows_additional_project(self, current_client_count: int) -> bool:
        """Return True if the employee may be assigned to one more client project."""
        return current_client_count < self.max_client_projects

    def remaining_client_capacity(self, client_used_pct: float) -> float:
        """Return how much client capacity (%) is still available."""
        return max(0.0, self.max_client_allocation_pct - client_used_pct)


# Canonical policy table — keys are role category constants
ROLE_POLICIES: Dict[str, AllocationPolicy] = {
    CAT_SE: AllocationPolicy(
        category=CAT_SE,
        max_client_projects=1,
        max_client_allocation_pct=100.0,
        description="Software Engineer / Associate Consultant — 1 client project max at 100%",
    ),
    CAT_SSE: AllocationPolicy(
        category=CAT_SSE,
        max_client_projects=1,
        max_client_allocation_pct=100.0,
        description="Senior SE / Senior AC — 1 client project max at 100%",
    ),
    CAT_ENABLER: AllocationPolicy(
        category=CAT_ENABLER,
        max_client_projects=2,
        max_client_allocation_pct=100.0,
        description="Solutions Enabler / Consultant — up to 2 concurrent client projects",
    ),
    CAT_SC: AllocationPolicy(
        category=CAT_SC,
        max_client_projects=3,
        max_client_allocation_pct=100.0,
        description="Solution Consultant / Senior Consultant / Manager — up to 3 concurrent client projects",
    ),
    CAT_TA_PLUS: AllocationPolicy(
        category=CAT_TA_PLUS,
        max_client_projects=5,
        max_client_allocation_pct=100.0,
        description="TA / Principal / Associate Partner / Partner — up to 5 concurrent client projects",
    ),
}

# Fallback when job title is completely unrecognised
_DEFAULT_POLICY = ROLE_POLICIES[CAT_ENABLER]


# ─────────────────────────────────────────────────────────────────────────────
# PROJECT TYPE CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

# Project types that count as CLIENT (billable, count toward concurrent limit)
CLIENT_PROJECT_TYPES = frozenset({
    "Client Project",
    "Managed Services",
})

# Project types that are INTERNAL (never consume client capacity)
INTERNAL_PROJECT_TYPES = frozenset({
    "Internal Project",
    "BAU Activity",
    "Sales Activity",
})

# Internal project name keywords (for fallback when type is missing)
INTERNAL_NAME_KEYWORDS = frozenset({
    "coe", "r&d", "research", "innovation", "internal ai", "bench",
    "training", "certification", "accelerator", "bau", "sales",
    "internal", "knowledge", "learning",
})


def classify_project_type(type_of_project: str) -> str:
    """
    Classify a raw project type string as 'CLIENT' or 'INTERNAL'.

    Returns:
        'CLIENT'   — billable, counts toward concurrent limit
        'INTERNAL' — internal, never consumes client capacity
    """
    if not type_of_project:
        return "INTERNAL"
    ptype = str(type_of_project).strip()
    if ptype in CLIENT_PROJECT_TYPES:
        return "CLIENT"
    if ptype in INTERNAL_PROJECT_TYPES:
        return "INTERNAL"
    # Keyword fallback
    ptype_lower = ptype.lower()
    if any(kw in ptype_lower for kw in INTERNAL_NAME_KEYWORDS):
        return "INTERNAL"
    return "CLIENT"  # default to CLIENT if unknown


# ─────────────────────────────────────────────────────────────────────────────
# HIERARCHY LOADER
# ─────────────────────────────────────────────────────────────────────────────

# Maps each Hierarchy sheet row index (0-7) to a role category
_ROW_TO_CATEGORY: Dict[int, str] = {
    0: CAT_SE,
    1: CAT_SSE,
    2: CAT_ENABLER,
    3: CAT_SC,
    4: CAT_SC,
    5: CAT_TA_PLUS,
    6: CAT_TA_PLUS,
    7: CAT_TA_PLUS,
}


def _normalize_title(title: str) -> str:
    """Normalise a job title to lowercase stripped string for lookup."""
    if not title or not isinstance(title, str):
        return ""
    return re.sub(r"\s+", " ", title.strip().lower())


class RolePolicyEngine:
    """
    Loads the Hierarchy sheet and provides job-title -> AllocationPolicy lookups.

    All title -> category mappings are driven by the Hierarchy sheet.
    Additional common variants are seeded as fallbacks.

    Usage:
        engine = RolePolicyEngine(pipeline_details_path)
        policy = engine.get_policy("Software Engineer")
        category = engine.get_category("Associate Consultant")
    """

    def __init__(self, pipeline_details_path: Optional[str] = None):
        # title_lower -> category string
        self._title_to_category: Dict[str, str] = {}

        # Always seed canonical fallbacks first (sheet will override if loaded)
        self._seed_canonical_titles()

        # Load from Hierarchy sheet (overrides any seeded values)
        if pipeline_details_path and Path(pipeline_details_path).exists():
            self._load_hierarchy(pipeline_details_path)

    def _load_hierarchy(self, path: str) -> None:
        """Parse the Hierarchy sheet and populate _title_to_category."""
        try:
            df = pd.read_excel(path, sheet_name="Hierarchy", header=0)
        except Exception as exc:
            print(f"[RolePolicy] Warning: could not read Hierarchy sheet from {path}: {exc}")
            return

        cols = df.columns.tolist()
        india_col = cols[0] if len(cols) >= 1 else None
        uk_col    = cols[1] if len(cols) >= 2 else None

        loaded = 0
        for row_idx, row in df.iterrows():
            if row_idx not in _ROW_TO_CATEGORY:
                continue
            category = _ROW_TO_CATEGORY[row_idx]

            if india_col:
                india_title = str(row.get(india_col, "") or "").strip()
                if india_title and india_title.lower() not in ("nan", ""):
                    self._title_to_category[_normalize_title(india_title)] = category
                    loaded += 1

            if uk_col:
                uk_title = str(row.get(uk_col, "") or "").strip()
                if uk_title and uk_title.lower() not in ("nan", ""):
                    self._title_to_category[_normalize_title(uk_title)] = category
                    loaded += 1

        print(
            f"[RolePolicy] Loaded Hierarchy sheet: {loaded} title mappings across "
            f"{len(set(self._title_to_category.values()))} categories."
        )

    def _seed_canonical_titles(self) -> None:
        """
        Seed additional known title variants that appear in people_cube but may
        not appear verbatim in the Hierarchy sheet (abbreviations, alternate spellings).
        """
        _extra: List[tuple] = [
            # SE
            ("software engineer", CAT_SE),
            ("associate consultant", CAT_SE),
            ("ac", CAT_SE),
            # SSE
            ("senior software engineer", CAT_SSE),
            ("senior associate consultant", CAT_SSE),
            ("sac", CAT_SSE),
            ("sse", CAT_SSE),
            # ENABLER
            ("solutions enabler", CAT_ENABLER),
            ("solution enabler", CAT_ENABLER),
            ("enabler", CAT_ENABLER),
            ("consultant", CAT_ENABLER),
            ("c", CAT_ENABLER),
            ("solutions enabler", CAT_ENABLER),
            # SC
            ("solution consultant", CAT_SC),
            ("solutions consultant", CAT_SC),
            ("senior solution consultant", CAT_SC),
            ("senior solutions consultant", CAT_SC),
            ("sol con", CAT_SC),
            ("snr sol con", CAT_SC),
            ("sr sol con", CAT_SC),
            ("senior consultant", CAT_SC),
            ("manager", CAT_SC),
            ("engagement manager", CAT_SC),
            ("em", CAT_SC),
            ("sc", CAT_SC),
            ("sc (em)", CAT_SC),
            ("sc or c - em", CAT_SC),
            # TA_PLUS
            ("technical solutions architect", CAT_TA_PLUS),
            ("technology solutions architect", CAT_TA_PLUS),
            ("principal technology architect", CAT_TA_PLUS),
            ("principal solutions architect", CAT_TA_PLUS),
            ("principal architect", CAT_TA_PLUS),
            ("principal consultant", CAT_TA_PLUS),
            ("principal", CAT_TA_PLUS),
            ("technical architect", CAT_TA_PLUS),
            ("technology architect", CAT_TA_PLUS),
            ("gtm architect", CAT_TA_PLUS),
            ("technical solutions architect", CAT_TA_PLUS),
            ("associate partner", CAT_TA_PLUS),
            ("partner", CAT_TA_PLUS),
            ("leadership", CAT_TA_PLUS),
            ("ap", CAT_TA_PLUS),
            ("p", CAT_TA_PLUS),
            ("ta", CAT_TA_PLUS),
            ("pa", CAT_TA_PLUS),
            ("ap/p", CAT_TA_PLUS),
            ("senior data science sme", CAT_TA_PLUS),
            ("ds", CAT_SC),
        ]
        for title, cat in _extra:
            key = _normalize_title(title)
            self._title_to_category[key] = cat

    def get_category(self, job_name: str) -> str:
        """
        Return the role category for a given job title.

        Falls back progressively:
          1. Exact normalised match
          2. Substring match (handles "Senior Solution Consultant (UK)")
          3. Keyword match on 'architect', 'partner', 'manager', etc.
          4. Default: ENABLER
        """
        key = _normalize_title(job_name)
        if not key:
            return CAT_ENABLER

        # 1. Exact match
        if key in self._title_to_category:
            return self._title_to_category[key]

        # 2. Substring match — the known title is contained within the key
        for known_title, cat in self._title_to_category.items():
            if known_title and known_title in key:
                return cat

        # 3. Keyword fallback
        if "software engineer" in key:
            return CAT_SSE if "senior" in key else CAT_SE
        if "enabler" in key:
            return CAT_ENABLER
        if "consultant" in key:
            if "senior" in key or "solution" in key:
                return CAT_SC
            return CAT_ENABLER
        if "architect" in key or "principal" in key:
            return CAT_TA_PLUS
        if "partner" in key or "leadership" in key:
            return CAT_TA_PLUS
        if "manager" in key:
            return CAT_SC

        return CAT_ENABLER  # safe default

    def get_policy(self, job_name: str) -> AllocationPolicy:
        """Return the AllocationPolicy for a given job title."""
        category = self.get_category(job_name)
        return ROLE_POLICIES.get(category, _DEFAULT_POLICY)

    def all_categories(self) -> List[str]:
        """Return all unique categories known to this engine."""
        return sorted(set(self._title_to_category.values()))

    def dump_mappings(self) -> Dict[str, str]:
        """Return the full title -> category mapping (useful for debugging)."""
        return dict(self._title_to_category)


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL SINGLETON AND CONVENIENCE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

_engine_instance: Optional[RolePolicyEngine] = None


def init_role_policy(pipeline_details_path: Optional[str] = None) -> RolePolicyEngine:
    """Initialise the global RolePolicyEngine singleton. Call once on startup."""
    global _engine_instance
    _engine_instance = RolePolicyEngine(pipeline_details_path)
    return _engine_instance


def get_role_policy_engine() -> RolePolicyEngine:
    """Return the global singleton, initialising with defaults if not yet created."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = RolePolicyEngine()
    return _engine_instance


def get_category(job_name: str) -> str:
    """Module-level shortcut: get role category for a job title."""
    return get_role_policy_engine().get_category(job_name)


def get_policy(job_name: str) -> AllocationPolicy:
    """Module-level shortcut: get AllocationPolicy for a job title."""
    return get_role_policy_engine().get_policy(job_name)
