# Coach Nono

AI sim-racing coach for Assetto Corsa Competizione. Captures telemetry on the Windows host, processes it in Docker, and delivers per-lap coaching through a Streamlit dashboard.

---

## Quick-start

```powershell
# 1. Start the pipeline (first run builds images — takes a few minutes)
docker compose up -d db ingest process dashboard-v2

# 2. Apply the extended-telemetry migration (first run only, or after a DB volume wipe)
docker compose exec -T db psql -U coach -d coach < db/init/02_extras.sql

# 3. Start capturing (run on the Windows host, not in Docker)
.\capture\run_capture.ps1

# 4. Open the dashboard
start http://localhost:8502
```

---

## Important commands

### Pipeline (Docker)

| Action | Command |
|--------|---------|
| Start CPU pipeline | `docker compose up -d db ingest process dashboard-v2` |
| Stop everything | `docker compose down` |
| Wipe DB volume (destructive) | `docker compose down -v` |
| Rebuild images after code changes | `docker compose up -d --build` |
| Scale process workers | `docker compose up -d --scale process=4` |
| Start GPU coach (needs Ollama) | `docker compose --profile gpu up -d` |

### Logs

| Action | Command |
|--------|---------|
| Watch ingest live | `docker compose logs -f ingest` |
| Watch all services | `docker compose logs -f` |
| Last 50 lines, then follow | `docker compose logs --tail=50 -f ingest` |
| Watch ingest + process | `docker compose logs -f ingest process` |

### Database

| Action | Command |
|--------|---------|
| Open psql | `docker compose exec db psql -U coach -d coach` |
| Apply extras migration | `docker compose exec -T db psql -U coach -d coach < db/init/02_extras.sql` |

### Capture agent (Windows host)

| Action | Command |
|--------|---------|
| Start capture | `.\capture\run_capture.ps1` |
| Start with coordinate recording | `$env:CAPTURE_COORDS = "true"; .\capture\run_capture.ps1` |

### Dashboards

| Dashboard | URL |
|-----------|-----|
| v1 (delta + coaching notes) | http://localhost:8501 |
| v2 (full telemetry) | http://localhost:8502 |

---

## Pipeline flow

```
WINDOWS HOST
  ACC game
    │  shared memory (Win32)
    ▼
  capture_agent.py  ── 50 Hz poll loop
    │  on each lap crossing:
    │    writes  data/raw/<session_id>/lap_NNN.parquet   (telemetry, ~50 Hz samples)
    │    writes  data/raw/<session_id>/lap_NNN.meta.json (lap metadata, written last)
    │    optionally writes  data/coords/<session_id>/lap_NNN.parquet  (XYZ world coords)

DOCKER  (bind-mount: ./data → /data)
  ingest  ── polls data/raw/ every 2 s for *.meta.json
    │  for each lap file:
    │    validates parquet (readable, ≥ 100 samples)
    │    upserts sessions row, inserts laps row (status=pending)
    │    moves parquet → data/laps/<session_id>/
    │    computes per-lap aggregates → extras table (if extended schema)
    │    registers coords path → coordinates table (if coords file present)
    │    deletes meta.json  ← handshake signal "done"
    ▼
  process  ── SKIP LOCKED job queue (scalable: --scale process=N)
    │  for each pending lap:
    │    align: resample onto 1000-point spline grid
    │    delta: cumulative time-delta vs personal best, 20-sector loss table
    │    inputs: 7 detectors (trail-brake, coasting, lockup, overspeed, …)
    │    writes findings → DB  (status=done)
    │    updates PB if faster valid lap
    ▼
  dashboard-v2  (port 8502)
    reads sessions, laps, extras, findings from DB
    displays delta traces, telemetry overlays, coaching notes
```

**Handshake:** capture writes the parquet first, then the `.meta.json`. Ingest only picks up a lap when the meta file exists, so it never sees a half-written parquet. Ingest deletes the meta when done — safe to restart either side at any time.

---

## Data layout

```
data/
  raw/          ← landing zone (capture writes here, ingest drains it)
  laps/         ← permanent parquet store, organised by session_id
  coords/       ← world-coordinate traces (CAPTURE_COORDS=true only)
  findings/     ← per-lap analysis artefacts
  config/
    thresholds.json   ← copy from thresholds.example.json and tune
```

---

## Configuration

Copy `.env.example` to `.env` and adjust as needed. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://coach:coach@db:5432/coach` | Postgres connection |
| `LOG_LEVEL` | `INFO` | `DEBUG` for extras/coords confirmation logs |
| `CAPTURE_COORDS` | `false` | Record XYZ world coordinates per lap |
| `COACH_MODEL` | `qwen2.5:7b-instruct-q4_K_M` | Ollama model for GPU coach |
