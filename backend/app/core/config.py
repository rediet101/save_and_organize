from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Multilingual Save-and-Organize API"
    environment: str = "development"
    log_level: str = "INFO"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # Tried in order when the primary model is overloaded or rate-limited.
    gemini_fallback_models: list[str] = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/save_organize"

    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
