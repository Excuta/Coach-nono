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

    # GPU / LLM coach
    ollama_url: str = os.getenv("OLLAMA_URL", "http://coach-llm:11434")
    coach_model: str = os.getenv("COACH_MODEL", "qwen2.5:7b-instruct-q4_K_M")

    # Worker lease: seconds before a claimed-but-silent lap is reset to pending
    worker_lease_minutes: int = int(os.getenv("WORKER_LEASE_MINUTES", "5"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")


cfg = _Config()
