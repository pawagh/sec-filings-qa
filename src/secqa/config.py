import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root in a source checkout; containers install the package into site-packages, so they set
# SECQA_HOME=/app where config/ and sql/ are copied.
ROOT = Path(os.environ.get("SECQA_HOME") or Path(__file__).resolve().parents[2])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://secqa:secqa@localhost:5432/secqa"
    sec_user_agent: str = "secqa-portfolio-project your-email@example.com"
    sec_max_rps: float = 8.0  # SEC fair-access limit is 10 req/s
    cache_dir: Path = ROOT / "data" / "raw"
    api_cache_ttl_hours: float = 24.0

    companies_csv: Path = ROOT / "config" / "companies.csv"
    concepts_csv: Path = ROOT / "config" / "metric_concepts.csv"
    migrations_dir: Path = ROOT / "sql" / "migrations"
    fiscal_years_raw: str = Field(default="2023,2024,2025", alias="FISCAL_YEARS")

    embed_model: str = "ibm-granite/granite-embedding-small-english-r2"
    embed_batch_size: int = 32
    chunk_size: int = 1200
    chunk_overlap: int = 200

    llm_provider: str = "none"  # gemini | ollama | none
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "granite3.3:8b"

    @property
    def fiscal_years(self) -> list[int]:
        return sorted(int(y) for y in self.fiscal_years_raw.split(",") if y.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
