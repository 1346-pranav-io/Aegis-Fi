"""API settings from the environment. No credential has a default."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_API_", extra="forbid")

    jwt_algorithm: str = "HS256"
    jwt_key: str  # HS256: shared secret (local dev); RS256: PEM public key
    jwt_issuer: str
    jwt_audience: str = "aegis-api"
    # one connection URL per database login (docs/architecture: shared logins, human ids come
    # from the verified token)
    app_url: str = Field(description="aegis_app login")
    approver_url: str = Field(description="aegis_approver login")
    reviewer_url: str = Field(description="aegis_reviewer login")
