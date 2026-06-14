from __future__ import annotations

import time

from coach.schema import CanonicalSample, SessionContext

_SESSION_TYPES: dict[int, str] = {
    0: "unknown",
    1: "practice",
    2: "qualifying",
    3: "superpole",
    4: "race",
    5: "hotlap",
    6: "hotstint",
    7: "superpole_session",
    8: "drift",
    9: "time_attack",
}
_ACC_LIVE = 2


class ACCSource:
    def __init__(self) -> None:
        self._asm = None
        self._t0: float | None = None  # monotonic clock at first LIVE sample

    # ------------------------------------------------------------------
    # TelemetrySource protocol
    # ------------------------------------------------------------------

    def open(self) -> None:
        from pyaccsharedmemory import accSharedMemory  # Windows-only; lazy import
        self._asm = accSharedMemory()

    def context(self) -> SessionContext:
        raw = self._asm.read_shared_memory()
        g, s = raw.Graphics, raw.Statics
        return SessionContext(
            game="acc",
            car=_text(s.carModel),
            track=_text(s.track),
            session_type=_SESSION_TYPES.get(int(g.session), "unknown"),
            conditions={"rain_intensity": int(getattr(g, "rainIntensity", 0))},
        )

    def read(self) -> CanonicalSample | None:
        raw = self._asm.read_shared_memory()
        if int(raw.Graphics.status) != _ACC_LIVE:
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
    # Callers that need completedLaps etc. use read_shared_memory() +
    # to_sample() themselves so they only pay for one shared-memory read.
    # ------------------------------------------------------------------

    def read_shared_memory(self):
        return self._asm.read_shared_memory()

    def to_sample(self, raw, t: float) -> CanonicalSample:
        g, p = raw.Graphics, raw.Physics
        return CanonicalSample(
            t=t,
            lap_time=_ms(getattr(g, "iCurrentTime", None) or getattr(g, "currentTime", 0)),
            spline=float(g.normalizedCarPosition),
            distance_m=float(getattr(g, "distanceTraveled", 0.0)),
            speed=float(p.speedKmh) / 3.6,
            throttle=float(p.gas),
            brake=float(p.brake),
            steer=float(p.steerAngle),
            gear=int(p.gear),
            rpm=float(p.rpms),
            tyre_temp=tuple(p.tyreCoreTemperature),
            tyre_press=tuple(p.wheelsPressure),
            abs_active=bool(getattr(p, "absInAction", 0) or float(getattr(p, "abs", 0)) > 0.01),
            tc_active=bool(getattr(p, "tcinAction", 0) or float(getattr(p, "tc", 0)) > 0.01),
            fuel=float(p.fuel),
        )


def _text(val) -> str:
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="ignore").rstrip("\x00").strip()
    return str(val).rstrip("\x00").strip()


def _ms(val) -> float:
    try:
        return int(val) / 1000.0
    except (TypeError, ValueError):
        return 0.0
