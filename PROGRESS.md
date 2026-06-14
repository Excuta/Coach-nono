# PROGRESS

## 2026-06-14 — Phase A scaffolding

**What changed:**
- Created repo skeleton: `.gitignore`, `data/` subdirs (raw/laps/findings/reference/models/archive), `PROGRESS.md`
- Added `.env.example` with all required env vars
- Added `Makefile` with all operating commands from the roadmap
- Created `services/pipeline/Dockerfile` (single image, python -m coach.<module> entrypoint)
- Created `services/pipeline/pyproject.toml` with full dependency set
- Created `services/pipeline/coach/__init__.py` and `coach/config.py` (env-driven config loader)
- Created `db/init/01_schema.sql` with sessions/laps/findings/pbs tables + SKIP LOCKED claim query

**What's next:** Phase B — Tier 0 capture + ingest (schema.py, sources/base.py + acc.py, capture_agent.py, ingest.py)

**Blockers:** None
