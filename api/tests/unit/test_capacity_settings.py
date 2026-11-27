"""Optional capacity deployment settings normalize Compose's empty values."""

from pydantic import SecretStr

from src.config import Settings


def test_empty_compose_capacity_settings_mean_no_provider():
    settings = Settings(worker_capacity_url="", worker_capacity_token=SecretStr(""))

    assert settings.worker_capacity_url is None
    assert settings.worker_capacity_token is None


def test_explicit_capacity_configuration_is_preserved():
    settings = Settings(
        worker_capacity_url="https://capacity.example.test",
        worker_capacity_token=SecretStr("test-capacity-token"),
    )

    assert settings.worker_capacity_url == "https://capacity.example.test"
    assert settings.worker_capacity_token is not None
    assert settings.worker_capacity_token.get_secret_value() == "test-capacity-token"
