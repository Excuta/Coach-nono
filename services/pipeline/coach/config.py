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

    # GPU / LLM coach
    ollama_url: str = os.getenv("OLLAMA_URL", "http://coach-llm:11434")
    coach_model: str = os.getenv("COACH_MODEL", "qwen2.5:7b-instruct-q4_K_M")

    # TTS output (Phase E+): uses Nono's real voice via XTTS v2 zero-shot cloning.
    # Set COACH_TTS=xtts and point TTS_VOICE_REF at a clean WAV sample of her voice.
    # Leave empty for text-only output (default until voice pack recordings are ready).
    coach_tts: str = os.getenv("COACH_TTS", "")
    tts_voice_ref: str = os.getenv("TTS_VOICE_REF", "")  # path to Nono's reference WAV

    # Worker lease: seconds before a claimed-but-silent lap is reset to pending
    worker_lease_minutes: int = int(os.getenv("WORKER_LEASE_MINUTES", "5"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")


cfg = _Config()
