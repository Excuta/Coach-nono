# Coach Nono — Build Roadmap (Claude Code)

**Audience:** Claude Code (implementer) + Yahia (driver/operator).
**Hardware:** Windows sim PC, RTX 5060 (8 GB), Docker Desktop + WSL2, 32 GB RAM.
**Scope of THIS roadmap:** build Tiers 0–3 to completion. Tiers 4–5 are an outline to refine later.

> **How to use this file (Claude Code):** Treat the "Build Checklist" as the source of truth.
> Tick boxes as you complete them, and after each work session append a 3–5 line entry to
> `PROGRESS.md` (what changed, what's next, any blockers). This makes the project resumable across
> your own sessions, mirroring the runtime pausability below. Don't start coding services before
> the scaffolding/checklist items above them are done.

---

## 0. Guiding principles

1. **It's a pipeline, not one model.** Tiers 0–2 are signal processing + heuristics (no ML).
   Tier 3 adds an LLM for the coaching voice. GPU is used *only* by Tier 3+.
2. **GPU is optional and detachable.** CPU pipeline runs without the GPU. GPU services live behind
   a Compose profile so you can bring them up/down and keep the card free when you want it.
3. **Everything is idempotent and resumable.** Stop containers anytime; on restart, work continues.
   Losing in-flight progress means at most re-processing one lap.
4. **Portability seam, ACC implementation.** Define a canonical telemetry schema + a `TelemetrySource`
   interface now; implement only the ACC adapter. Other games become new adapters later, downstream
   code unchanged. (This costs little; we build the seam, not the other adapters.)
5. **Standards over cleverness.** Postgres job-queue with `FOR UPDATE SKIP LOCKED`, named volumes,
   `restart: unless-stopped`, atomic file writes. Nothing exotic.

---

## 1. Architecture

```
WINDOWS HOST (not containerized — shared memory is a Win32 object)
  capture agent  --reads ACC shared memory (~333Hz physics / 60Hz graphics)
                 --segments into laps, writes ONE parquet + meta per lap (atomically)
                 --> ./data/raw/<session>/<lap>.parquet   (bind-mounted into the stack)

DOCKER COMPOSE STACK
  db (Postgres/TimescaleDB)   <-- metadata, job queue, findings, sessions   [volume: db-data]
  ingest   (CPU worker)       watches ./data/raw, validates, registers laps, moves raw->laps
  process  (CPU worker, SCALABLE)  pulls pending laps, runs align+delta+input-heuristics, writes findings
  dashboard(CPU)              Streamlit UI: delta traces, per-corner findings, history
  coach-llm(GPU, profile=gpu) Ollama serving a 7-8B Q4 model
  coach    (GPU, profile=gpu) turns findings+history into coaching + session plans (calls coach-llm)
```

Data flow: `capture → raw/ → ingest → laps/ + db rows(status=pending) → process (claims job) →
findings/ + db rows(status=done) → dashboard / coach reads findings`.

**Why capture is host-side:** ACC exposes telemetry via Windows named shared memory
(`acpmf_physics`, `acpmf_graphics`, `acpmf_static`). A WSL2/Linux container can't read Win32 shared
memory, so the capture agent runs as a small Python process on the host and writes files into the
bind-mounted `./data` dir. Everything downstream is containerized.

---

## 2. Repo layout

```
acc-coach/
  docker-compose.yml
  .env.example
  README.md
  PROGRESS.md                 # Claude Code appends a log here each session
  Makefile                    # or justfile — convenience commands
  db/
    init/01_schema.sql        # tables + indexes, runs on first db boot
  capture/                    # HOST-side (not built into an image)
    capture_agent.py
    requirements.txt
    run_capture.ps1           # convenience launcher (Windows)
  services/
    pipeline/                 # one image, multiple entrypoints (ingest/process/dashboard/coach)
      Dockerfile
      pyproject.toml
      coach/
        __init__.py
        config.py
        db.py                 # connection pool, job-claim helpers
        schema.py             # canonical telemetry dataclasses
        sources/
          base.py             # TelemetrySource interface (portability seam)
          acc.py              # ACC adapter (field mapping; used by capture_agent)
        ingest.py             # raw -> registered laps
        align.py              # resample by distance/spline; lap alignment
        delta.py              # cumulative time-delta + per-corner loss
        inputs.py             # Tier 2 heuristics (trail brake, coasting, lockups, ...)
        setups.py             # Tier 1.5/2.5 read/write + rule advisor
        process.py            # worker: claim lap -> align/delta/inputs -> findings
        coach_service.py      # Tier 3: findings+history -> LLM -> coaching + session plan
        sessions.py           # training-session manager, PB tracking
        dashboard.py          # Streamlit
  data/                       # bind-mounted; gitignored
    raw/   laps/   findings/   reference/   models/   archive/
```

---

## 3. Docker & operations design

### Profiles (keep the GPU free at will)
- Default `docker compose up -d` starts **CPU-only**: `db, ingest, process, dashboard`.
- GPU services (`coach-llm, coach`) sit behind `profiles: ["gpu"]`. Add them only when wanted:
  `docker compose --profile gpu up -d`. Free the card again with
  `docker compose --profile gpu down` (CPU pipeline keeps running untouched).

### Parallelism
- `process` is stateless and scalable: `docker compose up -d --scale process=4`.
- Workers claim distinct laps via `SELECT ... FOR UPDATE SKIP LOCKED` so replicas never collide.
- Start with 1; scale up to burn through a backlog, scale back to 1 (or 0) to idle.

### Pausability / resumability (standard patterns — keep it simple)
- **Idempotent outputs:** `lap_id` is a hash of (session_id, lap_index, content). Re-processing
  overwrites identical files; safe to repeat.
- **Job queue with lease:** laps have `status ∈ {pending, processing, done, failed}` + `claimed_at`.
  A worker claims with SKIP LOCKED, heartbeats `claimed_at`. On startup, any `processing` row whose
  lease is older than N minutes is reset to `pending` (covers a killed worker).
- **Atomic file writes:** write to `*.tmp` then `os.rename` (atomic on same volume). A half-written
  parquet never gets ingested.
- **Named volumes** (`db-data`, `ollama-models`) survive `docker compose down`. They are only wiped
  by `docker compose down -v` — **don't run `-v` unless you mean it.**
- **Graceful stop:** workers trap SIGTERM, finish or release the current lap, exit. Jobs are seconds
  long in Tiers 0–3, so a hard kill costs at most one lap, which is reprocessed automatically.
- `restart: unless-stopped` on long-lived services so a host reboot brings the stack back.

### Storage / retention (the "storage container")
- `db` + its volume is the durable store for metadata, findings, and session history.
- File lake under `./data`: `raw/` (inbox) → `laps/` (validated source of truth) → `findings/`.
- Retention: keep `raw/` until the lap's `status=done`, then move it to `archive/` (or prune after
  X days via a tiny periodic job). Reference/PB laps in `reference/` are never auto-pruned.

---

## 4. Canonical telemetry schema (portability seam)

Define one normalized sample shape; every game adapter maps into it. Downstream code only ever sees
this — that's what makes the system portable.

```python
# coach/schema.py
@dataclass
class CanonicalSample:
    t: float            # seconds since session start
    lap_time: float     # current lap time (s)
    spline: float       # normalized lap position 0..1   <-- alignment key
    distance_m: float   # distance into lap (m), if available
    speed: float        # m/s
    throttle: float     # 0..1
    brake: float        # 0..1
    steer: float        # -1..1
    gear: int
    rpm: float
    # optional/extended (None if a game doesn't expose them):
    tyre_temp: tuple | None       # FL,FR,RL,RR
    tyre_press: tuple | None
    abs_active: bool | None
    tc_active: bool | None
    fuel: float | None

@dataclass
class SessionContext:
    game: str           # "acc"
    car: str
    track: str
    session_type: str   # practice/quali/race
    conditions: dict     # temps, tyre compound, etc.
```

```python
# coach/sources/base.py
class TelemetrySource(Protocol):
    def open(self) -> None: ...
    def context(self) -> SessionContext: ...
    def read(self) -> CanonicalSample | None: ...   # one tick, None if not in car
    def close(self) -> None: ...
```

- `coach/sources/acc.py` implements this over `pyaccsharedmemory` (used by the host capture agent).
- Future games = new files (`iracing.py`, `ac.py`, `rf2.py`, `ams2.py`) implementing the same
  interface. **Do not build these now**; just leave the interface + a one-line registry so adding
  one later is mechanical. Heuristic thresholds in `inputs.py` are car/game-tunable via config.

---

## 5. Database schema (sketch — implement in db/init/01_schema.sql)

```sql
CREATE TABLE sessions (
  id BIGSERIAL PRIMARY KEY, game TEXT, car TEXT, track TEXT, session_type TEXT,
  conditions JSONB, started_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE laps (
  id TEXT PRIMARY KEY,                 -- lap_id (hash)
  session_id BIGINT REFERENCES sessions(id),
  lap_index INT, lap_time DOUBLE PRECISION, valid BOOLEAN,
  raw_path TEXT, lap_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending|processing|done|failed
  claimed_at TIMESTAMPTZ, attempts INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON laps (status);
CREATE TABLE findings (
  id BIGSERIAL PRIMARY KEY, lap_id TEXT REFERENCES laps(id),
  corner INT, kind TEXT, severity REAL, time_loss_s REAL,
  detail JSONB, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE pbs (   -- personal-best reference per car/track
  game TEXT, car TEXT, track TEXT, lap_id TEXT, lap_time DOUBLE PRECISION,
  PRIMARY KEY (game, car, track)
);
```

Job claim (used by `process` workers):
```sql
UPDATE laps SET status='processing', claimed_at=now(), attempts=attempts+1
WHERE id = (SELECT id FROM laps WHERE status='pending'
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING *;
```

---

## 6. YOUR data-gathering protocol  ← Yahia does this

You don't need to flip any in-game setting for shared-memory capture — it's always live. You just
run the capture agent while you drive. (MoTeC `.ld` export is a separate optional thing; skip for now.)

### Every driving session
1. Launch ACC. Pick **one car + one track** and stick with it for the session (consistency).
2. Use a **Practice** session with **fixed conditions** (set track/air temp, time, tyres) so laps
   are comparable.
3. Start the capture agent **before** going on track: `./capture/run_capture.ps1`
   (it auto-detects when you're in the car and writes one parquet per completed lap).
4. Drive. When done, stop the agent (Ctrl+C). Then run `docker compose up -d` (or leave it running)
   to ingest + process.
5. Save the setup you used (ACC writes it under `Documents\...\Setups\<car>\<track>\`); note its name.

### What to actually drive (so the analyzer has good material)
- **Reference run first:** 5–10 **clean, pushing** flying laps, no offs. This seeds your PB baseline.
- **Coaching runs:** then drive normally — *including* your usual mistakes. Variation is what the
  input analyzer learns from. Don't drive artificially clean.
- **Volume targets:**
  - Tier 1 (delta vs your PB): as few as **2–3 clean laps** is enough to see something.
  - Tier 2 (reliable heuristics): aim for **~20–40 laps** on the car/track.
  - Tier 3 (useful trend/session coaching): a **handful of sessions** across a few days.
  - (Later) Tier 4 ML: **hundreds–thousands** of laps; keep everything from day one.
- **Generalization pass (once the pipeline works):** repeat on **2–3 other car/track combos** to
  confirm nothing is hard-coded to one combo.
- **Optional labels:** after a session, jot which laps felt good/bad (1 line each). Cheap now,
  valuable for Tier 4 later.

### Reference laps beyond yourself (optional, improves coaching)
- Record an **alien AI lap** (set AI to 100%, capture a hotlap) as a stretch reference, or
- Drop in a fast reference lap from a telemetry-sharing source into `./data/reference/`.

---

## 7. Build checklist (running — tick as you go)

### Phase A — Scaffolding
- [x] Repo skeleton + `.gitignore` (`data/`, `.env`, `__pycache__`)
- [x] `.env.example` (DB creds, DATA_DIR, model name) + `config.py` loader
- [x] `Makefile`/`justfile`: `up`, `up-gpu`, `down`, `scale`, `logs`, `psql`, `capture`
- [x] `docker-compose.yml` (from the provided file) wired to `db/ingest/process/dashboard` + gpu profile
- [x] `services/pipeline/Dockerfile` (one image, entrypoint via `python -m coach.<module>`)
- [x] `db/init/01_schema.sql` applied on first boot; `docker compose up db` healthy

### Phase B — Tier 0: capture + ingest
- [x] `schema.py` canonical dataclasses
- [x] `sources/base.py` interface + `sources/acc.py` ACC mapping (pyaccsharedmemory)
- [x] `capture/capture_agent.py`: poll shared memory, detect lap boundaries (spline wrap /
      completedLaps), buffer per lap, **atomic** write `raw/<session>/<lap>.parquet` + `.meta.json`
- [x] `run_capture.ps1` host launcher; verify a real lap produces a parquet
- [x] `ingest.py`: watch `raw/`, validate, create `sessions`/`laps` rows (status=pending),
      move file `raw/ → laps/`
- [ ] End-to-end smoke test: drive 1 lap → row appears with status=pending

### Phase C — Tier 1: delta / "finds gaps"
- [x] `align.py`: resample a lap onto a common spline/distance grid
- [x] `delta.py`: cumulative time-delta vs distance against PB; per-corner loss table
- [x] PB management in `pbs` (auto-update when a faster valid lap lands)
- [x] `process.py` worker: claim lap (SKIP LOCKED) → align+delta → write `findings/` + db
- [x] Dashboard page: delta trace + corner-loss ranking
- [x] Verify resumability: kill `process` mid-run, restart, work completes

### Phase D — Tier 2: input coaching (+ 1.5 setups I/O)
- [x] `inputs.py` detectors: trail-brake overlap, coasting gap, lockups/ABS, corner overspeed,
      steering reversals, throttle smoothness, short-shift — each emits a finding w/ severity + fix
- [x] Config-driven thresholds (per car/track), documented defaults
- [x] `setups.py`: parse + write ACC setup JSON; diff two setups
- [x] Dashboard: per-corner coaching notes
- [ ] Tune thresholds against ~20–40 real laps

### Phase E — Tier 2.5 + Tier 3: setup advisor + LLM coach
- [ ] `setups.py` rule advisor: symptom → setup-change direction (+ optional RAG over setup notes)
- [ ] `coach-llm` (Ollama) up under gpu profile; pull a 7–8B Q4 model; confirm it fits 8 GB
- [ ] `sessions.py`: store history, track PB progression, generate practice-drill plans
- [ ] `coach_service.py`: feed **computed** findings + history to the LLM (it narrates, never invents
      numbers); output coaching + next-session plan using the **Coach Nono** persona (female voice,
      direct + encouraging tone — defined in `config.py:coach_persona`)
- [ ] Dashboard "Coach" tab (chat + session plan); verify GPU can be brought up/down cleanly
- [ ] **Voice output (optional — Nono's real voice):** Yahia is building a Crew Chief voice pack
      from Nono's recordings. Reuse those WAV samples to synthesize coaching output in her actual
      voice via **XTTS v2** (Coqui) zero-shot cloning — provide a reference clip, it runs on the
      RTX 5060 but shares VRAM with the LLM, so bring coach-llm down first or quantise further.
      Alt: **RVC** (voice conversion) on top of any TTS output, lighter on VRAM.
      Config hook: `COACH_TTS=xtts` + `TTS_VOICE_REF=/data/reference/nono_voice.wav`.

### Phase F — Hardening
- [ ] Stale-lease reclaim on worker startup
- [ ] Retention job: `raw → archive` after `done`
- [ ] `README.md` quickstart; `PROGRESS.md` current
- [ ] Generalization pass on a 2nd car/track (no hard-coding leaked in)

---

## 8. Operating commands (put in Makefile)

```bash
# CPU pipeline (GPU stays free)
docker compose up -d
# add the coach (uses GPU)
docker compose --profile gpu up -d
# free the GPU again, keep CPU pipeline running
docker compose --profile gpu down
# burn through a backlog
docker compose up -d --scale process=4
# pause everything (resume later, no data loss)
docker compose stop      # ... docker compose start
# logs / db shell
docker compose logs -f process
docker compose exec db psql -U coach
# HOST: run capture while driving (PowerShell)
./capture/run_capture.ps1
```

---

## 9. Tier 4 & 5 — outline only (refine later)

**Tier 4 — trained models (optional, 8 GB is fine; data is the wall):**
- Personalized error classifier (label corner segments good/bad → small 1D-CNN/GRU or GBM).
- Corner/lap-time predictor to quantify "this mistake cost X".
- "Alien-self" model: distribution of your fastest-lap inputs per corner; flag live deviations.
- QLoRA fine-tune of the coach LLM (7B, 4-bit) for a sharper domain voice.
- *Infra:* add a `train` service (gpu profile) that checkpoints to `data/models/` so runs resume.

**Tier 5 — self-discovering agent (research-grade, months, may not pay off):**
- Imitation learning from fast laps, or RL agent driving ACC via vJoy input injection.
- Setup optimization via Bayesian optimization where each eval is a driven lap.
- *Hard blockers:* no gym/RL API, real-time-only, slow/manual resets, brittle input injection,
  anti-cheat gray area (offline/practice ONLY), ACC patches break the harness, expensive eval
  function may never converge past a good human setup.
- Keep as a separate ambition, not a milestone on the main line.

---

## 10. Definition of done (this roadmap)
A driving session → capture → ingest → process produces, per lap, a delta trace, a ranked
corner-loss table, and concrete input-coaching notes in the dashboard; the GPU coach (when brought
up) turns those plus your history into spoken-style coaching and a next-session plan; and the whole
stack survives stop/start/scale/reboot without manual repair.
