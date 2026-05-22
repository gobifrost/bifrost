"""
Bifrost SDK Credentials Storage

Multi-record credential storage for the CLI. Supports per-URL credentials
across multiple Bifrost instances simultaneously, with three backends:

- EnvBackend (read-only): BIFROST_API_URL + BIFROST_ACCESS_TOKEN + BIFROST_REFRESH_TOKEN
- KeyringBackend: OS-native credential storage via the `keyring` library
- JsonBackend: ~/.bifrost/credentials.json as a dict-of-URLs

Resolution order: env vars → persistent (keychain or JSON) → legacy single-record JSON.
"""

import json
import os
import platform
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

KEYRING_SERVICE = "bifrost"


# --------------------------------------------------------------------------- #
# Public dataclass
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Credentials:
    """A single set of CLI credentials for one Bifrost API URL."""

    api_url: str
    access_token: str
    refresh_token: str
    expires_at: str  # ISO 8601 with timezone

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "Credentials":
        return cls(
            api_url=data["api_url"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
        )


# --------------------------------------------------------------------------- #
# Path helpers (unchanged)
# --------------------------------------------------------------------------- #

def get_config_dir() -> Path:
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Bifrost"
        return Path.home() / "Bifrost"
    return Path.home() / ".bifrost"


def get_credentials_path() -> Path:
    return get_config_dir() / "credentials.json"


# --------------------------------------------------------------------------- #
# Backend protocol + implementations
# --------------------------------------------------------------------------- #

class Backend(Protocol):
    """Storage backend for per-URL credentials (env / keychain / JSON)."""

    def get(self, api_url: str) -> Credentials | None:
        """Return credentials for the given URL, or None if absent."""
        raise NotImplementedError

    def save(self, creds: Credentials) -> None:
        """Persist credentials, replacing any existing entry for that URL."""
        raise NotImplementedError

    def clear(self, api_url: str) -> None:
        """Remove credentials for the given URL. No-op if absent."""
        raise NotImplementedError

    def list_urls(self) -> list[str]:
        """Return all URLs that currently have stored credentials."""
        raise NotImplementedError


class EnvBackend:
    """Read-only backend that returns credentials assembled from env vars."""

    def get(self, api_url: str) -> Credentials | None:
        env_url = os.environ.get("BIFROST_API_URL", "").rstrip("/")
        if not env_url or env_url != api_url.rstrip("/"):
            return None
        access = os.environ.get("BIFROST_ACCESS_TOKEN", "")
        refresh = os.environ.get("BIFROST_REFRESH_TOKEN", "")
        if not access or not refresh:
            return None
        # Env-sourced credentials have no recorded expiry; use a far-future
        # placeholder so is_token_expired() doesn't trigger automatic refresh.
        # Refresh-on-401 still works in client.py.
        return Credentials(
            api_url=env_url,
            access_token=access,
            refresh_token=refresh,
            expires_at="2099-01-01T00:00:00+00:00",
        )

    def save(self, creds: Credentials) -> None:
        # Env backend is read-only — saves silently noop.
        return

    def clear(self, api_url: str) -> None:
        return

    def list_urls(self) -> list[str]:
        env_url = os.environ.get("BIFROST_API_URL", "").rstrip("/")
        if env_url and os.environ.get("BIFROST_ACCESS_TOKEN") and os.environ.get("BIFROST_REFRESH_TOKEN"):
            return [env_url]
        return []


class JsonBackend:
    """Multi-record JSON store at ~/.bifrost/credentials.json."""

    def _load(self) -> dict[str, dict]:
        path = get_credentials_path()
        if not path.exists():
            return {}
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        # Multi-record format: {url: {fields...}}. If we see the legacy
        # top-level shape (any credential field at the top level), the
        # migration path will rewrite it; treat as empty for now.
        if not isinstance(data, dict):
            return {}
        legacy_keys = {"api_url", "access_token", "refresh_token", "expires_at"}
        if legacy_keys & set(data.keys()):
            return {}  # legacy single-record or malformed; migration handles it
        # Defensive: drop any entries whose value isn't a dict (corrupted file).
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    def _save(self, store: dict[str, dict]) -> None:
        config_dir = get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        if platform.system() != "Windows":
            config_dir.chmod(0o700)
        path = get_credentials_path()
        with open(path, "w") as f:
            json.dump(store, f, indent=2)
        if platform.system() != "Windows":
            path.chmod(0o600)

    def get(self, api_url: str) -> Credentials | None:
        store = self._load()
        record = store.get(api_url.rstrip("/"))
        if not record:
            return None
        try:
            return Credentials.from_dict(record)
        except KeyError:
            return None

    def save(self, creds: Credentials) -> None:
        store = self._load()
        store[creds.api_url.rstrip("/")] = creds.to_dict()
        self._save(store)

    def clear(self, api_url: str) -> None:
        store = self._load()
        store.pop(api_url.rstrip("/"), None)
        self._save(store)

    def list_urls(self) -> list[str]:
        return list(self._load().keys())


class KeyringBackend:
    """OS-native credential storage via the `keyring` library.

    URLs are tracked in a separate index entry because keyring backends
    don't expose a `list` operation across (service, username) pairs.
    """

    INDEX_USERNAME = "__index__"

    def __init__(self, _keyring=None):
        if _keyring is None:
            import keyring as _keyring  # noqa: PLR0402
        self._kr = _keyring

    def _load_index(self) -> set[str]:
        raw = self._kr.get_password(KEYRING_SERVICE, self.INDEX_USERNAME)
        if not raw:
            return set()
        try:
            return set(json.loads(raw))
        except (json.JSONDecodeError, ValueError):
            return set()

    def _save_index(self, index: set[str]) -> None:
        self._kr.set_password(KEYRING_SERVICE, self.INDEX_USERNAME, json.dumps(sorted(index)))

    def get(self, api_url: str) -> Credentials | None:
        api_url = api_url.rstrip("/")
        raw = self._kr.get_password(KEYRING_SERVICE, api_url)
        if not raw:
            return None
        try:
            return Credentials.from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError):
            return None

    def save(self, creds: Credentials) -> None:
        api_url = creds.api_url.rstrip("/")
        self._kr.set_password(KEYRING_SERVICE, api_url, json.dumps(creds.to_dict()))
        idx = self._load_index()
        idx.add(api_url)
        self._save_index(idx)

    def clear(self, api_url: str) -> None:
        api_url = api_url.rstrip("/")
        try:
            self._kr.delete_password(KEYRING_SERVICE, api_url)
        except Exception:
            pass  # already absent
        idx = self._load_index()
        idx.discard(api_url)
        self._save_index(idx)

    def list_urls(self) -> list[str]:
        return sorted(self._load_index())


# --------------------------------------------------------------------------- #
# Backend selection
# --------------------------------------------------------------------------- #

_keyring_fallback_reason: str | None = None


def _select_persistent_backend() -> Backend:
    """
    Choose the persistent backend.

    Order: try `keyring` (probe with a no-op read so headless Linux fails
    fast at startup), fall back to JSON. On fallback, stash the reason in
    `_keyring_fallback_reason` so `warn_if_keyring_fallback()` can surface
    it during interactive flows like `bifrost login`. Routine calls stay
    silent — every `bifrost files` invocation would otherwise pollute
    stderr (and tool_result text for MCP/agent callers).
    """
    global _keyring_fallback_reason

    try:
        import keyring
        import keyring.errors
    except ImportError:
        _keyring_fallback_reason = "keyring package not installed"
        return JsonBackend()

    try:
        keyring.get_keyring()
        # Probe — surfaces NoKeyringError / SecretServiceError / DBusException
        # when the backend is nominally available but the OS service isn't.
        keyring.get_password(KEYRING_SERVICE, "__probe__")
        return KeyringBackend(_keyring=keyring)
    except (keyring.errors.NoKeyringError, keyring.errors.KeyringError, Exception) as e:
        _keyring_fallback_reason = type(e).__name__
        return JsonBackend()


def warn_if_keyring_fallback() -> None:
    """
    Print a one-time stderr warning if the persistent backend fell back to
    JSON. Call this from interactive entry points (e.g. `bifrost login`)
    where the user is actively configuring credentials and should know
    their tokens aren't in the OS keychain.
    """
    import sys

    get_persistent_backend()  # ensure selection has run
    if _keyring_fallback_reason is not None:
        print(
            f"warning: OS keychain unavailable ({_keyring_fallback_reason}); "
            "falling back to ~/.bifrost/credentials.json. "
            "Install/enable a keyring service to upgrade.",
            file=sys.stderr,
        )


_persistent_backend: Backend | None = None


def get_persistent_backend() -> Backend:
    """Memoized accessor for the persistent backend."""
    global _persistent_backend
    if _persistent_backend is None:
        _persistent_backend = _select_persistent_backend()
    return _persistent_backend


def _reset_persistent_backend_for_tests() -> None:
    """Clear the cached backend so tests can swap it out."""
    global _persistent_backend, _keyring_fallback_reason
    _persistent_backend = None
    _keyring_fallback_reason = None


# --------------------------------------------------------------------------- #
# Public functions
# --------------------------------------------------------------------------- #

def _resolve_url(api_url: str | None) -> str | None:
    """
    Resolve which URL the no-arg credentials calls should target.

    Order:
      1. The argument (if given).
      2. BIFROST_API_URL env var.
      3. The first URL in the persistent backend (back-compat for users
         who only have one set; ordering is stable per backend but not
         guaranteed across backends).
    """
    if api_url:
        return api_url.rstrip("/")
    env_url = os.environ.get("BIFROST_API_URL", "").rstrip("/")
    if env_url:
        return env_url
    urls = get_persistent_backend().list_urls()
    if urls:
        return urls[0]
    return None


def _try_migrate_legacy() -> Credentials | None:
    """
    If ~/.bifrost/credentials.json contains the legacy single-record format,
    migrate it into the active persistent backend and return the parsed
    record. On failure, leave the legacy file untouched and return the
    parsed record anyway so callers still get the credentials.
    """
    path = get_credentials_path()
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict) or "api_url" not in raw or "access_token" not in raw:
        return None
    try:
        legacy = Credentials.from_dict(raw)
    except KeyError:
        return None

    # Try to migrate.
    try:
        get_persistent_backend().save(legacy)
    except Exception:
        # Migration failed (disk full, keyring locked, etc.).
        # Return the parsed legacy record so the caller still works,
        # but don't touch the file. Next call will retry migration.
        return legacy

    # Migration succeeded. If the active backend is JsonBackend, save() already
    # rewrote the file in dict-of-URLs format — done. If it's KeyringBackend,
    # the legacy file is still on disk; rewrite it as `{}` (marker that migration ran).
    try:
        with open(path, "r") as f:
            new_contents = json.load(f)
        if isinstance(new_contents, dict) and "api_url" in new_contents:
            # Keyring path: legacy file still on disk; clear it.
            with open(path, "w") as f:
                json.dump({}, f)
            if platform.system() != "Windows":
                path.chmod(0o600)
    except (json.JSONDecodeError, OSError):
        # Best-effort cleanup of the legacy marker file. If we can't read it
        # back or rewrite it, the migration has already succeeded into the
        # backend, so the credential is safe and the marker is non-critical.
        pass

    return legacy


def get_credentials(api_url: str | None = None) -> dict | None:
    """
    Resolve credentials for a given API URL.

    Resolution order:
      1. Env vars (EnvBackend) — for ephemeral sessions.
      2. Persistent backend (keychain or JSON) — for long-lived sessions.
      3. Legacy single-record JSON — lazily migrated.

    If api_url is None, the URL is resolved via _resolve_url(). When even
    that fails, we fall through to the legacy file as a last resort to
    learn the URL.

    Returns: dict with keys api_url/access_token/refresh_token/expires_at,
    or None. Returns dict (not Credentials) for back-compat with existing
    callers in client.py / cli.py.
    """
    resolved = _resolve_url(api_url)

    if resolved is None:
        # No URL anywhere; try legacy file as last resort to learn one.
        legacy = _try_migrate_legacy()
        if legacy is None:
            return None
        return legacy.to_dict()

    # 1. Env vars
    env_creds = EnvBackend().get(resolved)
    if env_creds is not None:
        return env_creds.to_dict()

    # 2. Persistent backend
    creds = get_persistent_backend().get(resolved)
    if creds is not None:
        return creds.to_dict()

    # 3. Legacy fallback (and migrate if found)
    legacy = _try_migrate_legacy()
    if legacy is not None and legacy.api_url.rstrip("/") == resolved:
        return legacy.to_dict()
    return None


def save_credentials(
    api_url: str,
    access_token: str,
    refresh_token: str,
    expires_at: str,
) -> None:
    """Persist credentials to the active backend (keychain or JSON)."""
    creds = Credentials(
        api_url=api_url.rstrip("/"),
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
    )
    get_persistent_backend().save(creds)


def clear_credentials(api_url: str | None = None) -> None:
    """
    Remove a single URL's credentials from the persistent backend.

    No-arg behavior uses the same resolution as get_credentials(): env var,
    then first URL in store. So a `bifrost logout` after a `bifrost login`
    targets the same record `get_credentials()` would have returned, even
    when multiple URLs are present.
    """
    target = _resolve_url(api_url)
    if target is None:
        return
    get_persistent_backend().clear(target)


def list_credentials() -> list[str]:
    """Return all URLs that have stored credentials."""
    return get_persistent_backend().list_urls()


def is_token_expired(buffer_seconds: int = 60, api_url: str | None = None) -> bool:
    """Return True if the resolved credentials' access token is expired."""
    creds = get_credentials(api_url)
    if not creds:
        return True
    expires_at_str = creds.get("expires_at")
    if not expires_at_str:
        return True
    try:
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (expires_at - now).total_seconds() <= buffer_seconds
    except (ValueError, AttributeError):
        return True
