from __future__ import annotations

from dataclasses import dataclass


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
class SessionContext:
    game: str           # "acc"
    car: str
    track: str
    session_type: str   # "practice" | "qualifying" | "race" | ...
    conditions: dict    # {"rain_intensity": 0, ...}
