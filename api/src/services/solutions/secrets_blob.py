from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.core.security import decrypt_with_key, encrypt_with_key

BLOB_VERSION = 1


@dataclass
class SolutionContent:
    """The sensitive tier of a full-backup export: secret/config values and
    table rows. Travels only inside the password-encrypted .bifrost/secrets.enc
    blob — never in plaintext."""

    config_values: dict[str, str] = field(default_factory=dict)
    table_data: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def encode_secrets_blob(content: SolutionContent, *, password: str) -> str:
    """Serialize + password-encrypt the sensitive content into one blob string
    (the body of .bifrost/secrets.enc)."""
    payload = json.dumps(
        {
            "version": BLOB_VERSION,
            "config_values": content.config_values,
            "table_data": content.table_data,
        }
    )
    return encrypt_with_key(payload, password)


def decode_secrets_blob(blob: str, *, password: str) -> SolutionContent:
    """Decrypt + parse the blob. Raises cryptography.fernet.InvalidToken on a
    wrong password (let it propagate — callers map it to an actionable error)."""
    payload = json.loads(decrypt_with_key(blob, password))
    return SolutionContent(
        config_values=payload.get("config_values", {}),
        table_data=payload.get("table_data", {}),
    )
