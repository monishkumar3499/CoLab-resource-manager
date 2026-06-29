# FastAPI backend for CoLab Recommendation Engine
import math
import os
import sys
from typing import Dict, List, Optional, Any
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add backend to path to allow importing engine module
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from engine.recommend import RecommendationEngine, RecommendationResult
from engine.config import EngineConfig, RankingWeights, DEFAULT_CONFIG

app = FastAPI(title="CoLab Recommendation Engine API", version="1.0.0")

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For local development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Recommendation Engine instance initialized on startup
print("[API] Initializing global recommendation engine...")
engine = RecommendationEngine(force_reset=True)
print("[API] Global recommendation engine ready.")

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def clean_val(v: Any) -> Any:
    """Recursively clean values to make them JSON-serializable (converts NaN/NaT to None)."""
    if isinstance(v, list):
        return [clean_val(x) for x in v]
    if isinstance(v, dict):
        return {k: clean_val(val) for k, val in v.items()}
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if pd.isna(v):
        return None
    return v

def clean_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Clean all records from a Pandas DataFrame for JSON output."""
    cleaned = []
    for r in records:
        row = {}
        for k, v in r.items():
            row[k] = clean_val(v)
        cleaned.append(row)
    return cleaned

def serialize_dataclass(obj: Any) -> Any:
    """Serialize custom dataclasses recursively into JSON-serializable structures."""
    def _serialise(val):
        if hasattr(val, "__dataclass_fields__"):
            return {k: _serialise(v) for k, v in asdict(val).items()}
        if isinstance(val, (list, tuple)):
            return [_serialise(i) for i in val]
        if isinstance(val, dict):
            return {k: _serialise(v) for k, v in val.items()}
        if hasattr(val, "isoformat"):
            return val.isoformat()
        return clean_val(val)
    return _serialise(obj)

# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/overview")
def get_overview():
    """Get high-level summary statistics of the datacubes."""
    try:
        people_count = len(engine.ds.people)
        projects_count = len(engine.ds.projects)
        pipeline_count = len(engine.ds.pipeline_projects)
        roles_count = len(engine.ds.pipeline_roles)
        return {
            "people_count": people_count,
            "projects_count": projects_count,
            "pipeline_count": pipeline_count,
            "roles_count": roles_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pipeline-projects")
def get_pipeline_projects():
    """Get all pipeline projects requesting resources, sorted by priority, with allocation status."""
    try:
        records = engine.ds.pipeline_projects.to_dict(orient="records")
        
        # Get distinct project IDs that have allocations in SQLite
        import sqlite3
        db_path = engine.get_db_path()
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT project_id FROM allocations")
        allocated_ids = {r[0] for r in cursor.fetchall()}
        conn.close()
        
        for r in records:
            pid = str(r.get("pipeline_id"))
            r["allocated"] = pid in allocated_ids
            
        return clean_records(records)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/people")
def get_people():
    """Get the resource pool (all employee details) updated with stateful SQLite capacities."""
    try:
        # Read static data
        df = engine.ds.people.copy()
        # Read stateful capacities from SQLite
        import sqlite3
        db_path = engine.get_db_path()
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT employee_id, capacity_remaining, utilisation FROM employees")
        rows = cursor.fetchall()
        conn.close()
        
        state_map = {r[0]: {"available_capacity_pct": r[1], "effective_util_pct": r[2]} for r in rows}
        
        # Overwrite DataFrame values
        for idx, row in df.iterrows():
            eid = str(row["employee_id"]).strip().upper()
            if eid in state_map:
                df.at[idx, "available_capacity_pct"] = state_map[eid]["available_capacity_pct"]
                df.at[idx, "effective_availability"] = min(100.0, max(0.0, state_map[eid]["available_capacity_pct"]))
                df.at[idx, "effective_util_pct"] = state_map[eid]["effective_util_pct"]
                df.at[idx, "util_pct"] = state_map[eid]["effective_util_pct"]
                
        records = df.to_dict(orient="records")
        return clean_records(records)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/projects")
def get_projects():
    """Get all active client/delivery projects with health scores."""
    try:
        records = engine.ds.projects.to_dict(orient="records")
        return clean_records(records)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config")
def get_config():
    """Get the current ranking weights and default parameters."""
    try:
        return {
            "weights": serialize_dataclass(engine.cfg.weights),
            "rules": serialize_dataclass(engine.cfg.rules),
            "impact": serialize_dataclass(engine.cfg.impact)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class RecommendRequest(BaseModel):
    weights: Optional[Dict[str, float]] = None

@app.post("/api/recommend/{pipeline_id}")
def run_recommend(pipeline_id: str, payload: Optional[RecommendRequest] = None):
    """Run recommendations for a specific pipeline project request as a dry-run/preview, optionally with custom weights."""
    try:
        # Check if project exists
        matches = engine.ds.pipeline_projects[
            engine.ds.pipeline_projects["pipeline_id"] == pipeline_id
        ]
        if matches.empty:
            raise HTTPException(status_code=404, detail=f"Pipeline project '{pipeline_id}' not found.")

        # If custom weights are provided, re-create the engine settings
        if payload and payload.weights:
            default_weights_dict = asdict(engine.cfg.weights)
            for k, v in payload.weights.items():
                if k in default_weights_dict:
                    default_weights_dict[k] = v
            
            custom_weights = RankingWeights(**default_weights_dict)
            custom_cfg = EngineConfig(
                retrieval=engine.cfg.retrieval,
                rules=engine.cfg.rules,
                weights=custom_weights,
                impact=engine.cfg.impact,
                llm=engine.cfg.llm,
                datacube_dir=engine.cfg.datacube_dir,
                debug=engine.cfg.debug
            )
            # Run with custom parameters in preview mode
            custom_engine = RecommendationEngine(custom_cfg)
            result = custom_engine.run(pipeline_id, commit=False)
        else:
            # Run with default config in preview mode
            result = engine.run(pipeline_id, commit=False)
            
        return serialize_dataclass(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/allocate/{pipeline_id}")
def commit_allocate(pipeline_id: str, payload: Optional[RecommendRequest] = None):
    """Commit allocations for a specific pipeline project request by running the engine with commit=True."""
    try:
        # Undo any existing allocations first to avoid double allocation
        engine.undo_allocations(pipeline_id)

        # Resolve weights and run with commit=True
        if payload and payload.weights:
            default_weights_dict = asdict(engine.cfg.weights)
            for k, v in payload.weights.items():
                if k in default_weights_dict:
                    default_weights_dict[k] = v
            
            custom_weights = RankingWeights(**default_weights_dict)
            custom_cfg = EngineConfig(
                retrieval=engine.cfg.retrieval,
                rules=engine.cfg.rules,
                weights=custom_weights,
                impact=engine.cfg.impact,
                llm=engine.cfg.llm,
                datacube_dir=engine.cfg.datacube_dir,
                debug=engine.cfg.debug
            )
            custom_engine = RecommendationEngine(custom_cfg)
            result = custom_engine.run(pipeline_id, commit=True)
        else:
            result = engine.run(pipeline_id, commit=True)
            
        return serialize_dataclass(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/undo-allocate/{pipeline_id}")
def undo_allocate(pipeline_id: str):
    """Undo allocations for a specific pipeline project, restoring capacity in the database."""
    try:
        engine.undo_allocations(pipeline_id)
        return {"status": "success", "message": f"Allocations reverted for {pipeline_id}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/run-all")
def run_all_projects():
    """Run recommendations statefully for all pipeline projects."""
    try:
        results = engine.run_all()
        return {"status": "success", "message": f"Calculated plans for {len(results)} projects statefully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generate-excel")
def generate_excel_endpoint():
    """Run allocations globally and fill the Excel workbook."""
    try:
        from fill_resource import run_excel_generation
        run_excel_generation(engine)
        return {"status": "success", "message": "Excel workbook generated successfully."}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download-excel")
def download_excel_endpoint():
    """Download the generated Excel file."""
    from fastapi.responses import FileResponse
    excel_path = os.path.join(os.path.dirname(__file__), "output_excels", "updated_resources.xlsx")
    if not os.path.exists(excel_path):
        raise HTTPException(status_code=404, detail="Excel file not found. Please run Excel generation first.")
    return FileResponse(excel_path, filename="updated_resources.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
