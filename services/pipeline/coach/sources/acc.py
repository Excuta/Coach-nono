from __future__ import annotations

import dataclasses
import time

from coach.schema import CanonicalSample, ExtendedSample, SessionContext

_SESSION_TYPES: dict[int, str] = {
    -1: "unknown",
    0: "practice",
    1: "qualifying",
    2: "race",
    3: "hotlap",
    4: "time_attack",
    5: "drift",
    6: "drag",
    7: "hotstint",
    8: "superpole",
}
_ACC_LIVE = 2


class ACCSource:
    def __init__(self) -> None:
        self._asm = None
        self._t0: float | None = None

    # ------------------------------------------------------------------
    # TelemetrySource protocol
    # ------------------------------------------------------------------

    def open(self) -> None:
        from pyaccsharedmemory import accSharedMemory  # Windows-only; lazy import
        self._asm = accSharedMemory()

    def context(self, raw=None) -> SessionContext:
        if raw is None:
            raw = self._asm.read_shared_memory()
        g, s = raw.Graphics, raw.Static
        statics = {
            "max_rpm": int(s.max_rpm),
            "max_fuel": float(s.max_fuel),
            "sector_count": int(s.sector_count),
            "player_name": " ".join(
                x.rstrip("\x00") for x in [s.player_name, s.player_surname, s.player_nick]
                if x.rstrip("\x00")
            ),
            "is_online": bool(s.is_online),
            "penalty_enabled": bool(s.penalty_enabled),
            "aid_fuel_rate": float(s.aid_fuel_rate),
            "aid_tyre_rate": float(s.aid_tyre_rate),
            "aid_mechanical_damage": float(s.aid_mechanical_damage),
            "aid_stability": float(s.aid_stability),
            "aid_auto_clutch": bool(s.aid_auto_clutch),
        }
        return SessionContext(
            game="acc",
            car=s.car_model.rstrip("\x00").strip(),
            track=s.track.rstrip("\x00").strip(),
            session_type=_SESSION_TYPES.get(g.session_type.value, "unknown"),
            conditions={"rain_intensity": g.rain_intensity.value},
            statics=statics,
        )

    def read(self) -> CanonicalSample | None:
        raw = self._asm.read_shared_memory()
        if raw is None or raw.Graphics.status.value != _ACC_LIVE:
            self._t0 = None
            return None
        if self._t0 is None:
            self._t0 = time.monotonic()
        return self.to_sample(raw, time.monotonic() - self._t0)

    def close(self) -> None:
        if self._asm is not None:
            try:
                self._asm.close()
            except Exception:
                pass
            self._asm = None

    # ------------------------------------------------------------------
    # Capture-agent helpers
    # ------------------------------------------------------------------

    def read_shared_memory(self):
        return self._asm.read_shared_memory()

    def to_sample(self, raw, t: float) -> ExtendedSample:
        g, p = raw.Graphics, raw.Physics
        return ExtendedSample(
            # --- CanonicalSample fields ---
            t=t,
            lap_time=g.current_time / 1000.0,
            spline=float(g.normalized_car_position),
            distance_m=float(g.distance_traveled),
            speed=float(p.speed_kmh) / 3.6,
            throttle=float(p.gas),
            brake=float(p.brake),
            steer=float(p.steer_angle),
            gear=int(p.gear),
            rpm=float(p.rpm),
            tyre_temp=_wheels_tuple(p.tyre_core_temp),
            tyre_press=_wheels_tuple(p.wheel_pressure),
            abs_active=float(p.abs) > 0.01,
            tc_active=float(p.tc) > 0.01,
            fuel=float(p.fuel),
            # --- Chassis dynamics ---
            g_lat=float(p.g_force.x),
            g_lon=float(p.g_force.z),
            g_vert=float(p.g_force.y),
            local_vel_x=float(p.local_velocity.x),
            yaw_rate=float(p.local_angular_vel.y),
            pitch_rate=float(p.local_angular_vel.x),
            roll_rate=float(p.local_angular_vel.z),
            # --- Per-wheel grip ---
            wheel_slip=_wheels_tuple(p.wheel_slip),
            slip_ratio=_wheels_tuple(p.slip_ratio),
            slip_angle=_wheels_tuple(p.slip_angle),
            wheel_angular_s=_wheels_tuple(p.wheel_angular_s),
            suspension_travel=_wheels_tuple(p.suspension_travel),
            # --- Brakes ---
            brake_temp=_wheels_tuple(p.brake_temp),
            brake_pressure=_wheels_tuple(p.brake_pressure),
            pad_life=_wheels_tuple(p.pad_life),
            disc_life=_wheels_tuple(p.disc_life),
            brake_bias=float(p.brake_bias),
            front_brake_compound=int(p.front_brake_compound),
            rear_brake_compound=int(p.rear_brake_compound),
            # --- Damage ---
            car_damage=tuple(float(x) for x in dataclasses.astuple(p.car_damage)),
            suspension_damage=_wheels_tuple(p.suspension_damage),
            # --- Drivetrain / engine ---
            clutch=float(p.clutch),
            turbo_boost=float(p.turbo_boost),
            water_temp=float(p.water_temp),
            autoshifter_on=bool(p.autoshifter_on),
            pit_limiter_on=bool(p.pit_limiter_on),
            is_ai_controlled=bool(p.is_ai_controlled),
            # --- Vibration ---
            kerb_vibration=float(p.kerb_vibration),
            slip_vibration=float(p.slip_vibration),
            # --- Environment ---
            air_temp=float(p.air_temp),
            road_temp=float(p.road_temp),
            # --- Graphics per-sample ---
            track_grip_status=_enum_int(g.track_grip_status),
            is_in_pit=bool(g.is_in_pit),
            is_in_pit_lane=bool(g.is_in_pit_lane),
            current_sector_index=int(g.current_sector_index),
            flag=_enum_int(g.flag),
            position=int(g.position),
            gap_ahead=float(g.gap_ahead),
            gap_behind=float(g.gap_behind),
            delta_lap_time=int(g.delta_lap_time),
            penalty_time=float(g.penalty_time),
            exhaust_temp=float(g.exhaust_temp),
            used_fuel=float(g.used_fuel),
            fuel_per_lap=float(g.fuel_per_lap),
            wind_speed=float(g.wind_speed),
            wind_direction=float(g.wind_direction),
            rain_10min=_enum_int(g.rain_intensity_in_10min),
            rain_30min=_enum_int(g.rain_intensity_in_30min),
            tc_level=int(g.tc_level),
            tc_cut_level=int(g.tc_cut_level),
            abs_level=int(g.abs_level),
            engine_map=int(g.engine_map),
            driver_stint_time_left=int(g.driver_stint_time_left),
            global_yellow_s1=bool(g.global_yellow_s1),
            global_yellow_s2=bool(g.global_yellow_s2),
            global_yellow_s3=bool(g.global_yellow_s3),
        )

    def coords_row(self, raw, t: float) -> dict:
        g = raw.Graphics
        slot = next((i for i, cid in enumerate(g.car_id) if cid == g.player_car_id), 0)
        c = g.car_coordinates[slot]
        return {"t": t, "x": float(c.x), "y": float(c.y), "z": float(c.z)}


def _wheels_tuple(w) -> tuple:
    return (w.front_left, w.front_right, w.rear_left, w.rear_right)


def _enum_int(v) -> int:
    return int(getattr(v, "value", v))
