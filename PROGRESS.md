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

---

## 2026-06-14 — Phase C closeout + validity bug fix

**What changed:**
- `capture/capture_agent.py`: fixed lap validity — ACC resets `isValidLap` to True on the crossing sample, so reading it only at the boundary always returned True; now latches False across the entire lap
- Resumability confirmed: simulated stale lease (claimed_at = 30 min ago), restarted process container, stale lease reclaimed and lap completed correctly
- Manually corrected laps 4 and 6 in DB to `valid=false` (captured before the fix)

**What's next:** Phase D — Tier 2 input coaching (`inputs.py`)

**Blockers:** None

---

## 2026-06-14 — Phase D: Tier 2 input coaching

**What changed:**
- `inputs.py`: 7 detectors (trail-brake overlap, coasting, lockup/ABS, steering reversal, throttle spike, short-shift, corner overspeed); each emits a finding with severity + fix text
- `config.py`: `thresholds_config` property → `data/config/thresholds.json`
- `process.py`: integrated `inputs.detect()` into the worker; unified `_insert_findings` accepts any finding kind; input findings run on all valid laps including PB-registration laps
- `setups.py`: `load_setup` / `save_setup` (atomic) / `diff_setups` (dot-notation diff) for ACC JSON setup files
- `dashboard.py`: "Coaching notes" section showing input findings ranked by severity with fix text
- `thresholds.example.json`: documented defaults with per-combo override format; copy to `data/config/thresholds.json` to activate

**What's next:** Tune thresholds against real laps (~20–40 laps on one combo)

**Blockers:** None — threshold tuning requires real driving data

---

## 2026-06-18 — Extended telemetry + dashboard_v2

**What changed:**
- Extended capture to 71 channels (full ACC physics/graphics structs via `ExtendedSample`)
- Added `extras` table (31 per-lap aggregates: g-forces, tyre wear, damage, aid usage, temps, etc.)
- Added `coordinates` table (XYZ world positions when `CAPTURE_COORDS=true`)
- Built `dashboard_v2.py` on port 8502: full telemetry explorer, delta, coaching notes, session health, extras
- Pipeline verified end-to-end on Silverstone and Paul Ricard, Ferrari 296 GT3

**What's next:** Robust capture hardening, then threshold tuning

**Blockers:** None

---

## 2026-06-18 — Public GitHub push

**What changed:**
- Scrubbed PII from full git history (git-filter-repo, two passes: compound paths first, then tokens)
- Moved docker-compose credentials to `.env` with `${VAR:-default}` substitution
- Added MIT LICENSE, SECURITY.md, Dependabot config
- Added gitleaks pre-commit hook + `scripts/scan_secrets.ps1` for ongoing auditing
- Pushed to github.com/Excuta/Coach-nono

**What's next:** Robust capture migration

---

## 2026-06-18 — Robust Capture: Phases 1–3

**What changed:**
- Split `capture_agent.py` into modules: `health.py` (heartbeat + lockfile), `recovery.py` (startup scan), `notify.py` (BurntToast + webhook), `logging_setup.py` (rotating JSON logs)
- Replaced NSSM service with Windows Scheduled Task (`CoachNono-Capture`, AtLogon, Interactive) — NSSM rejects MSA credentials on Windows 11; Task Scheduler runs in the user's interactive session without stored password
- Fixed idle heartbeat bug: `raw is None` path (ACC not running) skipped `health.update()`, causing permanently stale heartbeat
- Fixed `dashboard_v2` progress bar crash: ACC sends negative mechanical damage before session init; clamped to `[0.0, 1.0]`

**What's next:** Phase 4 watchdog + tray icon

---

## 2026-06-18 — Robust Capture: Phase 4 + ingest-sweep + bug fixes

**What changed:**
- Watchdog task (`CoachNono-Watchdog`): single-shot PowerShell, repeats every 5 min via Task Scheduler repetition trigger; restarts capture if heartbeat stale >45s, fires BurntToast on restart
- Tray icon (`CoachNono-Tray`): pystray viewer, green/amber/red circle by state, right-click menu (open dashboard, stop/start capture, exit); polls `status.json` every 5s
- Confirmed `ingest-sweep` running (was registered but never started); 30-min adaptive cycle, defers to 5 min when ACC is live
- Fixed concurrent-instance parquet corruption: two capture instances raced on the same `.parquet.tmp` — now uses PID-unique tmp filenames + exponential back-off retry for `os.replace()` (handles Defender/AV holds and sharing violations)
- Fixed `notify.py` terminal flash: BurntToast PowerShell subprocess now spawned with `-WindowStyle Hidden` + `CREATE_NO_WINDOW`; console was briefly visible on every lap completion
- Fixed `ingest.py` validate-before-move: corrupt parquets now stay in `raw/` with `meta.json` intact for retry/inspection instead of stranding in `laps/` with no DB record
- Added `capture/recover_failed_laps.py`: one-shot tool to rebuild parquet + `meta.json` from `.failed.json` dumps; used to recover 8 Silverstone laps from today's incident

**Blockers:** None — Phase E ready when ~20–40 laps of threshold data available

---

## 2026-06-18 — Phase E1: Corner Analysis Engine

**What changed:**
- `db/init/03_corners.sql`: three new tables — `corners` (geometry per track), `corner_stats` (10 metrics per lap × per corner), `corner_baselines` (P10–P90 percentile stats, keyed by game + game_version_major + track + car). Migration applied live without blocking ingest.
- `services/pipeline/coach/analysis/corners.py`: auto-detects corners by finding speed minima in averaged aligned speed traces (scipy `find_peaks` with prominence + distance guards). Extracts 10 metrics: entry/apex/exit speed, brake point, throttle point, coast duration, trail-brake overlap, max lat-g, min slip ratio, steer reversals.
- `services/pipeline/coach/analysis/db_ops.py`: stores corners + corner_stats; recomputes running percentile baselines after each lap.
- `services/pipeline/coach/analysis/findings.py`: generates statistical findings (6 types) when z-score exceeds per-metric threshold vs the driver's own baseline. No user-facing thresholds — sensitivity is auto-calibrated. Requires ≥5 valid laps per corner before activating.
- `services/pipeline/coach/process.py`: wired new analysis after alignment, merged with existing `inputs.py` findings. Fully wrapped in try/except — any failure logs and falls back gracefully.
- `config.py` + `.env.example`: added `GAME_VERSION_MAJOR` (default `"1.10"`) to scope baselines per ACC major version.

**Verified:**
- 6 corners detected on Paul_Ricard from 3 fastest laps
- 30 corner_stats rows populated (5 laps × 6 corners)
- Baselines live at sample_count=5; statistical findings flowing (4 findings on test lap)
- Ingest was never blocked; process service restarted cleanly

**What's next:** Phase E2 — Dashboard revamp (Corner Analysis page, Lap Trends page, enhanced Track Map)
