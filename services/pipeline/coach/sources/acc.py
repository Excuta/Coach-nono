from __future__ import annotations

import time

from coach.schema import CanonicalSample, SessionContext

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
        return SessionContext(
            game="acc",
            car=s.car_model.rstrip("\x00").strip(),
            track=s.track.rstrip("\x00").strip(),
            session_type=_SESSION_TYPES.get(g.session_type.value, "unknown"),
            conditions={"rain_intensity": g.rain_intensity.value},
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
    # Capture-agent helper: convert one raw read to a CanonicalSample.
    # ------------------------------------------------------------------

    def read_shared_memory(self):
        return self._asm.read_shared_memory()

    def to_sample(self, raw, t: float) -> CanonicalSample:
        g, p = raw.Graphics, raw.Physics
        return CanonicalSample(
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
        )


def _wheels_tuple(w) -> tuple:
    return (w.front_left, w.front_right, w.rear_left, w.rear_right)
