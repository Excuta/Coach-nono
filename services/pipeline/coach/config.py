"""
Central config loader. All values come from environment variables (set via .env
or docker-compose environment blocks). Import `cfg` wherever settings are needed.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # no-op inside containers; useful for local dev runs


class _Config:
    # Database
    database_url: str = os.environ["DATABASE_URL"]

    # File lake root (bind-mounted ./data -> /data inside containers)
    data_dir: Path = Path(os.getenv("DATA_DIR", "/data"))

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def laps_dir(self) -> Path:
        return self.data_dir / "laps"

    @property
    def findings_dir(self) -> Path:
        return self.data_dir / "findings"

    @property
    def reference_dir(self) -> Path:
        return self.data_dir / "reference"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @property
    def thresholds_config(self) -> Path:
        return self.data_dir / "config" / "thresholds.json"

    @property
    def coords_dir(self) -> Path:
        return self.data_dir / "coords"

    # Coach persona
    coach_name: str = os.getenv("COACH_NAME", "Coach Nono")
    # System prompt preamble injected into every LLM call. Override via env to experiment.
    coach_persona: str = os.getenv(
        "COACH_PERSONA",
        "You are Coach Nono, an expert sim-racing coach with a direct, encouraging female voice. "
        "You only ever reference numbers and findings computed by the pipeline — never invent data. "
        "Keep coaching notes concise and actionable.",
    )

    # Game version used to scope baselines — baselines from different major versions are never mixed.
    # Update this env var when ACC ships a major physics update (e.g. "1.11").
    game_version_major: str = os.getenv("GAME_VERSION_MAJOR", "1.10")

    # Worker lease: seconds before a claimed-but-silent lap is reset to pending
    worker_lease_minutes: int = int(os.getenv("WORKER_LEASE_MINUTES", "5"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")


cfg = _Config()
