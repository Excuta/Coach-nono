# Coach Nono — project instructions

## What this is
Personal AI sim-racing coach for ACC. Named after Yahia's wife Nono — her real recorded voice is planned for coaching output (Phase E+, XTTS v2 zero-shot cloning). Always refer to this project as "Coach Nono", never "acc-coach" or "ACC AI Coach".

## Critical non-inferable facts

### Windows / Docker split
- Capture agent runs on the **Windows host** — ACC shared memory is a Win32 named object (`acs physics`, `acs graphics`, `acs static`), inaccessible from inside Docker.
- Everything else runs in Docker. The `./data` directory is bind-mounted as `/data` in all containers.

### pyaccsharedmemory quirks
- `CarDamage` is a plain dataclass (front/rear/left/right/center) — use `dataclasses.astuple()` to unpack, not iteration.
- `car_coordinates` is a fixed 60-slot array indexed by car slot, NOT by `player_car_id`. Find the player's slot via `next(i for i, cid in enumerate(g.car_id) if cid == g.player_car_id)`.
- `session_index` in `GraphicsMap` is the reliable new-session signal — increments on every session change regardless of car/track/type. Do NOT rely on car/track string comparison (statics can lag) or `completed_lap` regression (fails on first-lap restarts).

### DB migrations
- `ALTER TABLE` hangs indefinitely if any service is running — psycopg2 with `autocommit=False` holds idle connections.
- Stop all services before migrating: `docker compose stop ingest process dashboard dashboard-v2`
- Use `Get-Content db\init\<file>.sql | docker compose exec -T db psql -U coach -d coach` in PowerShell. The `<` redirect in Git Bash on Windows mangles absolute paths.
- Migrations in `db/init/` auto-run on a **fresh** volume (`docker-entrypoint-initdb.d`). Existing volumes need manual application.

### DB connection recovery
- `conn()` in `db.py` checks `_conn.closed` but a failed transaction leaves `closed=0`. Use `_conn.get_transaction_status() == TRANSACTION_STATUS_INERROR` to detect and rollback aborted transactions.

### Data notes
- `fuel_used_lap` for `lap_index=0` is unreliable — outlap starts with garage-fill fuel state.
- `lap_path` stored in `laps` table is an absolute container path (`/data/laps/...`). In `dashboard_v2.py`, use `Path(lap_path)` directly (or check `is_absolute()`) — do not unconditionally prepend `cfg.data_dir`.

## Roadmap
See `acc-coach-roadmap.md` — tick boxes as phases complete. Append to `PROGRESS.md` after each session.

**Current status:** Phases A–D complete. Extended telemetry (71 channels, extras table, coordinates table, dashboard_v2) done. Pipeline verified end-to-end on Silverstone with Ferrari 296 GT3.

**Next:** Phase D threshold tuning (~20–40 real laps), then Phase E (setup advisor + LLM coach).

## Commit convention
Use the `nono-commit` skill. Subject: `[claude] <imperative verb phrase>`. No Co-Authored-By trailer.
