# CoLab — Resource Recommendation Engine

CoLab is an internal AI-powered resource management tool. It matches open project roles to the best-fit employees using a multi-stage recommendation engine (semantic search + rule-based filtering + LLM reasoning) and surfaces results through a React dashboard.

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.10 + |
| Node.js | 18 + |
| npm | 9 + |

---

## Project Structure

```
CoLab/
├── backend/          # FastAPI server + recommendation engine
│   ├── data/         # Source CSV/Excel files (tracked in git)
│   ├── datacubes/    # Generated Parquet files (git-ignored, see step 2)
│   ├── engine/       # Multi-stage recommendation pipeline
│   ├── main.py       # FastAPI entrypoint
│   ├── generate_datacube.py
│   └── requirements.txt
└── frontend/         # React + Vite dashboard
    └── src/
```

---

## Quick Start

### 1. Clone the repo

```bash
git clone <repo-url>
cd CoLab
```

### 2. Backend setup

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Open `backend/.env` and set:

```env
DATA_DIR=./data
DATACUBE_DIR=./datacubes

# Get a free key from https://openrouter.ai
# Leave blank to fall back to rule-based mode (no LLM)
OPENROUTER_API_KEY=sk-or-...

# Embedding model (default works fine, no change needed)
EMBEDDING_MODEL=all-MiniLM-L6-v2

# Set to True to generate LLM reasoning in results
REASONING=True
```

### 4. Generate the datacubes

> This step pre-processes the source Excel/CSV files into fast Parquet datacubes.
> Run this **once** after cloning, and again whenever the source data changes.

```bash
# Make sure you are inside backend/ with the venv active
python generate_datacube.py
```

### 5. Start the backend server

```bash
uvicorn main:app --reload --port 8000
```

The API will be live at **http://localhost:8000**.  
Interactive docs: **http://localhost:8000/docs**

---

### 6. Frontend setup

Open a **new terminal** from the project root:

```bash
cd frontend

npm install
npm run dev
```

The React dashboard will open at **http://localhost:5173**.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `ModuleNotFoundError` on backend start | Make sure your virtual environment is activated before running `uvicorn`. |
| Datacubes missing / API returns empty results | Re-run `python generate_datacube.py` from the `backend/` directory. |
| Embedding model slow on first run | `sentence-transformers` downloads `all-MiniLM-L6-v2` (~90 MB) on first use. This is normal. |
| CORS errors in the browser | Ensure the backend is running on port `8000` and the frontend on `5173`. |
| LLM reasoning not appearing | Check that `OPENROUTER_API_KEY` is set in `.env` and `REASONING=True`. |

---

## Available API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check |
| `POST` | `/recommend` | Get resource recommendations for a role |
| `GET` | `/docs` | Swagger UI |

---

## Development Notes

- **No datacubes in git** — `backend/datacubes/` is git-ignored. Always run `generate_datacube.py` locally after cloning.
- **No `.env` in git** — Never commit `backend/.env`. Use `backend/.env.example` as the template.
- **Source data is tracked** — All files in `backend/data/` (CSVs, XLSXs) are committed and must be present before generating datacubes.
