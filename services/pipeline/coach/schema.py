from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CanonicalSample:
    t: float            # seconds since session start
    lap_time: float     # current lap timer (s)
    spline: float       # normalized track position 0..1
    distance_m: float   # distance into lap (m)
    speed: float        # m/s
    throttle: float     # 0..1
    brake: float        # 0..1
    steer: float        # -1..1  (negative = left)
    gear: int
    rpm: float
    tyre_temp: tuple | None = None    # (FL, FR, RL, RR) °C
    tyre_press: tuple | None = None   # (FL, FR, RL, RR) kPa
    abs_active: bool | None = None
    tc_active: bool | None = None
    fuel: float | None = None         # litres remaining


@dataclass
class ExtendedSample(CanonicalSample):
    # Chassis dynamics
    g_lat: float | None = None           # p.g_force.x  ← sentinel: presence = extended parquet
    g_lon: float | None = None           # p.g_force.z
    g_vert: float | None = None          # p.g_force.y
    local_vel_x: float | None = None     # p.local_velocity.x  (lateral sideslip; z≈speed, cut)
    yaw_rate: float | None = None        # p.local_angular_vel.y
    pitch_rate: float | None = None      # p.local_angular_vel.x
    roll_rate: float | None = None       # p.local_angular_vel.z
    # Per-wheel grip (FL, FR, RL, RR 4-tuples)
    wheel_slip: tuple | None = None
    slip_ratio: tuple | None = None
    slip_angle: tuple | None = None
    wheel_angular_s: tuple | None = None
    suspension_travel: tuple | None = None
    # Brakes (4-tuples unless noted)
    brake_temp: tuple | None = None
    brake_pressure: tuple | None = None
    pad_life: tuple | None = None
    disc_life: tuple | None = None
    brake_bias: float | None = None
    front_brake_compound: int | None = None
    rear_brake_compound: int | None = None
    # Damage
    car_damage: tuple | None = None        # 5-element (FL, FR, RL, RR, center)
    suspension_damage: tuple | None = None
    # Drivetrain / engine
    clutch: float | None = None
    turbo_boost: float | None = None
    water_temp: float | None = None
    autoshifter_on: bool | None = None
    pit_limiter_on: bool | None = None
    is_ai_controlled: bool | None = None
    # Surface vibration (useful for kerb and tyre-slip detection)
    kerb_vibration: float | None = None
    slip_vibration: float | None = None
    # Environment (from Physics, updates per sample)
    air_temp: float | None = None
    road_temp: float | None = None
    # Graphics per-sample fields
    track_grip_status: int | None = None   # enum .value
    is_in_pit: bool | None = None
    is_in_pit_lane: bool | None = None
    current_sector_index: int | None = None
    flag: int | None = None                # enum .value
    position: int | None = None
    gap_ahead: float | None = None
    gap_behind: float | None = None
    delta_lap_time: int | None = None      # ms — ACC's own real-time delta
    penalty_time: float | None = None
    exhaust_temp: float | None = None
    used_fuel: float | None = None
    fuel_per_lap: float | None = None
    wind_speed: float | None = None
    wind_direction: float | None = None
    rain_10min: int | None = None          # g.rain_intensity_in_10min enum .value
    rain_30min: int | None = None          # g.rain_intensity_in_30min enum .value
    tc_level: int | None = None
    tc_cut_level: int | None = None
    abs_level: int | None = None
    engine_map: int | None = None
    driver_stint_time_left: int | None = None   # ms
    global_yellow_s1: bool | None = None
    global_yellow_s2: bool | None = None
    global_yellow_s3: bool | None = None


@dataclass
class SessionContext:
    game: str           # "acc"
    car: str
    track: str
    session_type: str   # "practice" | "qualifying" | "race" | ...
    conditions: dict    # {"rain_intensity": 0, ...}
    statics: dict = field(default_factory=dict)  # from StaticsMap: max_rpm, player_name, aids…
