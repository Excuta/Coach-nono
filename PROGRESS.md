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

---

## 2026-06-14 — Phase B implementation (pending smoke test)

**What changed:**
- `coach/schema.py`: `CanonicalSample` + `SessionContext` dataclasses
- `coach/sources/base.py`: `TelemetrySource` Protocol
- `coach/sources/acc.py`: `ACCSource` — reads pyaccsharedmemory, maps to canonical schema; exposes `read_shared_memory()` + `to_sample()` for capture agent
- `db/init/01_schema.sql`: added `capture_id TEXT UNIQUE` to sessions table
- `coach/db.py`: `get_or_create_session`, `lap_id()`, `insert_lap()` helpers
- `capture/capture_agent.py`: 50 Hz poll loop, lap-boundary via `completedLaps`, atomic parquet + meta.json write
- `capture/requirements.txt` + `capture/run_capture.ps1`: host-side launcher with first-run venv setup
- `coach/ingest.py`: 2 s polling loop over raw/, validates parquet, registers sessions/laps in DB (status=pending), moves files to laps/

**What's next:** Smoke test (Yahia drives 1 lap) then Phase C (align, delta, process worker, dashboard)

**Blockers:** Smoke test needs real ACC session
