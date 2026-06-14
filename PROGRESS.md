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

---

## 2026-06-14 — Phase C complete (Tier 1 delta pipeline)

**What changed:**
- `coach/align.py`: `_extract_flying_lap` isolates flying lap from 0/1/2+ crossing buffers using iloc exclusive-end slice; `align()` resamples onto 1000-point spline grid via scipy interp1d
- `coach/delta.py`: `compute_delta` produces cumulative time-delta trace + 20-sector loss table sorted worst-first
- `coach/process.py`: SKIP LOCKED claim loop, stale-lease reclaim on startup, align+delta per lap, auto-updates PB when faster valid lap arrives, writes delta trace parquet + DB findings rows
- `coach/dashboard.py`: Streamlit dark-theme app — session/lap selectors, delta trace Plotly chart, inputs overlay, sector-loss table with severity colouring
- `services/pipeline/Dockerfile` + `pyproject.toml`: fixed two-step build (deps-layer cache, `setuptools.build_meta`)
- Smoke-tested with 11 laps Ferrari 296 GT3 @ Paul Ricard — correct delta values, PB chain updates, dashboard 200 OK at localhost:8501

**What's next:** Phase D — Tier 2 input coaching (`inputs.py` detectors: trail-brake, coasting, ABS/TC, corner overspeed, steering reversals)

**Blockers:** None
