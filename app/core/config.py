"""Environment-backed settings with local dotenv support."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings; secret values use Pydantic SecretStr wrappers."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "development"
    database_url: SecretStr | None = None
    database_direct_url: SecretStr | None = None
    session_hash_key: SecretStr | None = None
    login_rate_key: SecretStr | None = None
    auth_proxy_key: SecretStr | None = None
    public_site_origin: str | None = None
    session_idle_minutes: int = Field(default=30, ge=1, le=1440)
    session_absolute_hours: int = Field(default=12, ge=1, le=168)
    public_media_base_url: str | None = None
    aws_endpoint_url_s3: str | None = None
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    aws_region: str | None = None
    aws_s3_bucket: str | None = None
    aws_s3_private_bucket_confirmed: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return one settings object per process."""
    return Settings()
