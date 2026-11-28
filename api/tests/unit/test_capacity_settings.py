"""Optional capacity configuration works before an operator connects a provider."""

from pydantic import SecretStr

from src.config import Settings


def test_empty_compose_capacity_settings_mean_no_provider_or_image_override():
    settings = Settings(
        worker_capacity_url="", worker_capacity_token=SecretStr(""), worker_capacity_image="",
    )
    assert settings.worker_capacity_url is None
    assert settings.worker_capacity_token is None
    assert settings.worker_capacity_image is None


def test_explicit_capacity_configuration_is_preserved():
    image = "registry.cloudflare.com/account/worker@sha256:" + "a" * 64
    settings = Settings(
        worker_capacity_url="https://capacity.example.test",
        worker_capacity_token=SecretStr("test-capacity-token"), worker_capacity_image=image,
    )
    assert settings.worker_capacity_url == "https://capacity.example.test"
    assert settings.worker_capacity_token is not None
    assert settings.worker_capacity_token.get_secret_value() == "test-capacity-token"
    assert settings.worker_capacity_image == image
