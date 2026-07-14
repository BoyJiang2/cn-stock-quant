import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"


@dataclass(frozen=True)
class Settings:
    app_name: str = "CN Stock Quant"
    database_url: str = f"sqlite:///{DATA_DIR / 'quant.db'}"
    default_benchmark: str = "000300"
    allow_remote_llm: bool = os.getenv("ALLOW_REMOTE_LLM", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY", "").strip() or None
    openai_model: str | None = os.getenv("OPENAI_MODEL", "").strip() or None
    remote_llm_configured: bool = bool(
        os.getenv("OPENAI_API_KEY", "").strip() and os.getenv("OPENAI_MODEL", "").strip()
    )
    wecom_webhook_url: str | None = os.getenv("WECOM_WEBHOOK_URL", "").strip() or None
    wecom_webhook_configured: bool = bool(os.getenv("WECOM_WEBHOOK_URL", "").strip())


settings = Settings()
