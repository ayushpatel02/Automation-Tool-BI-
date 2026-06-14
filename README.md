# AI-Powered Power BI Report Generator

Generate valid Power BI projects (`.pbip`) from a natural-language description of the
report you want. Connect a data source, pick an AI model, describe the report, and the
tool produces a TMDL semantic model + PBIR report you can open in Power BI Desktop and
refine through an AI chat loop.

> The generation layer is an orchestration shell — the LLM produces the actual TMDL and
> PBIR artifacts as structured text, which the tool validates, repairs, and assembles
> into a `.pbip` project.

## Architecture

```
Streamlit frontend (8501)  ──REST + SSE──>  FastAPI backend (8000)
                                              ├── Connector layer (SQL / warehouse / files)
                                              ├── Schema profiler
                                              ├── LLM router (LiteLLM — Gemini / Claude / GPT)
                                              ├── Generation pipeline (TMDL → PBIR, validate + retry)
                                              ├── PBIP assembler
                                              └── PostgreSQL / SQLite
```

See [`docs/PLAN.md`](docs/PLAN.md) for the full implementation plan.

## Quick start (development)

```bash
# Backend
cd backend
uv sync                       # or: pip install -e .
cp .env.example .env          # set ENCRYPTION_KEY, JWT_SECRET, etc.
uvicorn app.main:app --reload # http://localhost:8000  (docs at /docs)

# Frontend (separate terminal)
cd frontend
pip install -r requirements.txt
streamlit run app.py          # http://localhost:8501
```

Or run everything with Docker:

```bash
docker compose -f docker-compose.dev.yml up --build
```

## Status

This repository is an in-progress build of the Phase 1 + 2 plan. The foundational
vertical slice is in place: auth (with email-based password reset), connectors,
profiler, LLM router, two-stage generation, assembler, validation + retry loop, REST
API, and a Streamlit UI. Cloud warehouse connectors, the refinement loop, and full CI
hardening are tracked in the milestone plan.

### Password reset

Users who forget their password can request a reset from the **Forgot password** tab on
the login screen. The backend issues a single-use, time-limited token (stored only as a
SHA-256 hash) and emails a link to the **Reset Password** page. Configure SMTP via the
`SMTP_*` variables in `.env`; if `SMTP_HOST` is left blank, the reset link/code is logged
to the backend console instead of sent — convenient for local development.

### Important format notes (verified June 2026)

- **PBIR** is the default report format in Power BI Service (since Jan 2026) and Power BI
  Desktop (since March 2026, still preview). Open generated projects in **Power BI
  Desktop ≥ March 2026 with the PBIR preview feature enabled**.
- **PBIR validation** uses the public JSON schemas at
  `https://developer.microsoft.com/json-schemas/fabric/item/report/definition/...`.
- **TMDL validation** has no pure-Python option; it shells out to Tabular Editor 2 CLI
  (bundled in the Docker image). Locally it degrades to structural checks if TE2 is absent.
- LLM calls route through **LiteLLM** so the Gemini June-2026 API migration
  (`response_mime_type` → `response_format`) is handled transparently.

## License

TBD
