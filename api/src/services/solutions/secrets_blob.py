from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from typing import Any

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# Blob format version. v2 = self-describing scrypt envelope (per-export random
# salt + work factor). v1 (HKDF + fixed salt via encrypt_with_key) is gone — it
# was unreleased, so there is no v1 read path to maintain.
BLOB_VERSION = 2

# scrypt parameters for the user-PASSWORD KDF. Unlike the at-rest secret path
# (which keys off the high-entropy settings.secret_key and correctly uses fast
# HKDF), this blob is unlocked by a user-chosen password and ships inside a
# downloadable zip — so a leaked zip + weak password is offline-brute-forceable
# unless the KDF has a real work factor. scrypt is memory-hard.
#
# n=2**15 (32768), r=8, p=1 → ~32 MiB, ~100 ms on a modern core: enough to make
# offline guessing expensive while keeping a single legit decode snappy. These
# are stored in the envelope so decode reproduces the exact key even if we tune
# them later.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_LEN = 32


@dataclass
class SolutionContent:
    """The sensitive tier of a full-backup export: secret/config values,
    table rows, and solution-owned file sidecars.  Travels only inside the
    password-encrypted .bifrost/secrets.enc blob — never in plaintext.

    ``solution_files`` is a list of dicts with keys:
      ``location``, ``path``, ``sha256``, ``size``, ``content_b64``
    (content bytes are base64-encoded for JSON serialization).
    """

    config_values: dict[str, str] = field(default_factory=dict)
    table_data: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    solution_files: list[dict[str, Any]] = field(default_factory=list)


def _derive_fernet_key(password: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    """scrypt-derive a urlsafe-base64 Fernet key from a password + salt."""
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=n, r=r, p=p)
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def encode_secrets_blob(content: SolutionContent, *, password: str) -> str:
    """Serialize + password-encrypt the sensitive content into one blob string
    (the body of .bifrost/secrets.enc).

    Emits a self-describing JSON envelope: the salt + scrypt params + version are
    NOT secret (they are needed to reproduce the key) and travel in cleartext
    alongside the Fernet ciphertext. A fresh random salt per call guarantees two
    exports of identical content+password produce different blobs."""
    salt = os.urandom(_SALT_BYTES)
    key = _derive_fernet_key(password, salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    payload: dict[str, Any] = {
        "config_values": content.config_values,
        "table_data": content.table_data,
    }
    if content.solution_files:
        payload["solution_files"] = content.solution_files
    inner = json.dumps(payload)
    token = Fernet(key).encrypt(inner.encode())
    envelope = {
        "v": BLOB_VERSION,
        "kdf": "scrypt",
        "n": _SCRYPT_N,
        "r": _SCRYPT_R,
        "p": _SCRYPT_P,
        "salt": base64.urlsafe_b64encode(salt).decode(),
        "ciphertext": token.decode(),
    }
    return json.dumps(envelope)


def decode_secrets_blob(blob: str, *, password: str) -> SolutionContent:
    """Decrypt + parse the blob. Raises cryptography.fernet.InvalidToken on a
    wrong password (let it propagate — callers map it to BadExportPassword)."""
    envelope = json.loads(blob)
    salt = base64.urlsafe_b64decode(envelope["salt"])
    key = _derive_fernet_key(
        password,
        salt,
        n=int(envelope["n"]),
        r=int(envelope["r"]),
        p=int(envelope["p"]),
    )
    inner = Fernet(key).decrypt(envelope["ciphertext"].encode())
    payload = json.loads(inner)
    return SolutionContent(
        config_values=payload.get("config_values", {}),
        table_data=payload.get("table_data", {}),
        solution_files=payload.get("solution_files", []),
    )
