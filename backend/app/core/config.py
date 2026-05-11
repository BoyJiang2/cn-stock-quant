from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"


@dataclass(frozen=True)
class Settings:
    app_name: str = "CN Stock Quant"
    database_url: str = f"sqlite:///{DATA_DIR / 'quant.db'}"
    default_benchmark: str = "000300"


settings = Settings()

