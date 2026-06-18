# Coach Nono

A personal AI sim-racing coach for Assetto Corsa Competizione, built around Yahia's wife Nono's voice and coaching style.

Captures full telemetry from ACC's Win32 shared memory on the Windows host (~50 Hz, 71 channels), processes each lap through a Docker pipeline that computes delta traces, detects driving mistakes, and stores per-lap aggregates — then surfaces everything in a Streamlit dashboard. A GPU LLM coach (Ollama) is wired in as an optional profile for natural-language coaching feedback.

---

## Quick-start

```powershell
# 1. Start the pipeline (first run builds images — a few minutes)
docker compose up -d db ingest process dashboard-v2

# 2. First run only: apply the extended-telemetry migration
#    Must stop services first — ALTER TABLE hangs if any connection is open
docker compose stop ingest process dashboard-v2
Get-Content db\init\02_extras.sql | docker compose exec -T db psql -U coach -d coach
docker compose up -d ingest process dashboard-v2

# 3. Start capturing on the Windows host (not in Docker)
.\capture\run_capture.ps1

# 4. Open the dashboard
start http://localhost:8502
```

> **Migration note:** always use `Get-Content ... |` in PowerShell, not `<` — Git Bash on Windows mangles absolute paths with the `<` redirect.

---

## Commands

### Pipeline

| Action | Command |
|--------|---------|
| Start pipeline | `docker compose up -d db ingest process dashboard-v2` |
| Rebuild after code changes | `docker compose up -d --build ingest process dashboard-v2` |
| Stop everything | `docker compose down` |
| Wipe DB volume *(destructive)* | `docker compose down -v` |
| Scale process workers | `docker compose up -d --scale process=4` |
| Start GPU coach | `docker compose --profile gpu up -d` |

### Logs

```powershell
docker compose logs -f ingest            # watch ingest
docker compose logs -f ingest process    # watch both workers
docker compose logs --tail=50 -f ingest  # last 50 lines then follow
```

### Database

```powershell
# Open psql
docker compose exec db psql -U coach -d coach

# Apply a migration safely (stop services first — ALTER TABLE hangs with open connections)
docker compose stop ingest process dashboard-v2
Get-Content db\init\<file>.sql | docker compose exec -T db psql -U coach -d coach
docker compose up -d ingest process dashboard-v2
```

### Capture (Windows host)

```powershell
# Manual launch (current)
.\capture\run_capture.ps1                                        # telemetry only
$env:CAPTURE_COORDS = "true"; .\capture\run_capture.ps1         # + XYZ world coords

# Planned: NSSM Windows Service (see capture/ROBUST_CAPTURE_PLAN.md)
# Once installed, capture starts automatically at login — run_capture.ps1 becomes debug-only
.\capture\install_service.ps1   # one-time install
Start-Service CoachNono-Capture
Stop-Service  CoachNono-Capture
```

### Data layer observability

```powershell
# Snapshot: container states, capture status, pending/processed lap counts, disk space
.\capture\status.ps1

# Live lap feed: watches for new laps as they are written (Ctrl+C to stop)
# Phase 1+: tails the structured log file with formatted output
# Now: polls data/raw/ for new meta.json files
.\capture\watch_laps.ps1
```

### Dashboard

| URL | Contents |
|-----|----------|
| http://localhost:8502 | Full telemetry, delta, coaching notes, session health |

---

## Pipeline flow

```
WINDOWS HOST
  ACC game ──[Win32 shared memory]──► capture_agent.py  (50 Hz)
                                        │
                                        │  per completed lap (atomic write — parquet first, meta last):
                                        ├── data/raw/<session_id>/lap_NNN.parquet   (71-channel telemetry)
                                        ├── data/raw/<session_id>/lap_NNN.meta.json (lap metadata + statics)
                                        └── data/coords/<session_id>/lap_NNN.parquet (XYZ, if CAPTURE_COORDS=true)

DOCKER  (bind-mount ./data → /data)
  ingest  polls data/raw/ every 2 s
    ├── validates parquet (≥100 samples)
    ├── upserts sessions row (with statics: max_rpm, sector_count, player_name, aids…)
    ├── inserts laps row  status=pending
    ├── moves parquet → data/laps/<session_id>/
    ├── computes 31 per-lap aggregates → extras table
    ├── registers coords path → coordinates table
    └── deletes meta.json  ← handshake: ingest done, process can claim

  process  (SKIP LOCKED job queue, scalable)
    ├── align: resample onto 1000-pt spline grid
    ├── delta: cumulative time-delta vs PB, 20-sector loss table
    ├── inputs: 7 detectors → findings (trail-brake, coasting, lockup, overspeed, steering, throttle, short-shift)
    ├── updates PB on faster valid lap
    └── status=done

  dashboard-v2  :8502
    └── reads sessions / laps / extras / findings / coordinates from DB
```

---

## Data layout

```
data/
  raw/          ← landing zone; ingest drains to laps/ within 2 s
  laps/         ← permanent parquet store  (data/laps/<session_id>/lap_NNN.parquet)
  coords/       ← XYZ world-coordinate traces  (CAPTURE_COORDS=true only)
  findings/     ← per-lap analysis artefacts written by process worker
  logs/
    capture/    ← capture agent logs + status.json heartbeat (readable by Docker services)
    sweep/      ← ingest-sweep run log (planned)
  config/
    thresholds.json   ← copy from thresholds.example.json and tune per car/track
```

---

## Telemetry schema

Each lap parquet has **71 columns** at ~50 Hz:

| Group | Channels |
|-------|----------|
| Core | `t`, `lap_time`, `spline`, `distance_m`, `speed`, `throttle`, `brake`, `steer`, `gear`, `rpm`, `fuel` |
| Chassis dynamics | `g_lat`, `g_lon`, `g_vert`, `local_vel_x`, `yaw_rate`, `pitch_rate`, `roll_rate` |
| Per-wheel (×4) | `tyre_temp`, `tyre_press`, `wheel_slip`, `slip_ratio`, `slip_angle`, `wheel_angular_s`, `suspension_travel`, `brake_temp`, `brake_pressure`, `pad_life`, `disc_life` |
| Brakes | `brake_bias`, `front_brake_compound`, `rear_brake_compound` |
| Damage | `car_damage` (5 zones), `suspension_damage` (4 wheels) |
| Engine | `clutch`, `turbo_boost`, `water_temp`, `exhaust_temp`, `used_fuel`, `fuel_per_lap` |
| Aids | `abs_active`, `tc_active`, `tc_level`, `tc_cut_level`, `abs_level`, `engine_map`, `autoshifter_on`, `pit_limiter_on` |
| Environment | `air_temp`, `road_temp`, `wind_speed`, `wind_direction`, `rain_10min`, `rain_30min`, `track_grip_status` |
| Session | `is_in_pit`, `is_in_pit_lane`, `current_sector_index`, `flag`, `position`, `gap_ahead`, `gap_behind`, `delta_lap_time`, `penalty_time`, `is_ai_controlled` |

---

## Configuration

Copy `.env.example` to `.env`. Key variables:

| Variable | Default | Notes |
|----------|---------|-------|
| `DATABASE_URL` | `postgresql://coach:coach@db:5432/coach` | |
| `LOG_LEVEL` | `INFO` | `DEBUG` shows per-lap extras/coords confirmations |
| `CAPTURE_COORDS` | *(off)* | Set to `true` to record XYZ world coordinates |
| `COACH_MODEL` | `qwen2.5:7b-instruct-q4_K_M` | Ollama model used by GPU coach profile |

---

## Architecture notes

- **Capture runs on Windows** — ACC shared memory is a Win32 named object, not accessible inside Docker.
- **Session detection** uses three signals in priority order: (1) `g.session_index` change — increments on every new ACC session; (2) `completed_lap` regression — catches track switches where `session_index` stays the same; (3) statics debounce (150 samples = 3 s) — catches practice-server rejoins with restored state where neither of the above fires.
- **Atomic file writes** — parquet is written to `.parquet.tmp` then `os.replace()`d; meta.json always written last. Ingest will never see a partial lap.
- **`fuel_used_lap` on outlap (lap_index=0)** is unreliable — fuel reading at session start captures garage-fill state, not lap start. Ignore for lap 0.
- **DB migrations** require all services stopped first. `ALTER TABLE sessions` will hang indefinitely if any psycopg2 connection is open (idle connections hold locks in autocommit=False mode).

---

## License
MIT — see [LICENSE](LICENSE).
