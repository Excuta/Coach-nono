-- Per-lap per-wheel tyre + brake aggregates.
-- Populated by the process worker from the aligned parquet.
-- Safe to apply while services are running (CREATE TABLE IF NOT EXISTS only).

CREATE TABLE IF NOT EXISTS tyre_laps (
    lap_id TEXT PRIMARY KEY REFERENCES laps(id) ON DELETE CASCADE,

    -- Mean tyre pressure over lap (bar) — FL FR RL RR
    press_avg_fl REAL, press_avg_fr REAL,
    press_avg_rl REAL, press_avg_rr REAL,

    -- Mean tyre core temperature over lap (°C)
    temp_avg_fl REAL, temp_avg_fr REAL,
    temp_avg_rl REAL, temp_avg_rr REAL,

    -- Peak tyre temperature reached during lap (°C)
    temp_max_fl REAL, temp_max_fr REAL,
    temp_max_rl REAL, temp_max_rr REAL,

    -- Pad life at end of lap (0–1, 1 = new)
    pad_life_fl REAL, pad_life_fr REAL,
    pad_life_rl REAL, pad_life_rr REAL,

    -- Disc life at end of lap (0–1, 1 = new)
    disc_life_fl REAL, disc_life_fr REAL,
    disc_life_rl REAL, disc_life_rr REAL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
