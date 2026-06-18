# Capture Agent v2 — Robustness Migration Plan

> Status: **PLANNING** — Phase 0 not yet started.
> Last updated: 2026-06-18

---

## Why this exists

The current `run_capture.ps1` is a manual launcher. ACC shared memory (`acs physics/graphics/static`) is a Win32 named-object — Docker containers cannot see it, so the capture agent must run on the Windows host. But "must run on Windows" ≠ "must be run manually". This plan upgrades the lifecycle, observability, and data-integrity of the capture without touching the shared-memory constraint.

**Capture is the most important part of the pipeline.** Data should be stockpiled in `data/raw`; the consumer side adapts.

---

## Session-change detection (already fixed, pre-migration)

Three signals, applied in priority order:

| # | Signal | Covers |
|---|---|---|
| 1 | `session_index` change | Most cases — increments on every new ACC session |
| 2 | `completed_lap` regression | Track switches where status stays live and session_index doesn't change |
| 3 | Statics debounce (150 samples = 3 s) | Practice-server switches with restored state — completed_lap doesn't reset, session_index same; statics eventually update |

All three are live in `capture_agent.py` as of 2026-06-18.

---

## Architecture — recommended approach per axis

### A. Automation — NSSM Windows Service

**Winner: NSSM** wrapping `capture/venv/Scripts/pythonw.exe capture_agent.py`, installed as a Windows Service with `Automatic (Delayed Start)` and crash-restart throttling.

- Agent already idles gracefully when ACC isn't running (existing "Waiting for ACC to go live" loop). No trigger needed — always-on is simpler.
- **Critical:** run the service as your interactive user account, **not** LocalSystem. ACC shared memory lives in the interactive user session; session-0 services can't see it. This is the #1 failure mode to validate.
- Use `pythonw.exe` (no console window). NSSM captures stdout/stderr to a file, but once logging goes to files (Phase 1), that's just a backup.

Alternatives rejected:
- **Task Scheduler on-logon** — weak crash-recovery, no clean restart policy.
- **pywin32 service** — requires service-control-handler threading wired into the Python code; fragile ("Error 1053").
- **WinSW** — equivalent capability but requires a per-service XML + .NET exe copy; NSSM is a single binary with a scripted CLI.

### B. Error communication — BurntToast + webhook backstop

**Primary: BurntToast** PowerShell module, invoked via subprocess from a `notify.py` Notifier class. Works in ACC borderless-fullscreen (the default). Per-event cooldown prevents spam.

**Backstop: Discord/Telegram webhook** (`requests.post` to `CAPTURE_ALERT_WEBHOOK` env var). Phone buzzes even mid-corner. This is the safety net for exclusive fullscreen where toasts queue silently.

Alert events:

| Event | Channel |
|---|---|
| Lap write failure | toast + webhook (data loss) |
| Disk space below threshold | toast + webhook |
| Lost ACC connection mid-session | toast |
| Orphaned `.tmp` found on startup | toast |
| Lap discarded (< MIN_SAMPLES) | toast (low priority) |
| Capture crash / NSSM restart | toast + webhook |

BurntToast must be installed for the service account's PowerShell scope: `Install-Module BurntToast -Scope AllUsers`.

### C. Permanent logs — `RotatingFileHandler` + JSON lines, disk-space-aware

Log to `data/logs/capture/capture.log`, JSON lines format. Lives under the bind-mounted `./data` — Docker services read it without a new mount. A separate **lap audit log** (`capture-audit.log`) records one line per lap written (session, lap index, sample count, checksum, lap time).

Replace the `logging.basicConfig(...)` in `capture_agent.py` with a `setup_logging()` from `logging_setup.py`. Keep the console handler for interactive runs.

**Size-based rotation, not daily rotation — and why:**

`TimedRotatingFileHandler` (daily) is tempting for the "one file per day" mental model but creates unpredictable file sizes: an intense practice day could write 50 MB before midnight while a light day writes 1 KB. That makes disk use impossible to bound. Use `RotatingFileHandler` instead:

```python
RotatingFileHandler("capture.log", maxBytes=5_000_000, backupCount=10)
```

This hard-caps the log directory at ~50 MB total. When a file rolls, a custom `rotator` hook gzip-compresses it and names it with a timestamp so forensics are still easy:

```
capture.log                       ← current (up to 5 MB, plain text)
capture.20260618_150000.log.gz    ← rolled, gzipped (~0.5 MB)
capture.20260617_091234.log.gz
...  (up to 10 rolled files)
```

With gzip, the effective on-disk cap is ~5–6 MB total. `backupCount=10` means at least 10 sessions of history even if each session fills a file.

**Disk-space-mindful logging policy — critical:**

- Log only **events**: session start/stop, lap written, lap discarded, errors, disk alerts, session-change signals. **Never log per-sample** (that would generate gigabytes at 50 Hz).
- **Sweep log is separate** (see F) and tiny — one JSON line per sweep run regardless of laps processed.
- **Include `data/logs/` size in the disk pre-check** (section D item 4) — if logs are somehow growing unexpectedly, alert on it.
- Avoid duplicate log destinations: once file logging is wired, suppress the console handler when running as a service (env flag `CAPTURE_SERVICE=1` or detect `pythonw.exe` via `sys.stdout.name`).
- The audit log uses the same rotation policy independently: `maxBytes=1_000_000`, `backupCount=10` ≈ 1 MB on disk after gzip.

### D. Data-integrity hardening

In order of value:

1. **Per-lap SHA-256 checksum** added to `meta.json` (`parquet_sha256`, `parquet_bytes`). Additive field — ingest ignores it until verification is added. Detects bit-rot / partial flush on the bind mount.
2. **Write-ahead session manifest** (`data/raw/<session_id>/_manifest.jsonl`) — append one line before `_write_lap` returns. Source of truth for "what should exist."
3. **Startup recovery scan** (`recovery.py`): delete stale `*.tmp` files, verify each manifest entry, re-emit `meta.json` for any lap whose parquet exists but meta is missing (process killed between the two writes — this **recovers** the lap into ingest). Toast a summary if anything was repaired.
4. **Disk-space pre-check** before each write — two thresholds:
   - **Alert threshold** (`CAPTURE_MIN_FREE_GB`, default 5 GB): toast + webhook, write a `data/logs/capture/TRIGGER_INGEST` sentinel so the sweeper wakes up immediately (see F). Still attempt the write.
   - **Pause threshold** (`CAPTURE_PAUSE_FREE_GB`, default 2 GB): finish the current lap write, then block before accumulating the next lap. Poll disk space every 10 s, waiting up to `CAPTURE_PAUSE_TIMEOUT_S` (default 300 s = 5 min) for the sweeper to free space. If timeout expires, log + alert + resume anyway (recording to disk risks loss, but stopping capture is worse).
5. **Wrap `_write_lap` in try/except** — on failure, dump `lap_buf` to `lap_NNN.failed.json` so samples survive even a corrupted write. Log + alert. Never silently swallow.

Current atomic write ordering (parquet first, meta last, `os.replace`) is correct and must be preserved. Ingest's `meta.json` trigger depends on it.

### E. Health channel — `status.json` heartbeat

Write `data/logs/capture/status.json` atomically every ~2 s:

```json
{
  "pid": 1234,
  "started_at": "2026-06-18T14:51:44",
  "last_heartbeat": "2026-06-18T15:05:22",
  "state": "idle|live|lost|error|paused_disk",
  "session_id": "...",
  "current_lap": 4,
  "laps_written_session": 3,
  "laps_written_total": 47,
  "free_disk_gb": 14.2,
  "unprocessed_raw_mb": 312.4,
  "unprocessed_lap_count": 18,
  "last_error": null,
  "last_lap_written_at": "2026-06-18T15:04:10",
  "sweep_trigger_active": false
}
```

`state: "paused_disk"` is set when capture is blocking on disk-space recovery. `unprocessed_raw_mb` and `unprocessed_lap_count` let the sweeper (section F) and dashboard see how much backlog exists without scanning the filesystem. The sweeper reads `sweep_trigger_active` to know when to run immediately.

Under the bind mount → Docker services read it free. A **watchdog** checks `last_heartbeat` staleness (catches hangs) and can `Restart-Service` + alert.

Optional `pystray` tray icon reads `status.json` and colors green/amber/red. Pure viewer.

### F. Periodic Ingest Sweeper

A Docker service (`ingest-sweep`) that proactively drains `data/raw/` on a schedule, deletes processed files, and acts as the disk-space safety valve for the capture agent.

**Why a separate service and not the existing `ingest`:**
The existing ingest service is event-driven (triggered by new `meta.json` files, polls every 2 s). The sweeper is schedule-driven and cleanup-responsible. Keeping them separate means each has a single clear job, and the sweeper can be restarted/tuned independently without touching the hot ingest path.

**Adaptive interval:**

The sweeper wakes up on a loop and checks how much unprocessed data exists (scan `data/raw/` for `*.meta.json` files not yet in the DB, or read `unprocessed_raw_mb` from `status.json`):

| Condition | Sleep interval |
|---|---|
| `sweep_trigger_active` in `status.json` = true | Run immediately, do not sleep |
| Unprocessed > 500 MB | 10 minutes |
| Unprocessed > 100 MB | 20 minutes |
| Unprocessed ≤ 100 MB or nothing new | 30 minutes |
| Capture state = `live` (ACC active, mid-session) | Defer 5 minutes, re-check — avoid disk I/O competition during driving |

"Nothing new" = no unprocessed meta files found → log one line (`sweep: nothing to process, sleeping 30 m`) and sleep. No other output.

**What actually accumulates on disk (important distinction):**

The existing `ingest` service already moves raw parquets from `data/raw/` → `data/laps/` and deletes `meta.json` within ~2 s of a lap landing. So `data/raw/` self-clears under normal conditions. The real long-term disk consumers are:
- `data/laps/` — permanent parquet store, never pruned
- `data/coords/` — XYZ traces, never pruned
- `data/findings/` — analysis artefacts, never pruned

The sweeper has two distinct jobs:
1. **Catch-up ingest** — for laps that landed in `data/raw/` while `ingest` was down (e.g., Docker not running). Drains the backlog.
2. **Long-term disk management** — periodically prune `data/laps/`, `data/coords/`, and `data/findings/` for sessions beyond a configurable retention window (`SWEEP_RETAIN_SESSIONS`, default keep last N sessions or last X days). Policy: mark old sessions as archived in DB before deleting parquets, so DB metadata survives even if the raw data is pruned.

**Per-run behavior:**

1. Check `capture/status.json` — if state is `live`, defer (back off 5 min, re-check). If `sweep_trigger_active` is true, override: run regardless.
2. **Catch-up pass**: find any `*.meta.json` still in `data/raw/` not in the DB. For each: verify parquet + checksum, ingest via shared code path, then let the normal ingest cleanup (`os.replace` + move) handle it. Alert on checksum mismatch; skip corrupt data.
3. **Disk-management pass** (skip if `sweep_trigger_active` — urgent mode only cares about raw/ catch-up): scan `data/laps/` for sessions outside retention window. Mark archived in DB, delete parquets + coords + findings. Log MB freed.
4. Write `data/logs/sweep/sweep.log` — one JSON-line per run: `{ts, laps_ingested, sessions_archived, mb_freed, duration_s, triggered_by: "schedule|disk_alert"}`.
5. Write `data/logs/capture/TRIGGER_INGEST_DONE` sentinel to unblock capture if it's paused.

**Disk-space failsafe handshake:**

```
Capture                        Sweeper
  |                              |
  |-- low disk detected          |
  |-- write TRIGGER_INGEST ----→ |
  |-- state = paused_disk        | ← detects trigger on next wakeup
  |                              |-- runs immediately
  |                              |-- deletes processed raws
  |                              |-- writes TRIGGER_INGEST_DONE
  |← polls disk space / done    |
  |-- state = live, continue     |
```

Capture checks for `TRIGGER_INGEST_DONE` every 10 s (up to `CAPTURE_PAUSE_TIMEOUT_S`). Both sentinel files live in `data/logs/capture/` under the bind mount — no new IPC channel needed.

**Logging (disk-space-mindful):**
- `data/logs/sweep/sweep.log`: one JSON line per sweep run, rotated at 1 MB / 10 files (~10 MB total). Tiny — even 48 runs/day × 1 line × ~200 bytes = ~3.5 MB/year before rotation.
- No per-lap logging to the sweep log — only aggregate: `laps_processed=N, mb_freed=X`.
- Errors (checksum mismatch, ingest failure) go to the existing `data/logs/ingest/` log (shared logger).

**Files:**
- `services/ingest-sweep/sweep_service.py` — the main loop
- `services/ingest-sweep/Dockerfile` — thin Python image, shares the pipeline venv image
- `docker-compose.yml` — add `ingest-sweep` service, bind-mount `./data:/data`

---

## Migration phases

### Phase 0 — Refactor in place + dashboard v1 removal

**Dashboard v1 removal (do first — zero risk):**
- Delete `services/pipeline/coach/dashboard.py`
- Remove the `dashboard` service from `docker-compose.yml`
- Remove all `dashboard` (non-v2) references from the README, quick-start, and migration commands
- `dashboard-v2` is the only dashboard going forward
- Commit separately before touching capture code

**Capture refactor (no behavior change):**
Split `capture_agent.py` into modules:
- `notify.py` — Notifier class (BurntToast + webhook, per-event cooldown)
- `logging_setup.py` — `setup_logging()` (rotating JSON file handler + audit log)
- `health.py` — `status.json` heartbeat writer + single-instance lockfile
- `recovery.py` — startup scan (`.tmp` cleanup, manifest verification, orphan recovery)

`capture_agent.py` imports and calls these. Behavior identical. Verify a normal manual run still produces the same `data/raw` output. **Commit.**

### Phase 1 — Wire hardening passively
- Logging → `data/logs/capture/capture.log` (size+time rotation, gzip on rollover)
- `status.json` heartbeat (includes `unprocessed_raw_mb`, `sweep_trigger_active`)
- Checksums in meta
- Recovery scan on startup
- Disk pre-check with two thresholds (alert at 5 GB, pause at 2 GB) + sentinel write
- `_write_lap` try/except with `.failed.json` dump
- Alerts for each failure event

Still launched manually via `run_capture.ps1`. Drive a session; confirm logs/status/toasts work and ingest still consumes unchanged. New meta fields are additive — ingest ignores extras. **No ingest change required.** Commit.

### Phase 1.5 — Ingest sweeper (can overlap with Phase 2)
- Build `services/ingest-sweep/sweep_service.py` and `Dockerfile`
- Add `ingest-sweep` to `docker-compose.yml`
- Test: run a session, stop Docker ingest, let raw accumulate, start sweeper → confirm it drains and deletes correctly
- Test disk-alert handshake: reduce `CAPTURE_MIN_FREE_GB` temporarily to trigger the sentinel flow
- Confirm `data/logs/sweep/sweep.log` stays small
- Commit.

### Phase 2 — Install service alongside
Write `install_service.ps1`:
```powershell
nssm install CoachNono-Capture "...\capture\venv\Scripts\pythonw.exe"
nssm set CoachNono-Capture AppParameters "...\capture\capture_agent.py"
nssm set CoachNono-Capture AppDirectory "...\Coach-Nono"
nssm set CoachNono-Capture ObjectName ".\<user>" "<password>"
nssm set CoachNono-Capture Start SERVICE_DEMAND_START  # manual until validated
nssm set CoachNono-Capture AppThrottle 10000
nssm set CoachNono-Capture AppExit Default Restart
nssm set CoachNono-Capture AppStdout "...\data\logs\capture\service-stdout.log"
nssm set CoachNono-Capture AppStderr "...\data\logs\capture\service-stderr.log"
```

Test start it without ACC (should idle). Then with ACC (should capture). Enforce single-instance lockfile so manual + service can't double-run (they'd fight over logs and status). Commit.

### Phase 3 — Cut over
Set service to `Automatic (Delayed Start)`. Stop using `run_capture.ps1` for driving. Keep it as "interactive/debug" launcher with a guard that exits if the lockfile is held. Commit.

### Phase 4 — Watchdog + optional tray
- Watchdog: scheduled task or second tiny NSSM service, reads `status.json`, alerts + restarts if `last_heartbeat` stale > N seconds.
- Tray: `pystray` app, pure viewer. Optional.

---

## Files to create / modify

**Capture agent (Windows host):**

| File | Action |
|---|---|
| `capture/capture_agent.py` | Modify — wire modules, wrap `_write_lap`, disk-pause logic, sentinel write |
| `capture/notify.py` | New — BurntToast subprocess + webhook Notifier |
| `capture/logging_setup.py` | New — `setup_logging()`, JSON formatter, size+time rotation, gzip hook |
| `capture/health.py` | New — `status.json` heartbeat + lockfile |
| `capture/recovery.py` | New — startup scan: `.tmp` cleanup, manifest verify, orphan re-emit |
| `capture/install_service.ps1` | New — NSSM install/configure script |
| `capture/watchdog.py` | New — heartbeat staleness checker (Phase 4) |
| `capture/requirements.txt` | Modify — add `requests`; `pystray`+`Pillow` optional |

**Ingest sweeper (Docker):**

| File | Action |
|---|---|
| `services/ingest-sweep/sweep_service.py` | New — adaptive sweep loop, catch-up ingest, disk-management, sentinel handshake |
| `services/ingest-sweep/Dockerfile` | New — thin Python image |
| `docker-compose.yml` | Modify — add `ingest-sweep` service, remove `dashboard` (v1) service |
| `data/logs/sweep/` | New log directory (auto-created by sweeper) |

**Delete:**
- `services/pipeline/coach/dashboard.py` — v1 dashboard, superseded by `dashboard_v2.py`

Consumer side (optional / no urgency):
- `services/pipeline/coach/ingest.py` — optionally verify `parquet_sha256`; surface capture health.
- `services/pipeline/coach/dashboard_v2.py` — add "Capture health" badge reading `status.json`.

---

## Win32 / Windows 11 gotchas

- **Service account session:** shared memory is in the interactive user session — run as your user, not LocalSystem. Validate this first before declaring Phase 2 done.
- **Store Python stub:** venv must use a real interpreter. `install_service.ps1` must point at the venv's `pythonw.exe` explicitly.
- **`pythonw.exe` vs `python.exe`:** use `pythonw` for the service (no console). Since it discards stdout/stderr, logging MUST go to files — don't rely on NSSM's stdout capture as the only log.
- **Toasts under exclusive fullscreen** are suppressed → hence the webhook backstop.
- **`os.replace` atomicity** holds only within one volume. `data/` is local — don't write `.tmp` to `%TEMP%` and replace across drives.
- **`TimedRotatingFileHandler` + two processes** corrupt the log. The lockfile must prevent this before Phase 2.
- **BurntToast scope:** install with `-Scope AllUsers` so the service account's PowerShell can use it.
