-- Coach Nono schema — runs automatically on first db container boot.
-- Safe to re-run: all objects use IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS sessions (
    id           BIGSERIAL PRIMARY KEY,
    capture_id   TEXT UNIQUE,            -- string key from capture agent (YYYYMMDD_HHMMSS_car_track)
    game         TEXT NOT NULL,
    car          TEXT NOT NULL,
    track        TEXT NOT NULL,
    session_type TEXT NOT NULL,          -- practice | quali | race
    conditions   JSONB,                  -- temps, compound, etc.
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS laps (
    id          TEXT PRIMARY KEY,        -- SHA-256 hash of (session_id, lap_index, content)
    session_id  BIGINT REFERENCES sessions(id),
    lap_index   INT  NOT NULL,
    lap_time    DOUBLE PRECISION,        -- seconds; NULL until known
    valid       BOOLEAN,
    raw_path    TEXT,                    -- relative to DATA_DIR
    lap_path    TEXT,                    -- relative to DATA_DIR, set after ingest moves file
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | processing | done | failed
    claimed_at  TIMESTAMPTZ,
    attempts    INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS laps_status_idx ON laps (status);
CREATE INDEX IF NOT EXISTS laps_session_idx ON laps (session_id);

CREATE TABLE IF NOT EXISTS findings (
    id           BIGSERIAL PRIMARY KEY,
    lap_id       TEXT REFERENCES laps(id),
    corner       INT,                   -- corner number within the lap; NULL = lap-wide
    kind         TEXT NOT NULL,         -- e.g. trail_brake | coasting | lockup | overspeed
    severity     REAL,                  -- 0..1
    time_loss_s  REAL,                  -- estimated seconds lost
    detail       JSONB,                 -- free-form extra context
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS findings_lap_idx ON findings (lap_id);

-- Personal-best reference per game/car/track combination
CREATE TABLE IF NOT EXISTS pbs (
    game      TEXT NOT NULL,
    car       TEXT NOT NULL,
    track     TEXT NOT NULL,
    lap_id    TEXT REFERENCES laps(id),
    lap_time  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (game, car, track)
);

-- ---------------------------------------------------------------------------
-- Job-claim query (reference — executed by process workers at runtime):
--
-- UPDATE laps
--    SET status = 'processing',
--        claimed_at = now(),
--        attempts = attempts + 1
--  WHERE id = (
--      SELECT id FROM laps
--       WHERE status = 'pending'
--       ORDER BY created_at
--       FOR UPDATE SKIP LOCKED
--       LIMIT 1
--  )
-- RETURNING *;
-- ---------------------------------------------------------------------------
