"""Keep non-origin input out of the framing policy."""

import pytest
from pydantic import ValidationError

from shared.models import AppFrameSigninSettings


@pytest.mark.parametrize("origin", [
    "http://halo.example.com",
    "https://*.example.com",
    "https://halo.example.com/path",
    "https://halo.example.com https://unrelated.example.com",
    "https://halo.example.com;frame-src",
    "https://user@halo.example.com",
])
def test_signin_settings_require_exact_https_origins(origin):
    with pytest.raises(ValidationError):
        AppFrameSigninSettings(allowed_origins=[origin])


def test_signin_settings_canonicalize_and_deduplicate_origins():
    settings = AppFrameSigninSettings(allowed_origins=[
        "https://Halo.Example.com:443",
        "https://halo.example.com",
    ])
    assert settings.allowed_origins == ["https://halo.example.com"]
