"""
stage1_retrieval.py — Semantic Candidate Retrieval

Builds a ChromaDB vector index of all employee profile_text embeddings.
For each pipeline role, retrieves the top-K closest candidates by cosine similarity.

No business rules applied here — maximum recall is the only objective.
Business rules are applied in Stage 3.

Output per role:
  CandidateMatch(employee_id, semantic_score, distance, matched_skills, matched_competencies)
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import EngineConfig, DEFAULT_CONFIG
from .loader import DataStore

# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CandidateMatch:
    employee_id: str
    semantic_score: float          # cosine similarity 0-1
    embedding_distance: float      # lower = closer
    matched_skills: List[str]      # skill names that appeared in role query
    matched_competencies: List[str]


@dataclass
class RetrievalResult:
    role_id: str                   # pipeline_id::role_abbr::index
    pipeline_id: str
    role_name: str
    required_role: str
    allocation_pct: float
    candidates: List[CandidateMatch] = field(default_factory=list)
    query_text: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# QUERY TEXT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

# Skill keywords by role (used to enrich role query text for better retrieval)
ROLE_SKILL_HINTS: Dict[str, str] = {
    "AP": "associate partner partner engagement management client leadership executive strategy commercial",
    "P": "principal technology architect principal architect technical leadership design strategy",
    "TA": "technology solutions architect technical solutions architect principal technology architect gtm architect design strategy",
    "M": "manager engagement manager leadership delivery oversight client engagement advisory delivery",
    "AC": "associate consultant senior associate consultant analyst advisory development",
    "C": "consultant senior consultant advisory delivery consultant client engagement",
    "SC": "solution consultant solutions consultant senior solution consultant senior solutions consultant advisory architecture",
    "SSE": "senior software engineer technical delivery hands-on development implementation",
    "SE": "software engineer development implementation coding programming",
    "ASE": "solutions enabler associate software engineer software coding development",
    "DS": "data scientist senior data science SME machine learning python AI NLP modelling statistics deep learning",
}

# COE-to-skill hints for richer query embedding
COE_SKILL_HINTS: Dict[str, str] = {
    "Data Engineering": "data pipelines ETL spark SQL dbt databricks data warehouse",
    "Data Science & AI": "machine learning python AI NLP modelling statistics deep learning",
    "Full Stack": "react nodejs typescript API microservices backend frontend",
    "TechOps & Automation": "devops kubernetes CI/CD monitoring automation infrastructure cloud",
    "Power BI & Consulting": "power BI reporting dashboards analytics consulting stakeholder",
    "Consulting": "business analysis consulting advisory stakeholder management",
}


def build_role_query(role_row: pd.Series, pipe_project: pd.Series) -> str:
    """Build a rich text query for a pipeline role for semantic retrieval."""
    from .config import standardize_role
    role_code = standardize_role(role_row.get("role_abbr") or "")
    parts = [
        f"Role: {role_row['role_name']}.",
        f"Role Code: {role_code}.",
        f"Allocation: {role_row['allocation_pct']}%.",
        f"Solution: {pipe_project.get('solution', '')}.",
    ]
    if role_row.get("skillset_notes") and len(str(role_row["skillset_notes"])) > 2:
        parts.append(f"Required skills: {role_row['skillset_notes']}.")
    parts.append(ROLE_SKILL_HINTS.get(role_code, ""))
    return " ".join(p for p in parts if p.strip())


# ─────────────────────────────────────────────────────────────────────────────
# SKILL MATCH HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_skill_tokens(text: str) -> List[str]:
    """Extract meaningful tokens from a skill text string."""
    if not text:
        return []
    import re
    tokens = re.split(r"[|,;\n\xa0]+", text.lower())
    cleaned = []
    for t in tokens:
        t = t.strip().strip("()[]")
        if len(t) > 2 and "score:" not in t and "exp:" not in t:
            cleaned.append(t.split("score:")[0].split("(")[0].strip())
    return [t for t in cleaned if t]


def _match_skills(role_query: str, emp_skill_text: str) -> List[str]:
    """Find role query tokens that appear in employee skill text."""
    import re
    role_tokens = set(re.sub(r"[^a-z0-9 ]", " ", role_query.lower()).split())
    emp_tokens = set(re.sub(r"[^a-z0-9 ]", " ", emp_skill_text.lower()).split())
    # Only return meaningful overlaps (>3 chars)
    matched = [t for t in role_tokens & emp_tokens if len(t) > 3]
    return sorted(matched)[:10]


def _match_competencies(role_tier: int, emp_comp_profile: dict) -> List[str]:
    """Return competency dimensions where employee has score ≥ 3."""
    if not emp_comp_profile:
        return []
    return [dim for dim, score in emp_comp_profile.items() if score >= 3]


# ─────────────────────────────────────────────────────────────────────────────
# INDEX BUILDER
# ─────────────────────────────────────────────────────────────────────────────

class TFIDFFallbackIndex:
    """
    TF-IDF cosine similarity fallback for when the embedding model
    cannot be downloaded. Equivalent quality for <1000 employees.
    """
    def __init__(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self._vectorizer = TfidfVectorizer(ngram_range=(1,2), max_features=8000,
                                            sublinear_tf=True, strip_accents="unicode")
        self._matrix = None
        self._employee_ids: List[str] = []

    def fit(self, texts: List[str], employee_ids: List[str]) -> None:
        from sklearn.preprocessing import normalize
        self._matrix = normalize(self._vectorizer.fit_transform(texts))
        self._employee_ids = employee_ids
        print(f"[Retrieval] TF-IDF index built: {len(employee_ids)} profiles, "
              f"{self._matrix.shape[1]} features")

    def query(self, query_text: str, top_k: int, eligible_ids: Optional[List[str]]) -> List[tuple]:
        import numpy as np
        from sklearn.metrics.pairwise import cosine_similarity as cos_sim
        from sklearn.preprocessing import normalize
        q = normalize(self._vectorizer.transform([query_text]))
        sims = cos_sim(q, self._matrix).flatten()
        if eligible_ids is not None:
            eligible_set = set(eligible_ids)
            mask = np.array([eid in eligible_set for eid in self._employee_ids])
            sims = sims * mask
        top_idx = np.argsort(sims)[::-1][:top_k]
        return [
            (self._employee_ids[i], round(float(sims[i]), 4), round(1.0 - float(sims[i]), 4))
            for i in top_idx if sims[i] > 0
        ]


class EmbeddingIndex:
    """
    Manages vector index for employee profiles.
    Uses ChromaDB + SentenceTransformers when available.
    Falls back to TF-IDF cosine similarity (sklearn) when HuggingFace is unreachable.
    """

    def __init__(self, cfg: EngineConfig = DEFAULT_CONFIG):
        self.cfg = cfg
        self._model = None
        self._collection = None
        self._employee_ids: List[str] = []
        self._loaded = False
        self._tfidf: Optional[TFIDFFallbackIndex] = None
        self._use_tfidf = False

    def _try_load_transformer(self):
        try:
            from sentence_transformers import SentenceTransformer
            print(f"[Retrieval] Loading embedding model: {self.cfg.retrieval.embedding_model}")
            self._model = SentenceTransformer(self.cfg.retrieval.embedding_model)
            return True
        except Exception as e:
            print(f"[Retrieval] Embedding model unavailable ({type(e).__name__}). Using TF-IDF fallback.")
            self._use_tfidf = True
            return False

    def _get_model(self):
        if self._model is None and not self._use_tfidf:
            self._try_load_transformer()
        return self._model

    def _get_collection(self):
        if self._collection is None:
            import chromadb
            client = chromadb.PersistentClient(path=self.cfg.retrieval.chroma_persist_dir)
            try:
                self._collection = client.get_collection(self.cfg.retrieval.collection_name)
                self._loaded = self._collection.count() > 0
                if self._loaded:
                    print(f"[Retrieval] Loaded existing index: {self._collection.count()} profiles")
            except Exception:
                self._collection = client.create_collection(
                    name=self.cfg.retrieval.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
        return self._collection

    def build(self, people: pd.DataFrame, force_rebuild: bool = False) -> None:
        """Embed all employee profiles. Uses SentenceTransformers+ChromaDB or TF-IDF fallback."""
        # Try transformer first; fall back to TF-IDF if unavailable
        model = self._get_model()
        texts = people["profile_text"].fillna("").tolist()
        ids = people["employee_id"].tolist()

        if self._use_tfidf or model is None:
            # TF-IDF fallback path
            if self._tfidf is None:
                self._tfidf = TFIDFFallbackIndex()
                self._tfidf.fit(texts, ids)
            self._loaded = True
            return

        # ChromaDB + SentenceTransformer path
        collection = self._get_collection()
        if self._loaded and not force_rebuild:
            return

        print(f"[Retrieval] Embedding {len(people)} employee profiles...")
        batch_size = 64
        all_ids, all_embeddings, all_metadatas = [], [], []
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            batch_ids = ids[i:i + batch_size]
            batch_embs = model.encode(batch_texts, batch_size=32,
                                      show_progress_bar=False).tolist()
            for eid, emb, row_idx in zip(batch_ids, batch_embs, range(i, i + len(batch_ids))):
                row = people.iloc[row_idx]
                all_ids.append(eid)
                all_embeddings.append(emb)
                all_metadatas.append({
                    "employee_id": eid,
                    "seniority_tier": str(row["seniority_tier"]),
                    "geo_cluster": str(row["geo_cluster"]),
                    "primary_coe": str(row["primary_coe"]),
                })
        chunk = 5000
        for i in range(0, len(all_ids), chunk):
            collection.add(
                ids=all_ids[i:i + chunk],
                embeddings=all_embeddings[i:i + chunk],
                metadatas=all_metadatas[i:i + chunk],
            )
        self._loaded = True
        print(f"[Retrieval] ChromaDB index built: {len(all_ids)} profiles")

    def query(
        self,
        query_text: str,
        top_k: int = 50,
        eligible_ids: Optional[List[str]] = None,
    ) -> List[tuple]:
        """Returns list of (employee_id, cosine_similarity, distance)."""
        # TF-IDF path
        if self._use_tfidf or self._tfidf is not None:
            return self._tfidf.query(query_text, top_k, eligible_ids)

        # ChromaDB path
        model = self._get_model()
        collection = self._get_collection()
        query_emb = model.encode([query_text], show_progress_bar=False)[0].tolist()
        where_filter = {"employee_id": {"$in": eligible_ids}} if eligible_ids else None
        try:
            results = collection.query(
                query_embeddings=[query_emb],
                n_results=min(top_k, collection.count()),
                where=where_filter,
                include=["metadatas", "distances"],
            )
        except Exception:
            results = collection.query(
                query_embeddings=[query_emb],
                n_results=min(top_k * 3, collection.count()),
                include=["metadatas", "distances"],
            )
        ids_out = [m["employee_id"] for m in results["metadatas"][0]]
        distances = results["distances"][0]
        output = []
        for eid, dist in zip(ids_out, distances):
            if eligible_ids is not None and eid not in set(eligible_ids):
                continue
            sim = max(0.0, 1.0 - dist)
            output.append((eid, round(sim, 4), round(dist, 4)))

        return output


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1 — RETRIEVAL
# ─────────────────────────────────────────────────────────────────────────────

class SemanticRetrieval:
    """
    Stage 1: Retrieve top-K semantically closest candidates per role.
    No business rules — maximum recall.
    """

    def __init__(self, ds: DataStore, cfg: EngineConfig = DEFAULT_CONFIG):
        self.ds = ds
        self.cfg = cfg
        self.index = EmbeddingIndex(cfg)
        self.index.build(ds.people)

    def retrieve_for_role(
        self,
        role_row: pd.Series,
        pipe_project: pd.Series,
        excluded_ids: Optional[set] = None,
    ) -> RetrievalResult:
        """
        Retrieve top-K candidates for a single pipeline role slot.

        excluded_ids: employee IDs already assigned to other roles in this project
                      (do not double-allocate in Stage 1 — hard exclusion happens in Stage 3)
        """
        pipeline_id = role_row["pipeline_id"]
        role_id = f"{pipeline_id}::{role_row['role_abbr']}::{role_row.name}"

        query = build_role_query(role_row, pipe_project)
        top_k = self.cfg.retrieval.top_k
        min_sim = self.cfg.retrieval.min_similarity

        # Eligible pool: all people not excluded (Stage 3 applies hard rules)
        excluded = excluded_ids or set()
        from .config import standardize_role
        req_role_std = standardize_role(role_row.get("role_abbr") or "")
        compatibles = self.cfg.rules.role_compatibility.get(req_role_std, [req_role_std])

        eligible_ids = []
        for _, row in self.ds.people.iterrows():
            eid = row["employee_id"]
            if eid in excluded:
                continue
            cand_role_std = standardize_role(row["job_name"])
            if cand_role_std in compatibles:
                eligible_ids.append(eid)

        raw_results = self.index.query(query, top_k=top_k, eligible_ids=eligible_ids)

        from .config import standardize_role
        role_code = standardize_role(role_row.get("role_abbr") or "")

        candidates = []
        for eid, sim, dist in raw_results:
            if sim < min_sim:
                continue
            person = self.ds.get_person(eid)
            skill_text = person.get("skill_text", "") or ""
            comp_profile = person.get("competency_profile", {})

            matched_skills = _match_skills(query, skill_text)
            matched_comps = _match_competencies(role_code, comp_profile)

            candidates.append(CandidateMatch(
                employee_id=eid,
                semantic_score=sim,
                embedding_distance=dist,
                matched_skills=matched_skills,
                matched_competencies=matched_comps,
            ))

        return RetrievalResult(
            role_id=role_id,
            pipeline_id=pipeline_id,
            role_name=role_row["role_name"],
            required_role=role_code,
            allocation_pct=float(role_row["allocation_pct"]),
            candidates=candidates,
            query_text=query,
        )

    def retrieve_for_project(
        self,
        pipeline_id: str,
        excluded_per_role: Optional[Dict[str, set]] = None,
    ) -> List[RetrievalResult]:
        """
        Retrieve candidates for all role slots in a pipeline project.
        """
        roles = self.ds.pipeline_roles[
            self.ds.pipeline_roles["pipeline_id"] == pipeline_id
        ].reset_index(drop=True)

        project_row = self.ds.pipeline_projects[
            self.ds.pipeline_projects["pipeline_id"] == pipeline_id
        ].iloc[0]

        results = []
        for idx, role_row in roles.iterrows():
            excl = (excluded_per_role or {}).get(role_row["role_abbr"], set())
            result = self.retrieve_for_role(role_row, project_row, excl)
            results.append(result)

        print(f"[Retrieval] {pipeline_id}: "
              f"{len(roles)} roles → "
              f"{sum(len(r.candidates) for r in results)} total candidates")
        return results
