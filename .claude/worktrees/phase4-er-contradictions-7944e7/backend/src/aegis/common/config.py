"""Typed settings. No secrets in code; environment only."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_", extra="forbid")
    database_url: str  # required; no credential-bearing default (S-2)
    log_level: str = "INFO"
