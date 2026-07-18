"""Strict application settings loaded from the process environment."""

from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Validated settings required by the initial backend package."""

    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=None,
        extra="ignore",
        frozen=True,
        loc_by_alias=False,
        populate_by_name=True,
    )

    app_env: Literal["development", "test", "production"] = Field(
        validation_alias="APP_ENV"
    )
    app_base_url: AnyHttpUrl = Field(validation_alias="APP_BASE_URL")
    database_url: str = Field(min_length=1, validation_alias="DATABASE_URL")
    model_provider: Literal["lm-studio"] = Field(validation_alias="MODEL_PROVIDER")
    lm_studio_base_url: AnyHttpUrl = Field(validation_alias="LM_STUDIO_BASE_URL")
    lm_studio_api_key: SecretStr = Field(
        min_length=1, validation_alias="LM_STUDIO_API_KEY"
    )
    lm_studio_model_id: str = Field(min_length=1, validation_alias="LM_STUDIO_MODEL_ID")

    @field_validator("database_url", "lm_studio_api_key", "lm_studio_model_id")
    @classmethod
    def reject_blank_values(cls, value: SecretStr | str) -> SecretStr | str:
        """Reject values that only satisfy length constraints with whitespace."""
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not raw_value.strip():
            raise ValueError("value must not be blank")
        if isinstance(value, str) and value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the installed psycopg driver")
        return value


def load_settings() -> AppSettings:
    """Load and validate settings without implicit dotenv or fallback values."""
    return AppSettings()
