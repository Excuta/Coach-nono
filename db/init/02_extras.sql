-- Idempotent migration: extended telemetry storage.
-- Safe to run repeatedly (all IF NOT EXISTS / COALESCE-upsert patterns).
-- On a fresh DB volume this runs automatically after 01_schema.sql.
-- On an existing volume apply once:
--   docker compose exec -T db psql -U coach -d coach < db/init/02_extras.sql

-- -------------------------------------------------------------------------
-- Session statics (nullable; populated from StaticsMap on first ACC session)
-- -------------------------------------------------------------------------
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS max_rpm      INT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS max_fuel     DOUBLE PRECISION;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS sector_count INT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS player_name  TEXT;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_online    BOOLEAN;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS statics      JSONB;

-- -------------------------------------------------------------------------
-- Per-lap aggregate statistics of extended telemetry fields.
-- One row per lap; created by ingest when the parquet contains 'g_lat'.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS extras (
    lap_id              TEXT PRIMARY KEY REFERENCES laps(id) ON DELETE CASCADE,
    -- G-force peaks (absolute max over lap)
    g_lat_max           REAL,
    g_lon_max           REAL,
    g_lon_min           REAL,
    g_vert_max          REAL,
    -- Body dynamics peaks
    yaw_rate_abs_max    REAL,
    local_vel_x_abs_max REAL,
    -- Per-wheel grip peaks (absolute max across all four wheels)
    slip_angle_abs_max  REAL,
    slip_ratio_abs_max  REAL,
    wheel_slip_max      REAL,
    -- Suspension (min = most compressed travel)
    susp_travel_min     REAL,
    -- Brakes
    brake_temp_max      REAL,
    brake_press_max     REAL,
    brake_bias_mean     REAL,
    -- Engine / drivetrain
    water_temp_mean     REAL,
    water_temp_max      REAL,
    turbo_boost_max     REAL,
    exhaust_temp_max    REAL,
    -- Environment
    air_temp_mean       REAL,
    road_temp_mean      REAL,
    -- Wear (min across wheels = most worn)
    pad_life_min        REAL,
    disc_life_min       REAL,
    -- Damage totals (sum across zones at end of lap)
    car_damage_total    REAL,
    susp_damage_total   REAL,
    -- Fuel
    fuel_used_lap       REAL,
    -- Surface vibration
    kerb_vibration_max  REAL,
    slip_vibration_max  REAL,
    -- Aid / track state (snapshot at lap end)
    tc_level            INT,
    abs_level           INT,
    engine_map          INT,
    track_grip_status   INT,
    -- Race / flag state over the lap
    flag_seen           INT,
    pit_sample_count    INT,
    -- ACC's own delta at lap end (ms, cross-reference for our computed delta)
    delta_lap_time_end  INT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS extras_lap_idx ON extras(lap_id);

-- -------------------------------------------------------------------------
-- Optional world-coordinate traces.
-- The XYZ parquet lives in data/coords/<session_id>/<lap>.parquet;
-- this table stores the path so it can be located without filesystem scans.
-- Enabled by CAPTURE_COORDS=true in the capture agent environment.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS coordinates (
    id           BIGSERIAL PRIMARY KEY,
    lap_id       TEXT NOT NULL REFERENCES laps(id) ON DELETE CASCADE,
    coords_path  TEXT NOT NULL,
    sample_count INT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (lap_id)
);

CREATE INDEX IF NOT EXISTS coordinates_lap_idx ON coordinates(lap_id);
