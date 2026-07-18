import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch

from orchestrator.settings import AppSettings, load_settings

REQUIRED_ENVIRONMENT = {
    "APP_ENV": "development",
    "APP_BASE_URL": "http://localhost:3000",
    "DATABASE_URL": "postgresql+psycopg://local/test",
    "MODEL_PROVIDER": "lm-studio",
    "LM_STUDIO_BASE_URL": "http://host.docker.internal:1234/v1",
    "LM_STUDIO_API_KEY": "local-protocol-placeholder",
    "LM_STUDIO_MODEL_ID": "qwen3.6-27b",
}


@pytest.fixture(autouse=True)
def settings_environment(monkeypatch: MonkeyPatch) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


def test_load_settings_reads_every_required_value() -> None:
    settings = load_settings()

    assert isinstance(settings, AppSettings)
    assert settings.app_env == "development"
    assert str(settings.app_base_url) == "http://localhost:3000/"
    assert settings.database_url == "postgresql+psycopg://local/test"
    assert settings.model_provider == "lm-studio"
    assert str(settings.lm_studio_base_url) == ("http://host.docker.internal:1234/v1")
    assert settings.lm_studio_model_id == "qwen3.6-27b"
    assert (
        settings.lm_studio_api_key.get_secret_value()
        == REQUIRED_ENVIRONMENT["LM_STUDIO_API_KEY"]
    )
    assert "local-protocol-placeholder" not in repr(settings)


def test_load_settings_reports_a_missing_required_value(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("LM_STUDIO_MODEL_ID")

    with pytest.raises(ValidationError) as exc_info:
        load_settings()

    errors = exc_info.value.errors(include_url=False)
    assert len(errors) == 1
    assert errors[0]["type"] == "missing"
    assert errors[0]["loc"] == ("lm_studio_model_id",)


@pytest.mark.parametrize(
    ("name", "invalid_value", "field"),
    [
        ("APP_ENV", "Development", "app_env"),
        ("MODEL_PROVIDER", "fake", "model_provider"),
        ("DATABASE_URL", "postgresql+asyncpg://local/test", "database_url"),
        ("LM_STUDIO_BASE_URL", "not-a-url", "lm_studio_base_url"),
        ("LM_STUDIO_API_KEY", "   ", "lm_studio_api_key"),
        ("LM_STUDIO_MODEL_ID", "", "lm_studio_model_id"),
    ],
)
def test_load_settings_rejects_invalid_values(
    monkeypatch: MonkeyPatch,
    name: str,
    invalid_value: str,
    field: str,
) -> None:
    monkeypatch.setenv(name, invalid_value)

    with pytest.raises(ValidationError) as exc_info:
        load_settings()

    assert exc_info.value.errors(include_url=False)[0]["loc"] == (field,)
