-- Phase E1: corner geometry, per-lap stats, and running baselines.
-- Safe to apply to an existing DB (CREATE TABLE IF NOT EXISTS, no ALTER on existing tables).

CREATE TABLE IF NOT EXISTS corners (
  id SERIAL PRIMARY KEY,
  game TEXT NOT NULL,
  track TEXT NOT NULL,
  car TEXT,                    -- NULL = track-wide (geometry is car-agnostic)
  corner_index INT NOT NULL,   -- 1-based (T1, T2, …)
  name TEXT,
  spline_start REAL NOT NULL,
  spline_apex  REAL NOT NULL,
  spline_end   REAL NOT NULL,
  UNIQUE (game, track, corner_index)
);

CREATE TABLE IF NOT EXISTS corner_stats (
  id BIGSERIAL PRIMARY KEY,
  lap_id TEXT REFERENCES laps(id) ON DELETE CASCADE,
  corner_id INT REFERENCES corners(id) ON DELETE CASCADE,
  game_version_major TEXT,     -- e.g. "1.10"; baselines never mix across major versions
  entry_speed_kph REAL,
  apex_speed_kph  REAL,
  exit_speed_kph  REAL,
  brake_point     REAL,        -- spline position of first brake input
  throttle_point  REAL,        -- spline position of first throttle after brake release
  coast_duration  INT,         -- grid samples where both pedals < 6%
  trail_brake_overlap INT,     -- grid samples where both pedals > 5%
  max_lat_g       REAL,
  min_slip_ratio  REAL,        -- most negative (locked) wheel
  steer_reversals INT,
  UNIQUE (lap_id, corner_id)
);

CREATE INDEX IF NOT EXISTS corner_stats_corner_idx ON corner_stats (corner_id);

CREATE TABLE IF NOT EXISTS corner_baselines (
  id BIGSERIAL PRIMARY KEY,
  game TEXT NOT NULL,
  game_version_major TEXT NOT NULL,
  track TEXT NOT NULL,
  car TEXT NOT NULL,
  corner_id INT REFERENCES corners(id) ON DELETE CASCADE,
  metric TEXT NOT NULL,
  p10 REAL, p25 REAL, p50 REAL, p75 REAL, p90 REAL,
  stddev REAL,
  sample_count INT NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (game, game_version_major, track, car, corner_id, metric)
);
