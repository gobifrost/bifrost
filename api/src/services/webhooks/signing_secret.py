"""A webhook source's HMAC signing secret: write-only and encrypted at rest.

Callers set the secret as ``config["secret"]`` when creating or updating a
source, or rotate it through ``POST /api/events/sources/{id}/rotate-secret``.
It is stored only as ciphertext under ``state["secret_encrypted"]``. The
adapter decrypts it to verify incoming requests, and nothing else reads it.
Responses, manifests and Solution bundles never carry it. The create or rotate
response returns it once.
"""

import secrets
from typing import Any

from src.core.security import decrypt_secret, encrypt_secret

CONFIG_KEY = "secret"
STATE_KEY = "secret_encrypted"


def uses_signing_secret(adapter: Any) -> bool:
    """True when the adapter's config schema declares a signing secret."""
    return CONFIG_KEY in (adapter.config_schema or {}).get("properties", {})


def split_signing_secret(
    config: dict[str, Any],
) -> tuple[dict[str, Any], bool, str | None]:
    """Return ``config`` without the secret, whether the key was sent, and its value.

    A sent key whose value is empty or null asks for the secret to be cleared.
    """
    value = config.get(CONFIG_KEY)
    if value is not None and not isinstance(value, str):
        raise ValueError("Webhook secret must be a string")
    remaining = {key: item for key, item in config.items() if key != CONFIG_KEY}
    return remaining, CONFIG_KEY in config, value or None


def with_signing_secret(state: dict[str, Any] | None, secret: str | None) -> dict[str, Any]:
    """Return ``state`` holding ``secret`` encrypted, or holding none when it is None."""
    updated = {key: item for key, item in (state or {}).items() if key != STATE_KEY}
    if secret:
        updated[STATE_KEY] = encrypt_secret(secret)
    return updated


def carry_signing_secret(
    previous_state: dict[str, Any] | None,
    new_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return ``new_state`` keeping the encrypted secret ``previous_state`` holds."""
    carried = dict(new_state or {})
    encrypted = (previous_state or {}).get(STATE_KEY)
    if encrypted:
        carried[STATE_KEY] = encrypted
    return carried


def signing_secret_set(state: dict[str, Any] | None) -> bool:
    return bool((state or {}).get(STATE_KEY))


def read_signing_secret(state: dict[str, Any] | None) -> str | None:
    """Decrypt the stored secret. Only request verification may call this."""
    encrypted = (state or {}).get(STATE_KEY)
    return decrypt_secret(encrypted) if encrypted else None


def generate_signing_secret() -> str:
    return secrets.token_urlsafe(32)
