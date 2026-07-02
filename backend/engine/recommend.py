"""
recommend.py — Main Orchestrator

Wires all 8 stages into a single callable pipeline.

Usage (CLI):
    python recommend.py --pipeline-id "Sigma_3_2026-07-15"
    python recommend.py --all                        # run all pipeline projects by priority
    python recommend.py --all --top 5               # top 5 by composite_priority

Usage (programmatic):
    from engine.recommend import RecommendationEngine
    engine = RecommendationEngine()
    result = engine.run("Sigma_3_2026-07-15")
    print(result.project_plan.coverage_pct)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG, standardize_role
from .loader import DataStore
from .stage1_retrieval import SemanticRetrieval
from .stage2_intelligence import CandidateIntelligenceEngine
from .stage3_rules import BusinessRuleValidator
from .stage4_swap import SwapBackfillPlanner
from .stage5_6_impact_ranking import MultiObjectiveRanker
from .stage7_plans import RecommendationPlanGenerator, ProjectStaffingPlan, RoleStaffingPlan
from .stage8_llm import LLMIntelligence, LLMEnrichedOutput
from .stage9_validation import RecommendationValidator


# ─────────────────────────────────────────────────────────────────────────────
# FULL RESULT DATA CLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RecommendationResult:
    pipeline_id: str
    client: str
    project_plan: ProjectStaffingPlan
    llm_output: LLMEnrichedOutput
    elapsed_seconds: float
    stage_timings: Dict[str, float]


# ─────────────────────────────────────────────────────────────────────────────
# ENGINE ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

class RecommendationEngine:
    """
    Orchestrates stateful candidate selection and reservation across all pipeline projects.
    Initializes a temporary SQLite database for state tracking.
    """

    def get_db_path(self) -> str:
        db_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "database")
        os.makedirs(db_dir, exist_ok=True)
        return os.path.join(db_dir, "allocation_state.db")

    def __init__(self, cfg: EngineConfig = DEFAULT_CONFIG, force_reset: bool = False):
        self.cfg = cfg
        t0 = time.time()

        # Load all data once
        self.ds = DataStore(cfg)

        # Initialise stages (Stage 1 builds/loads the embedding index)
        self.retrieval    = SemanticRetrieval(self.ds, cfg)
        self.llm          = LLMIntelligence(cfg)

        # Initialize SQLite database
        self.init_db(force_reset=force_reset)
        self.globally_allocated_in_run = set()

        print(f"[Engine] Stateful engine ready in {time.time() - t0:.1f}s")

    def init_db(self, force_reset: bool = False):
        import sqlite3
        db_path = self.get_db_path()
        if force_reset:
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                    print(f"[Engine] Deleted existing state database: {db_path}")
                except Exception as e:
                    print(f"[Engine] Warning: Could not delete {db_path}: {e}")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create employees table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS employees (
                employee_id TEXT PRIMARY KEY,
                capacity_remaining REAL,
                utilisation REAL,
                role TEXT,
                country TEXT,
                primary_coe TEXT,
                skill_score REAL,
                role_category TEXT,
                client_project_count INTEGER DEFAULT 0,
                client_capacity_remaining REAL DEFAULT 100.0
            )
        """)

        # Create allocations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS allocations (
                allocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT,
                project_id TEXT,
                role TEXT,
                allocation_percent REAL,
                role_required TEXT,
                project_type TEXT,
                role_category TEXT,
                allocated_end_date TEXT,
                timestamp TEXT DEFAULT (datetime('now'))
            )
        """)

        # Run ALTER TABLE commands to add new columns if they do not exist (migration)
        cursor.execute("PRAGMA table_info(employees)")
        cols = [c[1] for c in cursor.fetchall()]
        if "role_category" not in cols:
            cursor.execute("ALTER TABLE employees ADD COLUMN role_category TEXT")
        if "client_project_count" not in cols:
            cursor.execute("ALTER TABLE employees ADD COLUMN client_project_count INTEGER DEFAULT 0")
        if "client_capacity_remaining" not in cols:
            cursor.execute("ALTER TABLE employees ADD COLUMN client_capacity_remaining REAL DEFAULT 100.0")

        cursor.execute("PRAGMA table_info(allocations)")
        acols = [c[1] for c in cursor.fetchall()]
        if "project_type" not in acols:
            cursor.execute("ALTER TABLE allocations ADD COLUMN project_type TEXT")
        if "role_category" not in acols:
            cursor.execute("ALTER TABLE allocations ADD COLUMN role_category TEXT")

        # Create project_state table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS project_state (
                project_id TEXT PRIMARY KEY,
                status TEXT,
                completed_at TEXT
            )
        """)

        # Create allocation_logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS allocation_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id TEXT,
                project TEXT,
                role TEXT,
                semantic_score REAL,
                skill_score REAL,
                coe_score REAL,
                tier_score REAL,
                geo_score REAL,
                capacity_before REAL,
                capacity_after REAL,
                reason_selected TEXT,
                reason_rejected TEXT,
                timestamp TEXT DEFAULT (datetime('now'))
            )
        """)

        # Populate employees table if empty
        cursor.execute("SELECT COUNT(*) FROM employees")
        count = cursor.fetchone()[0]
        if count == 0:
            print("[Engine] Initializing employees in SQLite from DataStore...")
            emp_data = []
            seen_eids = set()
            from .config import standardize_role
            for _, row in self.ds.people.iterrows():
                eid = str(row["employee_id"]).strip().upper()
                if eid in seen_eids:
                    continue
                seen_eids.add(eid)
                cap = float(row.get("available_capacity_pct", 100.0))
                util = float(row.get("effective_util_pct", 0.0))
                role = standardize_role(str(row.get("job_name") or "Consultant"))
                country = str(row.get("geo_cluster", "India"))
                coe = str(row.get("primary_coe", "Unknown"))
                ss = float(row.get("avg_skill_score", 0.0))
                
                # Role category from DataStore
                role_category = self.ds.get_role_category(str(row.get("job_name") or "Consultant"))
                
                # Client capacity and counts computed from raw allocations
                client_cap = self.ds._client_only_capacity.get(eid, 100.0)
                client_proj_cnt = self.ds._client_project_counts.get(eid, 0)
                
                emp_data.append((eid, cap, util, role, country, coe, ss, role_category, client_proj_cnt, client_cap))

            cursor.executemany("""
                INSERT OR REPLACE INTO employees (employee_id, capacity_remaining, utilisation, role, country, primary_coe, skill_score, role_category, client_project_count, client_capacity_remaining)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, emp_data)
            conn.commit()
            print(f"[Engine] Inserted {len(emp_data)} employees into SQLite.")

        # Populate allocations table if empty
        cursor.execute("SELECT COUNT(*) FROM allocations")
        alloc_count = cursor.fetchone()[0]
        if alloc_count == 0:
            print("[Engine] Initializing allocations in SQLite from DataStore...")
            cursor.execute("SELECT employee_id FROM employees")
            valid_eids = {r[0] for r in cursor.fetchall()}
            
            allocs_data = []
            for a in self.ds.initial_allocs:
                eid = a["employee_id"]
                if eid in valid_eids:
                    allocs_data.append((
                        eid,
                        a["project_id"],
                        a["role"],
                        a["allocation_percent"],
                        a["role_required"],
                        a["project_type"],
                        a["role_category"],
                        a["allocated_end_date"]
                    ))
            cursor.executemany("""
                INSERT INTO allocations (employee_id, project_id, role, allocation_percent, role_required, project_type, role_category, allocated_end_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, allocs_data)
            conn.commit()
            print(f"[Engine] Inserted {len(allocs_data)} allocations into SQLite.")

        conn.close()

    def validate_database_state(self, processed_projects: Optional[List[str]] = None):
        import sqlite3
        db_path = self.get_db_path()
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # If processed_projects is provided, filter validation to only employees affected by those projects
        if processed_projects:
            placeholders = ",".join("?" for _ in processed_projects)
            cursor.execute(f"SELECT DISTINCT employee_id FROM allocations WHERE project_id IN ({placeholders})", list(processed_projects))
            emp_ids = {r[0] for r in cursor.fetchall()}
            if not emp_ids:
                conn.close()
                print("[Engine] No active changes to validate.")
                return
        else:
            emp_ids = None

        # 1. Negative remaining capacity
        if emp_ids is not None:
            placeholders = ",".join("?" for _ in emp_ids)
            cursor.execute(f"SELECT employee_id, capacity_remaining FROM employees WHERE capacity_remaining < -0.01 AND employee_id IN ({placeholders})", list(emp_ids))
        else:
            cursor.execute("SELECT employee_id, capacity_remaining FROM employees WHERE capacity_remaining < -0.01")
        neg_rows = cursor.fetchall()
        if neg_rows:
            print(f"[Engine] [Warning] Phase 9: Negative remaining capacity detected: {neg_rows}")
 
        # 1b. Negative remaining client capacity
        if emp_ids is not None:
            placeholders = ",".join("?" for _ in emp_ids)
            cursor.execute(f"SELECT employee_id, client_capacity_remaining FROM employees WHERE client_capacity_remaining < -0.01 AND employee_id IN ({placeholders})", list(emp_ids))
        else:
            cursor.execute("SELECT employee_id, client_capacity_remaining FROM employees WHERE client_capacity_remaining < -0.01")
        neg_client_rows = cursor.fetchall()
        if neg_client_rows:
            print(f"[Engine] [Warning] Phase 9: Negative remaining client capacity detected: {neg_client_rows}")

        # 1c. Count CLIENT project allocations - must not exceed policy max
        if emp_ids is not None:
            placeholders = ",".join("?" for _ in emp_ids)
            cursor.execute(f"""
                SELECT a.employee_id, COUNT(DISTINCT a.project_id) as client_alloc_count, e.role_category
                FROM allocations a
                JOIN employees e ON a.employee_id = e.employee_id
                WHERE a.project_type = 'CLIENT' AND a.employee_id IN ({placeholders})
                GROUP BY a.employee_id, e.role_category
            """, list(emp_ids))
        else:
            cursor.execute("""
                SELECT a.employee_id, COUNT(DISTINCT a.project_id) as client_alloc_count, e.role_category
                FROM allocations a
                JOIN employees e ON a.employee_id = e.employee_id
                WHERE a.project_type = 'CLIENT'
                GROUP BY a.employee_id, e.role_category
            """)
        alloc_counts = cursor.fetchall()
        from .role_policy import ROLE_POLICIES
        for emp_id, count, cat in alloc_counts:
            policy = ROLE_POLICIES.get(cat)
            if policy and count > policy.max_client_projects:
                print(
                    f"[Engine] [Warning] Phase 9: Employee {emp_id} (category {cat}) has {count} CLIENT allocations, "
                    f"exceeding their maximum limit of {policy.max_client_projects} projects."
                )

        # 1d. Sum CLIENT allocation_percent - must not exceed 100%
        if emp_ids is not None:
            placeholders = ",".join("?" for _ in emp_ids)
            cursor.execute(f"""
                SELECT employee_id, SUM(allocation_percent) as client_alloc_pct
                FROM allocations
                WHERE project_type = 'CLIENT' AND employee_id IN ({placeholders})
                GROUP BY employee_id
            """, list(emp_ids))
        else:
            cursor.execute("""
                SELECT employee_id, SUM(allocation_percent) as client_alloc_pct
                FROM allocations
                WHERE project_type = 'CLIENT'
                GROUP BY employee_id
            """)
        alloc_pcts = cursor.fetchall()
        for emp_id, pct in alloc_pcts:
            if pct > 100.01:
                print(
                    f"[Engine] [Warning] Phase 9: Employee {emp_id} has client allocation total of {pct}%, "
                    f"exceeding 100% capacity limit."
                )

        # 2. Incompatible role assignment detected
        if processed_projects:
            placeholders = ",".join("?" for _ in processed_projects)
            cursor.execute(f"""
                SELECT a.employee_id, a.project_id, a.role, a.role_required, e.role
                FROM allocations a
                JOIN employees e ON a.employee_id = e.employee_id
                WHERE a.project_id IN ({placeholders})
            """, list(processed_projects))
        else:
            cursor.execute("""
                SELECT a.employee_id, a.project_id, a.role, a.role_required, e.role
                FROM allocations a
                JOIN employees e ON a.employee_id = e.employee_id
            """)
        allocs = cursor.fetchall()
        from .config import standardize_role
        compat_map = self.cfg.rules.role_compatibility
        for emp_id, proj_id, role, role_req, emp_role in allocs:
            req_std = standardize_role(role_req)
            emp_std = standardize_role(emp_role)
            compatibles = compat_map.get(req_std, [req_std])
            if emp_std not in compatibles:
                print(
                    f"[Engine] [Warning] Phase 9: Incompatible role assignment: "
                    f"employee {emp_id} (role {emp_std}) allocated to role {role_req} "
                    f"(requires one of {compatibles})"
                )

        # 4. Phantom employee detected
        cursor.execute("""
            SELECT employee_id FROM allocations
            WHERE employee_id NOT IN (SELECT employee_id FROM employees)
        """)
        phantom_rows = cursor.fetchall()
        if phantom_rows:
            print(f"[Engine] [Warning] Phase 10: Phantom employee detected in allocations: {phantom_rows}")

        # 5. Duplicate employee IDs in employees table
        cursor.execute("""
            SELECT employee_id, COUNT(*) FROM employees
            GROUP BY employee_id
            HAVING COUNT(*) > 1
        """)
        dup_rows = cursor.fetchall()
        if dup_rows:
            print(f"[Engine] [Warning] Phase 10: Duplicate employee IDs detected: {dup_rows}")

        conn.close()
        print("[Engine] Phase 10 validation checks completed (warnings logged).")

    # ── Single project ────────────────────────────────────────────────────────

    def run(self, pipeline_id: str, commit: bool = False) -> RecommendationResult:
        """Run the refactored, stateful recommendation pipeline for one project."""
        import sqlite3, re, numpy as np
        from .stage1_retrieval import build_role_query, RetrievalResult
        from .stage7_plans import ProjectStaffingPlan, RoleStaffingPlan, StaffingOption, _build_hire_option, _build_option_from_ranked
        from .stage3_rules import ValidatedCandidate

        t_total = time.time()
        timings: Dict[str, float] = {}

        # Resolve pipeline project row
        matches = self.ds.pipeline_projects[
            self.ds.pipeline_projects["pipeline_id"] == pipeline_id
        ]
        if matches.empty:
            raise ValueError(f"Pipeline project '{pipeline_id}' not found.")
        pipeline_project = matches.iloc[0]
        client = str(pipeline_project.get("client") or "")
        likely_start = str(pipeline_project.get("likely_start_str") or "") or None

        print(f"\n{'='*60}")
        print(f"[Engine] Running stateful allocation for: {pipeline_id} (commit={commit})")
        print(f"  Client: {client} | Priority: {pipeline_project.get('client_priority')} "
              f"| {pipeline_project.get('priority')} | SOW: {pipeline_project.get('sow_signed')}")
        print(f"{'='*60}")

        # Get roles requested by this project
        project_roles = self.ds.pipeline_roles[
            self.ds.pipeline_roles["pipeline_id"] == pipeline_id
        ]

        role_plans: List[RoleStaffingPlan] = []
        db_path = self.get_db_path()

        # Fetch current SQLite capacities to track state statefully in memory
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT employee_id, capacity_remaining, utilisation, role, role_category, client_project_count, client_capacity_remaining FROM employees")
        rows = cursor.fetchall()
        conn.close()

        # In-memory track of remaining capacities and allocations
        temp_state = {
            r[0]: {
                "capacity_remaining": r[1],
                "utilisation": r[2],
                "role": r[3],
                "role_category": r[4],
                "client_project_count": r[5],
                "client_capacity_remaining": r[6]
            } for r in rows
        }
        globally_assigned = set()

        t_allocation = time.time()

        # Initialize stage engines
        retriever = SemanticRetrieval(self.ds, self.cfg)
        rule_validator = BusinessRuleValidator(self.ds, self.cfg)
        intel_engine = CandidateIntelligenceEngine(self.ds, self.cfg)
        ranker = MultiObjectiveRanker(self.ds, self.cfg)
        plan_generator = RecommendationPlanGenerator(self.ds, self.cfg)

        # Import 6-level classifier constants
        from .stage3_rules import (
            classify_match_level,
            MATCH_LEVEL_1_EXACT, MATCH_LEVEL_2_STRONG,
            MATCH_LEVEL_3_TRANSFERABLE, MATCH_LEVEL_4_AVAILABILITY,
            MATCH_LEVEL_LABELS, MATCH_LEVEL_CONFIDENCE,
        )
        from .stage7_plans import _build_extend_start_option

        # Determine project strategic importance for Level 6 gate
        client_priority = str(pipeline_project.get("client_priority") or "")
        sow_signed = pipeline_project.get("sow_signed")
        sow_active = sow_signed is True or str(sow_signed).lower() in ("true", "1", "yes")
        is_strategic = (
            client_priority in self.cfg.rules.strategic_client_priorities
            or sow_active
        )

        project_allocations_unresolved = []

        for idx, role_row in project_roles.iterrows():
            role_name = str(role_row.get("role_name") or "")
            required_role = str(role_row.get("role_abbr") or "C")
            required_pct = float(role_row.get("allocation_pct", 100.0))

            remaining_pct = required_pct
            options_allocated = []

            # Streamlined Matching Waterfall based on Concurrent Project limits
            skillset_notes = role_row.get("skillset_notes", "") or ""
            has_skillset = bool(skillset_notes and len(str(skillset_notes).strip()) > 2)
            req_role_std = standardize_role(required_role)
            compatibles = self.cfg.rules.role_compatibility.get(req_role_std, [req_role_std])

            # Get eligible people
            eligible_people = []
            for _, p_row in self.ds.people.iterrows():
                eid = p_row["employee_id"]
                if eid in globally_assigned or eid in self.globally_allocated_in_run:
                    continue
                if bool(p_row.get("is_bau_only", False)):
                    continue
                cand_role_std = standardize_role(p_row["job_name"])
                if cand_role_std in compatibles:
                    eligible_people.append(p_row)

            selected_eid = None
            selected_person = None

            # Availability helper checking role project limits
            from .role_policy import ROLE_POLICIES, get_policy
            def is_available(p_row):
                eid = p_row["employee_id"]
                cat = None
                if eid in temp_state:
                    cat = temp_state[eid].get("role_category")
                policy = ROLE_POLICIES.get(cat) if cat else get_policy(p_row["job_name"])

                current_count = 0
                if eid in temp_state:
                    current_count = temp_state[eid].get("client_project_count", 0)
                else:
                    current_count = int(p_row.get("client_project_count", 0))
                return current_count < policy.max_client_projects

            # Build similarity map for all eligible people
            sim_map = {}
            if has_skillset and eligible_people:
                query = build_role_query(role_row, pipeline_project)
                eligible_ids = [p["employee_id"] for p in eligible_people]
                raw_results = retriever.index.query(query, top_k=50, eligible_ids=eligible_ids)
                for eid, sim, dist in raw_results:
                    sim_map[eid] = sim

                # -- Step 1: Skillset search (top 10 matches)
                min_sim = self.cfg.retrieval.min_similarity
                top_10 = [r for r in raw_results if r[1] >= min_sim][:10]
                for eid, sim, dist in top_10:
                    p_row = next((p for p in eligible_people if p["employee_id"] == eid), None)
                    if p_row and is_available(p_row):
                        selected_eid = eid
                        selected_person = p_row
                        break

            # -- Step 2: History-wise check
            if not selected_eid and eligible_people:
                pipeline_solution = str(pipeline_project.get("solution") or "").lower()
                for p_row in eligible_people:
                    eid = p_row["employee_id"]
                    active_pids = self.ds.get_person_projects(eid) if hasattr(self.ds, "get_person_projects") else []
                    match_found = False
                    for pid in active_pids:
                        project = self.ds.get_project(pid)
                        if project:
                            tech_coe = str(project.get("tech_coe") or "").lower()
                            if pipeline_solution and (pipeline_solution in tech_coe or tech_coe in pipeline_solution):
                                match_found = True
                                break
                    if match_found and is_available(p_row):
                        selected_eid = eid
                        selected_person = p_row
                        break

            # -- Step 3: COE-wise check
            if not selected_eid and eligible_people:
                pipeline_solution = str(pipeline_project.get("solution") or "").lower()
                for p_row in eligible_people:
                    coe = str(p_row.get("primary_coe") or "").lower()
                    if pipeline_solution and (pipeline_solution in coe or coe in pipeline_solution):
                        if is_available(p_row):
                            selected_eid = p_row["employee_id"]
                            selected_person = p_row
                            break

            # -- Step 4: Anyone in internal projects alone or in bench (count == 0)
            if not selected_eid and eligible_people:
                for p_row in eligible_people:
                    eid = p_row["employee_id"]
                    current_count = temp_state[eid].get("client_project_count", 0) if eid in temp_state else int(p_row.get("client_project_count", 0))
                    if current_count == 0 and is_available(p_row):
                        selected_eid = eid
                        selected_person = p_row
                        break

            # -- Step 5: Anyone available within count limit
            if not selected_eid and eligible_people:
                for p_row in eligible_people:
                    if is_available(p_row):
                        selected_eid = p_row["employee_id"]
                        selected_person = p_row
                        break

            if selected_eid and selected_person is not None:
                # Deduct exactly what is demanded
                allocated_pct = required_pct

                # Update temp state
                temp_state[selected_eid]["capacity_remaining"] = max(0.0, temp_state[selected_eid]["capacity_remaining"] - allocated_pct)
                temp_state[selected_eid]["utilisation"] = min(100.0, temp_state[selected_eid]["utilisation"] + allocated_pct)
                temp_state[selected_eid]["client_capacity_remaining"] = max(0.0, temp_state[selected_eid]["client_capacity_remaining"] - allocated_pct)
                temp_state[selected_eid]["client_project_count"] += 1

                # Build mock RankedCandidate to generate full explainability and match-level mapping
                from .stage5_6_impact_ranking import RankedCandidate, ImpactEstimate
                from .stage4_swap import SwapPlan
                from .stage3_rules import ValidatedCandidate
                from .stage7_plans import _build_option_from_ranked
                from .stage2_intelligence import EnrichedCandidate

                person = selected_person
                sim = sim_map.get(selected_eid, 0.0)

                # Classify match level: above 0 -> Strong Partial (2), 0 -> Availability-Based (4)
                if sim > 0.0:
                    match_level = 2
                    confidence = "HIGH"
                else:
                    match_level = 4
                    confidence = "MEDIUM"

                rc = RankedCandidate(
                    rank=1,
                    employee_id=selected_eid,
                    composite_score=0.90 if sim > 0.0 else 0.50,
                    confidence_band=confidence,
                    score_components={
                        "ranking_score": 0.90 if sim > 0.0 else 0.50,
                        "capability_score": sim,
                        "operational_score": 0.8,
                        "business_bonuses": 0.0,
                        "business_penalties": 0.0,
                        "composite": 0.90 if sim > 0.0 else 0.50,
                        "semantic_similarity": sim,
                        "skill_confidence": sim,
                        "coe_match": 1.0,
                        "location": 1.0
                    },
                    validated=ValidatedCandidate(
                        candidate=None,
                        passed=True,
                        violations=[],
                        soft_penalty=0.0,
                        soft_bonus=0.0,
                        geo_routing_score=1.0,
                        coe_routing_score=1.0,
                        failure_reason=None
                    ),
                    plan=SwapPlan(
                        target_employee_id=selected_eid,
                        chain=[], depth=0,
                        overall_confidence=1.0,
                        estimated_start_delay_days=0, is_feasible=True,
                        infeasible_reason=None,
                        candidate_type="DIRECT"
                    ),
                    impact=ImpactEstimate(
                        employee_id=selected_eid,
                        plan_type="DIRECT",
                        source_project_health_delta=0.0,
                        source_project_risk_increase=0.0,
                        team_loss_fraction=0.0,
                        knowledge_loss_score=0.0,
                        daily_rate=600.0,
                        weekly_revenue_contribution=3000.0,
                        revenue_at_risk_weekly=0.0,
                        estimated_start_delay_days=0,
                        client_impact="None",
                        delivery_risk_change=0.0,
                        utilization_change_pct=0.0,
                        acceptable=True,
                        rejection_reason=None
                    ),
                    job_name=person["job_name"],
                    location=person["location"],
                    geo_cluster=person.get("geo_cluster", "India"),
                    seniority_tier=int(person.get("seniority_tier", 3)),
                    available_capacity_pct=allocated_pct,
                    primary_coe=person.get("primary_coe", "Consulting"),
                    avg_skill_score=float(person.get("avg_skill_score", 0.0)),
                    top_skills_display=[str(s) for s in (person.get("top_skills", []) or [])[:5]],
                    role_name=role_name,
                    required_tier=3,
                    required_pct=allocated_pct
                )

                # Wrap in EnrichedCandidate for explanation generator
                ec = EnrichedCandidate(
                    employee_id=selected_eid,
                    job_name=person["job_name"],
                    location=person["location"],
                    seniority_tier=int(person.get("seniority_tier", 3)),
                    primary_coe=person.get("primary_coe", "Consulting"),
                    util_pct=float(person.get("utilisation", 0.0)),
                    effective_availability=allocated_pct,
                    has_skill_data=bool(person.get("skill_text")),
                    skill_breadth=len(person.get("top_skills", []) or []),
                    avg_skill_score=float(person.get("avg_skill_score", 0.0)),
                    top_skills=[],
                    similar_project_count=1,
                    client_experience_score=1.0,
                    on_red_project=False,
                    on_amber_project=False,
                    health_penalty=0.0,
                    ramp_down_flag=False,
                    predicted_available_date=None,
                    days_to_soonest_end=None,
                    is_bau_only=False,
                    semantic_score=sim,
                    skill_confidence=sim,
                    competency_confidence=0.8,
                    domain_experience_score=0.9,
                    active_project_ids=[],
                    current_project_contexts=[]
                )
                rc.validated.candidate = ec

                staff_option = _build_option_from_ranked(rc, likely_start, match_level=match_level)
                options_allocated.append((staff_option, None, allocated_pct))
                globally_assigned.add(selected_eid)
            else:
                # Fallback to extend start or hire
                from .stage4_swap import BackfillResult as _BR
                from .stage7_plans import _build_hire_option, _build_extend_start_fallback_option

                is_urgent = (
                    client_priority == "Gold" or 
                    str(pipeline_project.get("priority") or "").lower() == "urgent" or
                    str(pipeline_project.get("client_priority") or "").lower() == "gold"
                )

                if is_urgent:
                    backfill_res = _BR(
                        role_id=f"{pipeline_id}::{required_role}::{idx}",
                        role_name=role_name,
                        required_tier=3,
                        required_pct=required_pct,
                        primary_plan=None,
                        alternative_plans=[],
                        hire_signal=True,
                        hire_urgency="IMMEDIATE" if client_priority == "Gold" else "URGENT",
                        extend_start_date_signal=False,
                        estimated_availability_date=None,
                        passing_candidates=[],
                        gap_reason="All internal candidates at concurrent project limit"
                    )
                    backfill_res.required_role = required_role
                    hire_opt = _build_hire_option(required_role, required_pct, backfill_res)
                    extend_opt = _build_extend_start_fallback_option(required_role, required_pct, likely_start)
                    options_allocated.append((hire_opt, extend_opt, required_pct))
                else:
                    extend_opt = _build_extend_start_fallback_option(required_role, required_pct, likely_start)
                    options_allocated.append((extend_opt, extend_opt, required_pct))

            project_allocations_unresolved.append({
                "role_name": role_name,
                "required_role": required_role,
                "idx": idx,
                "allocations": options_allocated
            })

        # Count total slots and filled slots across the project to calculate fill ratio
        total_slots = 0
        filled_slots = 0
        for item in project_allocations_unresolved:
            for opt_tuple in item["allocations"]:
                total_slots += 1
                if opt_tuple[1] is None: # Direct/swap candidate
                    filled_slots += 1
                    
        allow_hire = client_priority in ("Gold", "Silver")
        
        # Now resolve and build final role plans!
        for item in project_allocations_unresolved:
            role_name = item["role_name"]
            required_role = item["required_role"]
            idx = item["idx"]
            allocations = item["allocations"]
            
            for opt_hire, opt_extend, val in allocations:
                if opt_extend is not None:
                    # It was a gap!
                    s_opt = opt_hire if allow_hire else opt_extend
                else:
                    # It was filled internally!
                    s_opt = opt_hire
                    
                sub_role_name = role_name
                if len(allocations) > 1:
                    sub_role_name = f"{role_name} (Part)"
                    
                is_hire = s_opt.plan_type == "D_HIRE"
                is_extend = s_opt.plan_type == "E_EXTEND_START"
                gap = is_hire or is_extend
                
                r_plan = RoleStaffingPlan(
                    role_id=f"{pipeline_id}::{required_role}::{idx}_{s_opt.plan_type}_{val}",
                    role_name=sub_role_name,
                    seniority_tier=3,
                    required_role=required_role,
                    required_pct=val,
                    recommended_option=s_opt,
                    all_options=[s_opt],
                    gap_detected=gap,
                    gap_reason=(
                        "External hire required — all internal options exhausted" if is_hire
                        else ("Project start date extension recommended" if is_extend else None)
                    ),
                    hire_signal=is_hire,
                    hire_urgency=str(pipeline_project.get("priority") or "Medium") if is_hire else "Low",
                )
                role_plans.append(r_plan)


        timings["allocation"] = round(time.time() - t_allocation, 3)

        # Stage 9: Validation & Audit Check
        # Automatically validate database/audit invariants before committing
        for eid, stats in temp_state.items():
            if stats["capacity_remaining"] < -0.01:
                print(
                    f"[Engine] [Warning] Stage 9 Audit: Employee {eid} exceeds 100% allocation ceiling. "
                    f"Remaining capacity: {stats['capacity_remaining']:.1f}%."
                )

        # Commit Allocations to SQLite state database only if commit is True
        if commit:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            for rp in role_plans:
                opt = rp.recommended_option
                # Level 5 (E_EXTEND_START) and Level 6 (D_HIRE) are informational only
                # — do not deduct capacity from any employee in the state DB
                if opt and opt.recommended_employee_id and opt.plan_type not in ("E_EXTEND_START", "D_HIRE"):
                    eid = opt.recommended_employee_id
                    val = rp.required_pct
                    self.globally_allocated_in_run.add(eid)

                    # If this option has redistribution results, update existing allocations in DB
                    redist_res = getattr(opt, "redistribution_result", None)
                    if redist_res and redist_res.feasible and redist_res.adjustments:
                        for adj in redist_res.adjustments:
                            cursor.execute("""
                                UPDATE allocations
                                SET allocation_percent = ?
                                WHERE employee_id = ? AND project_id = ?
                            """, (adj.allocation_after, eid, adj.project_id))

                    # Update employees stats to match temp_state exactly to avoid drift
                    cursor.execute("""
                        UPDATE employees
                        SET capacity_remaining = ?,
                            utilisation = ?,
                            client_capacity_remaining = ?,
                            client_project_count = client_project_count + 1
                        WHERE employee_id = ?
                    """, (
                        temp_state[eid]["capacity_remaining"],
                        temp_state[eid]["utilisation"],
                        temp_state[eid]["client_capacity_remaining"],
                        eid
                    ))

                    # Calculate end date for committed allocation
                    try:
                        weeks_val = float(pipeline_project.get("num_weeks", 12.0))
                        if pd.isna(weeks_val) or weeks_val <= 0:
                            weeks_val = 12.0
                    except Exception:
                        weeks_val = 12.0
                    likely_start_raw = pipeline_project.get("likely_start_str") or ""
                    lk_dt = pd.to_datetime(str(likely_start_raw).split(" ")[0], dayfirst=True, errors="coerce")
                    if pd.isna(lk_dt):
                        lk_dt = pd.Timestamp.now()
                    end_dt = lk_dt + pd.Timedelta(weeks=weeks_val)
                    alloc_end_str = end_dt.strftime("%Y-%m-%d")

                    cursor.execute("""
                        INSERT INTO allocations (employee_id, project_id, role, allocation_percent, role_required, project_type, role_category, allocated_end_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (eid, pipeline_id, rp.role_name, val, rp.required_role, "CLIENT", temp_state[eid]["role_category"], alloc_end_str))

                    # Log audit trail to explainability log
                    cursor.execute("""
                        INSERT INTO allocation_logs (
                            employee_id, project, role, semantic_score, skill_score, coe_score, tier_score, geo_score,
                            capacity_before, capacity_after, reason_selected, reason_rejected
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        eid, pipeline_id, rp.role_name,
                        opt.score_breakdown.get("semantic_similarity", 0.0),
                        opt.score_breakdown.get("skill_confidence", 0.0),
                        opt.score_breakdown.get("coe_match", 0.0),
                        opt.score_breakdown.get("composite", 0.0),
                        opt.score_breakdown.get("location", 0.0),
                        opt.available_capacity_pct + val, opt.available_capacity_pct,
                        f"[{opt.match_label}] Selected via 6-level hierarchy. Score: {opt.composite_score:.3f}.",
                        None
                    ))
            conn.commit()
            conn.close()


        # ── Stage 9b: Pre-return Recommendation Validation ───────────────────
        # Run 7 validation checks on every recommended option.
        # If a recommendation fails, automatically try the next best option.
        # This is the final safety gate before the plan is returned to the caller.
        t9b = time.time()
        validator = RecommendationValidator(self.cfg, ds=self.ds)
        role_plans = validator.validate_and_repair(
            role_plans=role_plans,
            temp_state=temp_state,
            globally_assigned=globally_assigned,
            pipeline_id=pipeline_id,
            pipeline_start=likely_start,
            client_priority=client_priority,
        )
        timings["stage9b_validation"] = round(time.time() - t9b, 3)

        # Build Project-Level Plan
        project_plan = plan_generator.build_project_plan(pipeline_project, role_plans)

        # ── Stage 8: LLM ─────────────────────────────────────────────────────
        t8 = time.time()
        llm_output = self.llm.enrich(project_plan)
        timings["stage8_llm"] = round(time.time() - t8, 3)

        elapsed = round(time.time() - t_total, 2)
        print(f"[Engine] Stateful run completed in {elapsed}s.")

        return RecommendationResult(
            pipeline_id=pipeline_id,
            client=client,
            project_plan=project_plan,
            llm_output=llm_output,
            elapsed_seconds=elapsed,
            stage_timings=timings,
        )

    def undo_allocations(self, pipeline_id: str):
        import sqlite3
        db_path = self.get_db_path()
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Find allocations to revert
        cursor.execute("""
            SELECT employee_id, allocation_percent
            FROM allocations
            WHERE project_id = ?
        """, (pipeline_id,))
        rows = cursor.fetchall()

        if rows:
            print(f"[Engine] Reverting allocations for: {pipeline_id}")
            for eid, alloc_pct in rows:
                cursor.execute("""
                    UPDATE employees
                    SET capacity_remaining = capacity_remaining + ?,
                        utilisation = utilisation - ?,
                        client_capacity_remaining = client_capacity_remaining + ?,
                        client_project_count = MAX(0, client_project_count - 1)
                    WHERE employee_id = ?
                """, (alloc_pct, alloc_pct, alloc_pct, eid))

            # Delete allocations
            cursor.execute("DELETE FROM allocations WHERE project_id = ?", (pipeline_id,))
            # Delete logs
            cursor.execute("DELETE FROM allocation_logs WHERE project = ?", (pipeline_id,))
            conn.commit()
            print(f"[Engine] Successfully reverted {len(rows)} allocations for {pipeline_id}.")
        else:
            print(f"[Engine] No allocations found to revert for: {pipeline_id}")

        conn.close()

    # ── All projects by priority ──────────────────────────────────────────────

    def run_all(self, top_n: Optional[int] = None, client_filter: Optional[str] = None, project_filter: Optional[str] = None) -> List[RecommendationResult]:
        """
        Run recommendations for all pipeline projects globally sorted in custom priority order.
        If top_n is set, only process the top N projects.
        """
        import pandas as pd
        self.globally_allocated_in_run = set()
        # Reset DB on run_all
        self.init_db(force_reset=True)

        projects = self.ds.pipeline_projects.copy()
        projects = projects.drop_duplicates(subset=["pipeline_id"])

        # Sort projects in the exact priority order globally
        def project_sort_key(r):
            client_name = str(r["client"] or "").strip().lower()
            
            sow_val = r["sow_signed"]
            sow_signed = bool(sow_val) if pd.notna(sow_val) else False
            
            client_prio = str(r["client_priority"] or "").strip().lower()
            req_prio = str(r["priority"] or "").strip().lower()
            
            sow_group = 0 if sow_signed else 1
            
            if client_prio == "gold":
                prio_score = 1
            elif req_prio == "urgent":
                prio_score = 2
            elif client_prio == "silver":
                prio_score = 3
            elif req_prio == "high":
                prio_score = 4
            elif client_prio == "bronze":
                prio_score = 5
            elif req_prio == "medium":
                prio_score = 6
            elif client_prio == "other" or client_prio == "others":
                prio_score = 7
            elif req_prio == "low":
                prio_score = 8
            else:
                prio_score = 9
                
            return (sow_group, prio_score, client_name)
            
        projects["sort_score"] = projects.apply(project_sort_key, axis=1)
        projects = projects.sort_values(by=["sort_score"]).drop(columns=["sort_score"])

        if client_filter:
            projects = projects[projects["client"].str.lower() == client_filter.lower()]
        if project_filter:
            projects = projects[projects["pipeline_id"].str.lower() == project_filter.lower()]

        if top_n:
            projects = projects.head(top_n)

        results = []
        for _, row in projects.iterrows():
            pid = str(row["pipeline_id"])
            try:
                result = self.run(pid, commit=True)
                results.append(result)
            except Exception as e:
                print(f"[Engine] ERROR on {pid}: {e}")
                import traceback
                traceback.print_exc()

        # Run Phase 10 Validation rules
        self.validate_database_state(processed_projects=[str(row["pipeline_id"]) for _, row in projects.iterrows()])

        return results


# ─────────────────────────────────────────────────────────────────────────────
# OUTPUT PRINTER
# ─────────────────────────────────────────────────────────────────────────────

def print_result(result: RecommendationResult) -> None:
    """Pretty-print a recommendation result to stdout."""
    plan = result.project_plan
    llm = result.llm_output

    print(f"\n{'='*70}")
    print(f"RECOMMENDATION REPORT")
    print(f"{'='*70}")
    print(f"Pipeline:       {result.pipeline_id}")
    print(f"Client:         {result.client} ({plan.client_priority})")
    print(f"Priority:       {plan.request_priority}")
    print(f"SOW Signed:     {'Yes' if plan.sow_signed else 'No [WARN]'}")
    print(f"Start Date:     {plan.likely_start_date or 'TBD'}")
    print(f"Coverage:       {plan.coverage_pct:.0f}% "
          f"({plan.roles_filled_immediate} immediate, "
          f"{plan.roles_filled_via_swap} swap, "
          f"{plan.roles_filled_via_wait} wait, "
          f"{plan.roles_needing_hire} hire, "
          f"{plan.roles_unfilled} unfilled)")
    print(f"Complexity:     {plan.overall_complexity}")
    print(f"Confidence:     {plan.composite_confidence:.2f}")
    print(f"Generated in:   {result.elapsed_seconds}s")

    print(f"\n{'-'*70}")
    print("EXECUTIVE SUMMARY")
    print(f"{'-'*70}")
    print(llm.executive_summary)

    print(f"\n{'-'*70}")
    print("ROLE-BY-ROLE RECOMMENDATIONS")
    print(f"{'-'*70}")
    for rp in plan.role_plans:
        opt = rp.recommended_option
        role_display = rp.required_role if hasattr(rp, "required_role") else f"Tier {rp.seniority_tier}"
        if opt:
            emp_str = opt.recommended_employee_id or "HIRE"
            delay_str = f" (+{opt.estimated_delay_days}d)" if opt.estimated_delay_days > 0 else ""
            print(f"\n  [{opt.confidence_band}] {rp.role_name} ({role_display}, {rp.required_pct:.0f}%)")
            print(f"  -> {emp_str} | {opt.plan_label}{delay_str}")
            print(f"    Score: {opt.composite_score:.3f} | {opt.implementation_complexity} complexity")
            if opt.swap_chain_summary:
                for step in opt.swap_chain_summary:
                    print(f"    {step}")
        else:
            print(f"\n  [LOW] {rp.role_name} ({role_display}, {rp.required_pct:.0f}%)")
            print(f"  -> [WARN] No option found. {rp.gap_reason or ''}")

    print(f"\n{'-'*70}")
    print("IMPLEMENTATION SEQUENCE")
    print(f"{'-'*70}")
    for action in plan.implementation_sequence:
        print(f"  {action}")

    print(f"\n{'-'*70}")
    print("RISKS & ASSUMPTIONS")
    print(f"{'-'*70}")
    print(llm.risks_and_assumptions)

    if hasattr(plan, "hire_headcount_by_role") and plan.hire_headcount_by_role:
        print(f"\n{'-'*70}")
        print("HIRING REQUIREMENTS")
        print(f"{'-'*70}")
        print(llm.hiring_justification)
        for r, count in sorted(plan.hire_headcount_by_role.items()):
            print(f"  {r}: {count} hire(s) needed")
    elif plan.hire_headcount_by_tier and plan.hire_headcount_by_tier:
        print(f"\n{'-'*70}")
        print("HIRING REQUIREMENTS")
        print(f"{'-'*70}")
        print(llm.hiring_justification)
        for tier, count in sorted(plan.hire_headcount_by_tier.items()):
            print(f"  Tier {tier}: {count} hire(s) needed")

    if any(
        rp.recommended_option and rp.recommended_option.swap_chain_summary
        for rp in plan.role_plans
    ):
        print(f"\n{'-'*70}")
        print("SWAP CHAIN DETAILS")
        print(f"{'-'*70}")
        print(llm.swap_chain_explanation)

    print(f"\n{'-'*70}")
    print("RM ACTION NOTES")
    print(f"{'-'*70}")
    print(llm.rm_action_notes)

    print(f"\n{'-'*70}")
    print("PLAN COMPARISON TABLE")
    print(f"{'-'*70}")
    print(llm.plan_comparison_table)
    print()


def save_result_json(result: RecommendationResult, out_path: str) -> None:
    """Save result to a JSON file for downstream consumption (API, frontend, etc.)."""

    def _serialise(obj):
        if hasattr(obj, "__dataclass_fields__"):
            return {k: _serialise(v) for k, v in asdict(obj).items()}
        if isinstance(obj, (list, tuple)):
            return [_serialise(i) for i in obj]
        if isinstance(obj, dict):
            return {k: _serialise(v) for k, v in obj.items()}
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return obj

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(_serialise(result), f, indent=2, default=str)
    print(f"[Engine] Result saved to {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CoLab Recommendation Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m engine.recommend --pipeline-id "Sigma_3_2026-07-15"
  python -m engine.recommend --all --top 3
  python -m engine.recommend --list
  python -m engine.recommend --pipeline-id "X" --save-json ./output.json
        """,
    )
    parser.add_argument("--pipeline-id", help="Single pipeline project ID to recommend for")
    parser.add_argument("--all", action="store_true", help="Run all pipeline projects")
    parser.add_argument("--top", type=int, default=None, help="Limit --all to top N projects")
    parser.add_argument("--list", action="store_true", help="List all pipeline project IDs")
    parser.add_argument("--save-json", help="Save result(s) to JSON file")
    parser.add_argument("--datacube-dir", default="./datacubes", help="Path to datacubes directory")
    parser.add_argument("--debug", action="store_true", help="Verbose debug output")
    args = parser.parse_args()

    cfg = EngineConfig(datacube_dir=args.datacube_dir, debug=args.debug)
    engine = RecommendationEngine(cfg)

    if args.list:
        print("\nAvailable pipeline projects (by priority):")
        for _, row in engine.ds.pipeline_projects.iterrows():
            print(
                f"  {row['pipeline_id']:<50} "
                f"| {str(row['client_priority'] or ''):<8} "
                f"| {str(row['priority'] or ''):<8} "
                f"| SOW: {'Y' if row['sow_signed'] else 'N'} "
                f"| Start: {row['likely_start_str'] or 'TBD'}"
            )
        return

    if args.pipeline_id:
        result = engine.run(args.pipeline_id)
        print_result(result)
        if args.save_json:
            save_result_json(result, args.save_json)

    elif args.all:
        results = engine.run_all(top_n=args.top)
        for result in results:
            print_result(result)
        if args.save_json and results:
            all_data = []
            for r in results:
                def _serialise(obj):
                    if hasattr(obj, "__dataclass_fields__"):
                        from dataclasses import asdict
                        return asdict(obj)
                    return str(obj)
                all_data.append(_serialise(r))
            with open(args.save_json, "w") as f:
                json.dump(all_data, f, indent=2, default=str)
            print(f"[Engine] {len(results)} results saved to {args.save_json}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
