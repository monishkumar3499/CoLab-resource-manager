"""
loader.py — Loads and post-processes all datacubes.

Fixes applied on load (data quality issues found in audit):
  1. swap_eligible was stored as int64 from ~bool bug → recomputed here.
  2. is_managed_service was True for everyone → recomputed from BAU-only logic.
  3. JSON columns (top_skills, competency_profile, etc.) deserialised here.
  4. NaN in list columns replaced with empty list.
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Set

import numpy as np
import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_json_col(val):
    """Deserialise a JSON string column to Python object; return [] on failure."""
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _list_col(series: pd.Series) -> pd.Series:
    return series.apply(_parse_json_col)


JSON_LIST_COLS = ["top_skills", "all_coes", "ts_project_ids",
                  "active_project_ids", "client_history", "allocated_employees"]
JSON_DICT_COLS = ["competency_profile"]


# ─────────────────────────────────────────────────────────────────────────────
# LOADERS
# ─────────────────────────────────────────────────────────────────────────────

def load_people(cfg: EngineConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """
    Load people_cube.parquet and apply all post-processing fixes.
    Returns a clean DataFrame ready for the recommendation engine.
    """
    path = Path(cfg.datacube_dir) / "people_cube.parquet"
    df = pd.read_parquet(path)

    # ── Fix list / dict columns ───────────────────────────────────────────────
    for col in JSON_LIST_COLS:
        if col in df.columns:
            df[col] = _list_col(df[col])
    for col in JSON_DICT_COLS:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: json.loads(v) if isinstance(v, str) else (v if isinstance(v, dict) else {})
            )

    # ── Recompute is_bau_only and swap_eligible ───────────────────────────────
    # is_managed_service in the cube was wrong (all True due to ~ on int64)
    # Recompute: BAU-only = employee whose active_project_ids are ALL CLIENT_127 (BAU) projects
    # We can't recheck project types here without the projects cube, so we use a heuristic:
    # If every project_id in active_project_ids starts with "CLIENT_127", treat as BAU-only
    def _is_bau_only(active_ids):
        if not active_ids:
            return False
        return all(pid.startswith("CLIENT_127") for pid in active_ids)

    df["is_bau_only"] = df["active_project_ids"].apply(_is_bau_only)
    df["swap_eligible"] = ~df["is_bau_only"]

    # ── Fill NaN scalars ──────────────────────────────────────────────────────
    df["util_pct"] = df["effective_util_pct"].fillna(0.0)
    df["available_capacity_pct"] = df["available_capacity_pct"].fillna(100.0)
    df["avg_skill_score"] = df["avg_skill_score"].fillna(0.0)
    df["max_skill_score"] = df["max_skill_score"].fillna(0.0)
    df["avg_competency_score"] = df["avg_competency_score"].fillna(0.0)
    df["avg_exp_years"] = df["avg_exp_years"].fillna(0.0)
    df["skill_breadth"] = df["skill_breadth"].fillna(0).astype(int)
    df["ramp_down_flag"] = df["ramp_down_flag"].fillna(False).astype(bool)
    df["on_red_project"] = df["on_red_project"].fillna(False).astype(bool)
    df["on_amber_project"] = df["on_amber_project"].fillna(False).astype(bool)
    df["health_penalty"] = df["health_penalty"].fillna(0.0)
    df["ramp_down_bonus"] = df["ramp_down_bonus"].fillna(0.0)
    df["days_to_soonest_end"] = df["days_to_soonest_end"].fillna(float("nan"))
    df["tenure_years"] = df["tenure_years"].fillna(0.0)
    df["skill_text"] = df["skill_text"].fillna("")
    df["profile_text"] = df["profile_text"].fillna("")
    df["primary_coe"] = df["primary_coe"].fillna("Unknown")

    # ── Derived columns ───────────────────────────────────────────────────────
    # has_skill_data: True if the employee appears in Skill_Data.xlsx
    df["has_skill_data"] = df["avg_skill_score"] > 0

    # has_competency_data
    df["has_competency_data"] = df["avg_competency_score"] > 0

    # effective_availability: capacity available right now
    df["effective_availability"] = df["available_capacity_pct"].clip(0.0, 100.0)

    # soon_free: ramp-down OR within 56 days
    df["soon_free"] = (
        df["ramp_down_flag"] |
        (df["days_to_soonest_end"].between(0, 56, inclusive="both"))
    )

    return df.reset_index(drop=True)


def load_projects(cfg: EngineConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    path = Path(cfg.datacube_dir) / "projects_cube.parquet"
    df = pd.read_parquet(path)

    # Deserialise allocated_employees
    if "allocated_employees" in df.columns:
        df["allocated_employees"] = _list_col(df["allocated_employees"])

    # Fill
    df["health_score"] = df["health_score"].fillna(0.65)
    df["extension_risk"] = df["extension_risk"].fillna(0.0)
    df["ramp_down_signal"] = df["ramp_down_signal"].fillna(0.0)
    for col in ["latest_scope", "latest_schedule", "latest_quality", "latest_csat", "latest_team"]:
        if col in df.columns:
            df[col] = df[col].fillna("NO_COLOR")
    df["billability_rate"] = df["billability_rate"].fillna(0.0)
    df["total_slots"] = df["total_slots"].fillna(0).astype(int)
    df["days_until_end"] = df["days_until_end"].fillna(float("nan"))

    # Build fast lookup: project_id → row dict
    df = df.set_index("project_id", drop=False)
    return df


def load_pipeline_projects(cfg: EngineConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    path = Path(cfg.datacube_dir) / "pipeline_projects_cube.parquet"
    df = pd.read_parquet(path)
    df["likely_start_dt"] = pd.to_datetime(df["likely_start_dt"], errors="coerce")
    if "orig_start_dt" in df.columns:
        df["orig_start_dt"] = pd.to_datetime(df["orig_start_dt"], errors="coerce")
    df["days_until_start"] = df["days_until_start"].fillna(float("nan"))
    df["composite_priority"] = df["composite_priority"].fillna(0).astype(int)
    df["sow_signed"] = df["sow_signed"].fillna(False).astype(bool)
    return df.sort_values("composite_priority", ascending=False).reset_index(drop=True)


def load_pipeline_roles(cfg: EngineConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    path = Path(cfg.datacube_dir) / "pipeline_roles_cube.parquet"
    df = pd.read_parquet(path)
    df["allocation_pct"] = pd.to_numeric(df["allocation_pct"], errors="coerce").fillna(100.0)
    df["seniority_tier"] = pd.to_numeric(df["seniority_tier"], errors="coerce").fillna(3).astype(int)
    df["role_urgency"] = df["role_urgency"].fillna(0).astype(int)
    df["skillset_notes"] = df["skillset_notes"].apply(
        lambda v: str(v) if v and not str(v).startswith("<") else ""
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# FAST LOOKUP HELPERS (used across stages)
# ─────────────────────────────────────────────────────────────────────────────

class DataStore:
    """
    Holds all four DataFrames in memory after a single load.
    Passed as a single dependency to every engine stage.
    """

    def __init__(self, cfg: EngineConfig = DEFAULT_CONFIG):
        print("[DataStore] Loading datacubes...")
        self.cfg = cfg
        self.people = load_people(cfg)
        self.projects = load_projects(cfg)
        self.pipeline_projects = load_pipeline_projects(cfg)
        self.pipeline_roles = load_pipeline_roles(cfg)

        # Build lookup dictionaries for O(1) access
        self._people_by_id: Dict = self.people.set_index("employee_id").to_dict("index")
        self._project_by_id: Dict = self.projects.to_dict("index")
        # Index: employee_id → list of project_ids they are currently on
        self._emp_to_projects: Dict[str, List[str]] = (
            self.people.set_index("employee_id")["active_project_ids"].to_dict()
        )
        # Index: project_id → list of employee_ids allocated
        self._project_to_emps: Dict[str, List[str]] = {}
        for _, row in self.projects.iterrows():
            emps = row["allocated_employees"]
            self._project_to_emps[row["project_id"]] = emps if isinstance(emps, list) else []

        # Load role policy engine and build hierarchy mapping from Pipeline Details
        from .role_policy import RolePolicyEngine
        pipeline_path = Path(self.cfg.datacube_dir).parent / "data" / "07__260624_Pipeline_Details.xlsx"
        self.role_policy_engine = RolePolicyEngine(str(pipeline_path))
        self.hierarchy = self.role_policy_engine.dump_mappings()

        # Load raw active allocations and project details to compute client-only capacity
        data_dir = Path(self.cfg.datacube_dir).parent / "data"
        
        allocs_df = pd.read_csv(data_dir / "03__260623_Project_Allocation_Details.csv")
        allocs_df["employee_id"] = allocs_df["employee_id"].astype(str).str.strip().str.upper()
        allocs_df["project_id"] = allocs_df["project_id"].astype(str).str.strip()
        allocs_df["allocation_by_percentage"] = pd.to_numeric(allocs_df["allocation_by_percentage"], errors="coerce").fillna(0.0)
        active_allocs = allocs_df[allocs_df["is_allocation_active"] == 1].copy()

        projects_df = pd.read_csv(data_dir / "02__260624_project_details.csv")
        projects_df = projects_df[projects_df["is_active_version"] == 1].copy()
        projects_df["project_id"] = projects_df["project_id"].astype(str).str.strip()
        projects_df["type_of_project"] = projects_df["type_of_project"].fillna("").str.strip()

        # Exclude BAU Activity, Internal Project, Sales Activity
        EXCLUDE_PROJ_TYPES = {"BAU Activity", "Internal Project", "Sales Activity"}
        projects_df["is_client"] = ~projects_df["type_of_project"].isin(EXCLUDE_PROJ_TYPES)
        client_project_ids = set(projects_df[projects_df["is_client"]]["project_id"])

        # Determine shadow employees
        shadow_eids = set(active_allocs[active_allocs["resourcing_status"].str.upper() == "SHADOW"]["employee_id"])
        self._shadow_employees = shadow_eids

        # Filter active allocations on client projects
        client_allocs = active_allocs[active_allocs["project_id"].isin(client_project_ids)].copy()

        # Group and sum allocations
        client_alloc_sum = client_allocs.groupby("employee_id")["allocation_by_percentage"].sum().to_dict()
        client_project_count_dict = client_allocs.groupby("employee_id")["project_id"].nunique().to_dict()

        # Populate capacities and counts
        self._client_only_capacity = {}
        self._client_project_counts = {}
        for _, row in self.people.iterrows():
            eid = str(row["employee_id"]).strip().upper()
            self._client_only_capacity[eid] = 100.0
            self._client_project_counts[eid] = 0

        for eid, val in client_alloc_sum.items():
            self._client_only_capacity[eid] = max(0.0, 100.0 - float(val))
        for eid, cnt in client_project_count_dict.items():
            self._client_project_counts[eid] = int(cnt)

        # Build initial allocations list for SQLite
        self.initial_allocs = []
        for _, row in active_allocs.iterrows():
            eid = str(row["employee_id"]).strip().upper()
            pid = str(row["project_id"]).strip()
            is_client = pid in client_project_ids
            alloc_type = "CLIENT" if is_client else "INTERNAL"
            alloc_pct = float(row["allocation_by_percentage"])
            
            # Find category of employee
            emp_row = self.get_person(eid)
            emp_job = emp_row.get("job_name", "Consultant") if emp_row else "Consultant"
            role_category = self.role_policy_engine.get_category(emp_job)
            
            self.initial_allocs.append({
                "employee_id": eid,
                "project_id": pid,
                "role": str(row.get("resourcing_status", "") or ""),
                "allocation_percent": alloc_pct,
                "role_required": str(row.get("resourcing_status", "") or ""),
                "project_type": alloc_type,
                "role_category": role_category,
                "allocated_end_date": str(row.get("allocated_end_date", "") or "")
            })

        print(f"[DataStore] Ready. "
              f"People={len(self.people)}, "
              f"Projects={len(self.projects)}, "
              f"Pipeline={len(self.pipeline_projects)} projects / {len(self.pipeline_roles)} roles")

    def get_person(self, employee_id: str) -> Dict:
        return self._people_by_id.get(employee_id, {})

    def get_role_category(self, job_name: str) -> str:
        return self.role_policy_engine.get_category(job_name)

    def is_shadow(self, employee_id: str) -> bool:
        return employee_id.upper() in self._shadow_employees

    def get_project(self, project_id: str) -> Dict:
        return self._project_by_id.get(project_id, {})

    def get_employee_projects(self, employee_id: str) -> List[str]:
        return self._emp_to_projects.get(employee_id, [])

    def get_project_employees(self, project_id: str) -> List[str]:
        return self._project_to_emps.get(project_id, [])

    def available_people(self, min_capacity: float = 25.0) -> pd.DataFrame:
        return self.people[self.people["effective_availability"] >= min_capacity]
