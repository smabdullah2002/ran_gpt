from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
	model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

	APP_NAME: str = "ran_gpt_backend"
	APP_VERSION: str = "0.1.0"
	PROJECT_URL: str | None = None
	SUPABASE_KEY: str | None = None
	DIRECT_CONNECTION_KEY: str | None = None
	SUPABASE_TABLE_NAME: str = "document_chunks"

	INGEST_MAX_DEPTH: int = 2
	INGEST_MAX_PAGES: int = 50
	INGEST_RATE_LIMIT_SECONDS: float = 0.2
	INGEST_REQUEST_TIMEOUT_SECONDS: int = 15
	INGEST_REQUEST_RETRIES: int = 2
	INGEST_MAX_CONCURRENCY: int = 8
	INGEST_PER_DOMAIN_CONCURRENCY: int = 3
	INGEST_PROCESSING_CONCURRENCY: int = 4
	INGEST_MIN_WORDS_AFTER_CLEANING: int = 40
	INGEST_MIN_WORDS_AFTER_CLEANING_FALLBACK: int = 8
	INGEST_MIN_WORDS_AFTER_CLEANING_BROWSER: int = 10
	INGEST_ALLOW_TINY_FALLBACK_CONTENT: bool = True
	INGEST_CHUNK_SIZE_WORDS: int = 500
	INGEST_CHUNK_OVERLAP_WORDS: int = 50
	INGEST_CACHE_PATH: str = "backend/data/ingestion_cache.json"


@lru_cache
def get_settings() -> Settings:
	return Settings()
