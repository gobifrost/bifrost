"""Tests for bifrost.credentials backend abstraction and multi-record store."""

import os

import pytest

from bifrost import credentials as creds_mod
from bifrost.credentials import Credentials, EnvBackend, JsonBackend


# ---------- Credentials dataclass ----------

class TestCredentialsDataclass:
    def test_round_trip_to_dict_and_back(self):
        c = Credentials(
            api_url="http://localhost:38421",
            access_token="at",
            refresh_token="rt",
            expires_at="2030-01-01T00:00:00+00:00",
        )
        d = c.to_dict()
        assert d == {
            "api_url": "http://localhost:38421",
            "access_token": "at",
            "refresh_token": "rt",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }
        assert Credentials.from_dict(d) == c


# ---------- EnvBackend ----------

class TestEnvBackend:
    def test_returns_credentials_when_all_three_env_vars_set(self, monkeypatch):
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")
        monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "at")
        monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "rt")
        backend = EnvBackend()
        result = backend.get("http://localhost:38421")
        assert result is not None
        assert result.access_token == "at"
        assert result.refresh_token == "rt"

    def test_returns_none_when_url_does_not_match(self, monkeypatch):
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")
        monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "at")
        monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "rt")
        backend = EnvBackend()
        # Ask for a different URL than what's in BIFROST_API_URL
        assert backend.get("http://localhost:99999") is None

    def test_returns_none_when_access_token_missing(self, monkeypatch):
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "rt")
        backend = EnvBackend()
        assert backend.get("http://localhost:38421") is None

    def test_returns_none_when_refresh_token_missing(self, monkeypatch):
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")
        monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "at")
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        backend = EnvBackend()
        assert backend.get("http://localhost:38421") is None

    def test_save_is_noop(self, monkeypatch):
        backend = EnvBackend()
        # Should not raise; env backend is read-only
        backend.save(Credentials("http://x", "at", "rt", "2030-01-01T00:00:00+00:00"))
        assert os.environ.get("BIFROST_ACCESS_TOKEN") in (None, "")  # didn't pollute env


# ---------- Credential provenance ----------

class TestCredentialProvenance:
    @pytest.fixture
    def isolated_store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        monkeypatch.setattr(
            creds_mod,
            "get_config_path",
            lambda: tmp_path / "config.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_persistent_backend", JsonBackend())
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_SOLUTION_ID", raising=False)
        monkeypatch.chdir(tmp_path)
        yield tmp_path
        creds_mod._reset_persistent_backend_for_tests()

    def test_url_only_process_override_does_not_borrow_dotenv_tokens(
        self, isolated_store, monkeypatch, caplog
    ):
        (isolated_store / ".env").write_text(
            "BIFROST_API_URL=http://localhost:36092\n"
            "BIFROST_ACCESS_TOKEN=localhost-access\n"
            "BIFROST_REFRESH_TOKEN=localhost-refresh\n"
        )
        creds_mod.save_credentials(
            "https://prod.example.com",
            "production-access",
            "production-refresh",
            "2099-01-01T00:00:00+00:00",
        )
        monkeypatch.setenv("BIFROST_API_URL", "https://prod.example.com")
        caplog.set_level("WARNING", logger="bifrost.credentials")

        creds_mod.load_dotenv_context()
        result = creds_mod.get_credentials()
        repeated_result = creds_mod.get_credentials()

        assert result is not None
        assert repeated_result == result
        assert result["api_url"] == "https://prod.example.com"
        assert result["access_token"] == "production-access"
        assert result["refresh_token"] == "production-refresh"
        assert "BIFROST_ACCESS_TOKEN" not in os.environ
        assert "BIFROST_REFRESH_TOKEN" not in os.environ
        assert len(caplog.records) == 1
        warning = caplog.records[0]
        assert warning.args == (
            "http://localhost:36092",
            "https://prod.example.com",
        )
        assert "localhost-access" not in warning.getMessage()
        assert "localhost-refresh" not in warning.getMessage()

    def test_complete_process_tuple_wins_over_dotenv_and_persistent(
        self, isolated_store, monkeypatch
    ):
        (isolated_store / ".env").write_text(
            "BIFROST_API_URL=http://localhost:36092\n"
            "BIFROST_ACCESS_TOKEN=localhost-access\n"
            "BIFROST_REFRESH_TOKEN=localhost-refresh\n"
        )
        creds_mod.save_credentials(
            "https://prod.example.com",
            "stored-access",
            "stored-refresh",
            "2099-01-01T00:00:00+00:00",
        )
        monkeypatch.setenv("BIFROST_API_URL", "https://prod.example.com")
        monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "process-access")
        monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "process-refresh")

        creds_mod.load_dotenv_context()
        result = creds_mod.get_credentials()

        assert result is not None
        assert result["access_token"] == "process-access"
        assert result["refresh_token"] == "process-refresh"

    def test_complete_nearest_dotenv_tuple_is_used(self, isolated_store):
        (isolated_store / ".env").write_text(
            "BIFROST_API_URL=http://localhost:36092\n"
            "BIFROST_ACCESS_TOKEN=localhost-access\n"
            "BIFROST_REFRESH_TOKEN=localhost-refresh\n"
        )

        result = creds_mod.get_credentials()

        assert result is not None
        assert result["api_url"] == "http://localhost:36092"
        assert result["access_token"] == "localhost-access"
        assert result["refresh_token"] == "localhost-refresh"

    def test_matching_url_only_process_override_uses_complete_dotenv_tuple(
        self, isolated_store, monkeypatch
    ):
        (isolated_store / ".env").write_text(
            "BIFROST_API_URL=http://localhost:36092\n"
            "BIFROST_ACCESS_TOKEN=localhost-access\n"
            "BIFROST_REFRESH_TOKEN=localhost-refresh\n"
        )
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:36092")

        result = creds_mod.get_credentials()

        assert result is not None
        assert result["access_token"] == "localhost-access"
        assert result["refresh_token"] == "localhost-refresh"

    def test_child_url_only_dotenv_uses_persistent_credentials_not_parent_tokens(
        self, isolated_store, monkeypatch
    ):
        (isolated_store / ".env").write_text(
            "BIFROST_API_URL=http://localhost:36092\n"
            "BIFROST_ACCESS_TOKEN=localhost-access\n"
            "BIFROST_REFRESH_TOKEN=localhost-refresh\n"
        )
        child = isolated_store / "solutions" / "sales"
        child.mkdir(parents=True)
        (child / ".env").write_text("BIFROST_API_URL=https://prod.example.com\n")
        creds_mod.save_credentials(
            "https://prod.example.com",
            "production-access",
            "production-refresh",
            "2099-01-01T00:00:00+00:00",
        )
        creds_mod.save_credentials(
            "http://localhost:36092",
            "stored-local-access",
            "stored-local-refresh",
            "2099-01-01T00:00:00+00:00",
        )
        monkeypatch.chdir(child)

        result = creds_mod.get_credentials()

        assert result is not None
        assert result["api_url"] == "https://prod.example.com"
        assert result["access_token"] == "production-access"
        assert result["refresh_token"] == "production-refresh"

    def test_partial_dotenv_tuple_falls_through_to_persistent(
        self, isolated_store
    ):
        (isolated_store / ".env").write_text(
            "BIFROST_API_URL=https://prod.example.com\n"
            "BIFROST_ACCESS_TOKEN=partial-access\n"
        )
        creds_mod.save_credentials(
            "https://prod.example.com",
            "production-access",
            "production-refresh",
            "2099-01-01T00:00:00+00:00",
        )

        result = creds_mod.get_credentials()

        assert result is not None
        assert result["access_token"] == "production-access"
        assert result["refresh_token"] == "production-refresh"

    def test_filtered_dotenv_bootstrap_preserves_non_auth_context(
        self, isolated_store
    ):
        (isolated_store / ".env").write_text(
            "BIFROST_API_URL=http://localhost:36092\n"
            "BIFROST_ACCESS_TOKEN=localhost-access\n"
            "BIFROST_REFRESH_TOKEN=localhost-refresh\n"
            "BIFROST_SOLUTION_ID=solution-id\n"
        )

        creds_mod.load_dotenv_context()

        assert os.environ["BIFROST_SOLUTION_ID"] == "solution-id"
        assert "BIFROST_API_URL" not in os.environ
        assert "BIFROST_ACCESS_TOKEN" not in os.environ
        assert "BIFROST_REFRESH_TOKEN" not in os.environ


# ---------- JsonBackend (multi-record) ----------

class TestJsonBackendMultiRecord:
    @pytest.fixture
    def tmp_creds_path(self, tmp_path, monkeypatch):
        path = tmp_path / "credentials.json"
        monkeypatch.setattr(creds_mod, "get_credentials_path", lambda: path)
        return path

    def test_save_and_get_single_record(self, tmp_creds_path):
        backend = JsonBackend()
        c = Credentials("http://localhost:38421", "at", "rt", "2030-01-01T00:00:00+00:00")
        backend.save(c)
        assert backend.get("http://localhost:38421") == c

    def test_save_two_urls_independently(self, tmp_creds_path):
        backend = JsonBackend()
        c1 = Credentials("http://localhost:38421", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        c2 = Credentials("https://prod.example.com", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        backend.save(c1)
        backend.save(c2)
        assert backend.get("http://localhost:38421") == c1
        assert backend.get("https://prod.example.com") == c2

    def test_clear_one_url_leaves_others(self, tmp_creds_path):
        backend = JsonBackend()
        c1 = Credentials("http://a", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        c2 = Credentials("http://b", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        backend.save(c1)
        backend.save(c2)
        backend.clear("http://a")
        assert backend.get("http://a") is None
        assert backend.get("http://b") == c2

    def test_list_returns_all_urls(self, tmp_creds_path):
        backend = JsonBackend()
        backend.save(Credentials("http://a", "at1", "rt1", "2030-01-01T00:00:00+00:00"))
        backend.save(Credentials("http://b", "at2", "rt2", "2030-01-01T00:00:00+00:00"))
        assert sorted(backend.list_urls()) == ["http://a", "http://b"]

    def test_get_returns_none_when_file_missing(self, tmp_creds_path):
        backend = JsonBackend()
        assert backend.get("http://anything") is None

    def test_file_permissions_are_0600(self, tmp_creds_path):
        import platform
        if platform.system() == "Windows":
            pytest.skip("POSIX permissions not applicable on Windows")
        backend = JsonBackend()
        backend.save(Credentials("http://a", "at", "rt", "2030-01-01T00:00:00+00:00"))
        mode = tmp_creds_path.stat().st_mode & 0o777
        assert mode == 0o600


# ---------- Public surface: no-arg resolution ----------

class TestNoArgResolution:
    """
    Verify get_credentials() / clear_credentials() agree on which URL
    they target when called without arguments. Latent bug if they don't:
    `bifrost logout` after `bifrost login` would leave creds on disk.
    """

    @pytest.fixture
    def tmp_creds_path(self, tmp_path, monkeypatch):
        path = tmp_path / "credentials.json"
        monkeypatch.setattr(creds_mod, "get_credentials_path", lambda: path)
        monkeypatch.setattr(creds_mod, "get_config_path", lambda: tmp_path / "config.json")
        creds_mod._reset_persistent_backend_for_tests()
        # Clear any inherited env vars so the env-var arm of resolution is off.
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        return path

    def test_no_arg_get_returns_none_when_two_present_without_default(self, tmp_creds_path):
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        assert creds_mod.get_credentials() is None

    def test_no_arg_clear_targets_same_url_as_no_arg_get(self, tmp_creds_path):
        """clear_credentials() with no arg must clear what get_credentials() returns."""
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        creds_mod.set_default_connection("http://second")

        before = creds_mod.get_credentials()
        assert before is not None and before["api_url"] == "http://second"

        creds_mod.clear_credentials()  # no arg

        # 'second' should be gone; 'first' must remain
        assert creds_mod.get_credentials("http://second") is None
        assert creds_mod.get_credentials("http://first") is not None
        assert creds_mod.get_default_connection() is None

    def test_no_arg_get_prefers_env_var_over_first_stored(self, tmp_creds_path, monkeypatch):
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        monkeypatch.setenv("BIFROST_API_URL", "http://second")
        result = creds_mod.get_credentials()
        assert result is not None
        assert result["api_url"] == "http://second"

    def test_no_arg_get_returns_only_stored_connection_without_env_or_default(self, tmp_creds_path):
        creds_mod.save_credentials("http://only", "at", "rt", "2030-01-01T00:00:00+00:00")

        result = creds_mod.get_credentials()

        assert result is not None
        assert result["api_url"] == "http://only"

    def test_no_arg_get_uses_default_connection_without_env(self, tmp_creds_path):
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        creds_mod.set_default_connection("http://second")

        result = creds_mod.get_credentials()

        assert result is not None
        assert result["api_url"] == "http://second"

    def test_env_url_overrides_default_connection(self, tmp_creds_path, monkeypatch):
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        creds_mod.set_default_connection("http://second")
        monkeypatch.setenv("BIFROST_API_URL", "http://first")

        result = creds_mod.get_credentials()

        assert result is not None
        assert result["api_url"] == "http://first"

    def test_multiple_connections_without_default_prompts_and_saves_selection(
        self, tmp_creds_path, monkeypatch
    ):
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        monkeypatch.setattr(
            creds_mod,
            "prompt_for_default_connection",
            lambda urls: "http://second",
        )

        result = creds_mod.get_credentials(prompt_for_default=True)

        assert result is not None
        assert result["api_url"] == "http://second"
        assert creds_mod.get_default_connection() == "http://second"

    def test_multiple_connections_without_default_returns_none_when_not_prompting(
        self, tmp_creds_path
    ):
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")

        assert creds_mod.get_credentials() is None


# ---------- KeyringBackend ----------

class FakeKeyring:
    """In-memory keyring used to test KeyringBackend without touching the OS."""

    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str):
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str):
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str):
        self.store.pop((service, username), None)


class FakeFailKeyring:
    """Keyring that raises NoKeyringError on every operation, mimicking headless Linux."""

    def get_password(self, *_a, **_kw):
        import keyring.errors
        raise keyring.errors.NoKeyringError("no backend")

    def set_password(self, *_a, **_kw):
        import keyring.errors
        raise keyring.errors.NoKeyringError("no backend")

    def delete_password(self, *_a, **_kw):
        import keyring.errors
        raise keyring.errors.NoKeyringError("no backend")


class TestKeyringBackend:
    @pytest.fixture
    def fake_kr(self):
        from bifrost.credentials import KeyringBackend
        fake = FakeKeyring()
        backend = KeyringBackend(_keyring=fake)
        return backend, fake

    def test_save_and_get_round_trip(self, fake_kr):
        backend, _ = fake_kr
        c = Credentials("http://localhost:38421", "at", "rt", "2030-01-01T00:00:00+00:00")
        backend.save(c)
        assert backend.get("http://localhost:38421") == c

    def test_save_two_urls_independently(self, fake_kr):
        backend, _ = fake_kr
        c1 = Credentials("http://a", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        c2 = Credentials("http://b", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        backend.save(c1)
        backend.save(c2)
        assert backend.get("http://a") == c1
        assert backend.get("http://b") == c2

    def test_clear_one_leaves_others(self, fake_kr):
        backend, _ = fake_kr
        backend.save(Credentials("http://a", "at1", "rt1", "2030-01-01T00:00:00+00:00"))
        backend.save(Credentials("http://b", "at2", "rt2", "2030-01-01T00:00:00+00:00"))
        backend.clear("http://a")
        assert backend.get("http://a") is None
        assert backend.get("http://b") is not None

    def test_list_urls_via_index(self, fake_kr):
        backend, _ = fake_kr
        backend.save(Credentials("http://a", "at", "rt", "2030-01-01T00:00:00+00:00"))
        backend.save(Credentials("http://b", "at", "rt", "2030-01-01T00:00:00+00:00"))
        assert sorted(backend.list_urls()) == ["http://a", "http://b"]

    def test_get_returns_none_for_unknown_url(self, fake_kr):
        backend, _ = fake_kr
        assert backend.get("http://never-saved") is None


# ---------- Backend selection ----------

class TestBackendSelection:
    def test_keyring_available_returns_keyring_backend(self, monkeypatch):
        from bifrost.credentials import KeyringBackend, _select_persistent_backend
        creds_mod._reset_persistent_backend_for_tests()
        fake = FakeKeyring()
        # Make `keyring.get_keyring()` return our fake AND make `keyring.get_password`
        # (the probe call) succeed. The probe is what the selector uses to verify
        # the backend isn't a fail.Keyring underneath.
        import keyring
        monkeypatch.setattr(keyring, "get_keyring", lambda: fake)
        monkeypatch.setattr(keyring, "get_password", lambda s, u: None)
        backend = _select_persistent_backend()
        assert isinstance(backend, KeyringBackend)

    def test_no_keyring_falls_back_to_json(self, monkeypatch, capsys):
        from bifrost.credentials import (
            JsonBackend,
            _select_persistent_backend,
            warn_if_keyring_fallback,
        )
        creds_mod._reset_persistent_backend_for_tests()
        import keyring
        import keyring.errors

        monkeypatch.setattr(keyring, "get_keyring", FakeFailKeyring)

        def fake_get_password(_s, _u):
            raise keyring.errors.NoKeyringError("no backend")

        monkeypatch.setattr(keyring, "get_password", fake_get_password)
        backend = _select_persistent_backend()
        assert isinstance(backend, JsonBackend)
        # Selection itself is silent — every `bifrost files` call would
        # otherwise pollute stderr / MCP tool_result text.
        assert capsys.readouterr().err == ""
        # Warning surfaces only when explicitly requested (e.g. from login).
        warn_if_keyring_fallback()
        captured = capsys.readouterr()
        assert "keyring" in captured.err.lower()
        assert "fallback" in captured.err.lower() or "falling back" in captured.err.lower()

    def test_partial_keyring_module_falls_back_to_json(self, monkeypatch):
        """A cached keyring.errors module may not be attached to its parent."""
        from bifrost.credentials import JsonBackend, _select_persistent_backend

        creds_mod._reset_persistent_backend_for_tests()
        import keyring
        import keyring.errors

        errors_module = keyring.errors

        def fake_get_password(_service, _username):
            raise errors_module.NoKeyringError("no backend")

        monkeypatch.setattr(keyring, "get_password", fake_get_password)
        monkeypatch.delattr(keyring, "errors")

        backend = _select_persistent_backend()

        assert isinstance(backend, JsonBackend)

    def test_keyring_import_error_falls_back_to_json(self, monkeypatch):
        from bifrost.credentials import JsonBackend, _select_persistent_backend
        creds_mod._reset_persistent_backend_for_tests()
        import sys
        # Force ImportError by removing the keyring module from cache and
        # blocking re-import.
        original = sys.modules.get("keyring")
        sys.modules["keyring"] = None  # type: ignore[assignment]
        try:
            backend = _select_persistent_backend()
        finally:
            if original is not None:
                sys.modules["keyring"] = original
            else:
                sys.modules.pop("keyring", None)
        assert isinstance(backend, JsonBackend)


# ---------- Legacy migration ----------

class TestLegacyMigration:
    @pytest.fixture
    def tmp_creds_path(self, tmp_path, monkeypatch):
        path = tmp_path / "credentials.json"
        monkeypatch.setattr(creds_mod, "get_credentials_path", lambda: path)
        creds_mod._reset_persistent_backend_for_tests()
        # Clear inherited env vars so EnvBackend doesn't shadow.
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        return path

    def _write_legacy(self, path):
        import json
        path.write_text(json.dumps({
            "api_url": "https://prod.example.com",
            "access_token": "legacy_at",
            "refresh_token": "legacy_rt",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }))

    def test_migrate_legacy_to_json_backend(self, tmp_creds_path, monkeypatch):
        import json
        # Force JSON backend
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        self._write_legacy(tmp_creds_path)

        result = creds_mod.get_credentials("https://prod.example.com")
        assert result is not None
        assert result["access_token"] == "legacy_at"

        # The file should now be in dict-of-URLs format
        data = json.loads(tmp_creds_path.read_text())
        assert list(data.keys()) == ["https://prod.example.com"]
        assert data["https://prod.example.com"]["access_token"] == "legacy_at"

    def test_migrate_legacy_to_keyring_backend(self, tmp_creds_path, monkeypatch):
        import json
        from bifrost.credentials import KEYRING_SERVICE, KeyringBackend
        fake = FakeKeyring()
        monkeypatch.setattr(
            creds_mod,
            "_select_persistent_backend",
            lambda: KeyringBackend(_keyring=fake),
        )
        creds_mod._reset_persistent_backend_for_tests()
        self._write_legacy(tmp_creds_path)

        result = creds_mod.get_credentials("https://prod.example.com")
        assert result is not None
        assert result["access_token"] == "legacy_at"

        # Keyring should have the entry
        raw = fake.get_password(KEYRING_SERVICE, "https://prod.example.com")
        assert raw is not None
        assert json.loads(raw)["access_token"] == "legacy_at"

        # JSON file is now an empty dict (marker that migration ran)
        data = json.loads(tmp_creds_path.read_text())
        assert data == {}

    def test_migrate_idempotent(self, tmp_creds_path, monkeypatch):
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        self._write_legacy(tmp_creds_path)

        # First call migrates
        creds_mod.get_credentials("https://prod.example.com")
        # Second call should still work, no exceptions
        result = creds_mod.get_credentials("https://prod.example.com")
        assert result["access_token"] == "legacy_at"

    def test_migration_failure_preserves_legacy_file(self, tmp_creds_path, monkeypatch):
        """If the new backend's save fails, the legacy file is untouched."""
        from bifrost.credentials import KeyringBackend

        class ExplodingKeyring:
            def get_password(self, *_a, **_kw):
                return None

            def set_password(self, *_a, **_kw):
                raise RuntimeError("disk full")

            def delete_password(self, *_a, **_kw):
                pass

        monkeypatch.setattr(
            creds_mod,
            "_select_persistent_backend",
            lambda: KeyringBackend(_keyring=ExplodingKeyring()),
        )
        creds_mod._reset_persistent_backend_for_tests()
        self._write_legacy(tmp_creds_path)
        original = tmp_creds_path.read_text()

        # Migration should fail silently and the legacy data should still be returned
        result = creds_mod.get_credentials("https://prod.example.com")
        assert result is not None
        assert result["access_token"] == "legacy_at"
        # File untouched on failure
        assert tmp_creds_path.read_text() == original

    def test_no_arg_get_resolves_url_from_legacy(self, tmp_creds_path, monkeypatch):
        """When no api_url is given AND no env var, falling back through legacy must work."""
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        self._write_legacy(tmp_creds_path)

        result = creds_mod.get_credentials()  # no args
        assert result is not None
        assert result["api_url"] == "https://prod.example.com"


class TestAuthTokenCommand:
    """`bifrost auth token` prints resolved url+token as JSON, so a scaffolded
    vite dev config can read the credential store (device-code login stores the
    token in keyring/JSON, NOT in a nearby .env) — R7-P2-f."""

    def test_token_emits_json_when_authenticated(self, monkeypatch, capsys):
        import json

        from bifrost.cli import handle_auth

        monkeypatch.setattr(
            "bifrost.cli.credentials.get_credentials",
            lambda api_url=None: {
                "api_url": "http://localhost:38421",
                "access_token": "at-123",
                "refresh_token": "rt-456",
                "expires_at": "2030-01-01T00:00:00+00:00",
            },
        )
        monkeypatch.setattr("bifrost.cli.credentials.is_token_expired", lambda api_url=None: False)
        rc = handle_auth(["token"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        # Only url + access token are emitted (no refresh token in dev surface).
        assert out == {
            "api_url": "http://localhost:38421",
            "access_token": "at-123",
        }

    def test_token_url_override_is_passed_through(self, monkeypatch, capsys):
        import json

        from bifrost.cli import handle_auth

        seen = {}

        def _fake(api_url=None):
            seen["url"] = api_url
            return {
                "api_url": api_url or "http://x",
                "access_token": "at",
                "refresh_token": "rt",
                "expires_at": None,
            }

        monkeypatch.setattr("bifrost.cli.credentials.get_credentials", _fake)
        monkeypatch.setattr("bifrost.cli.credentials.is_token_expired", lambda api_url=None: False)
        rc = handle_auth(["token", "--url", "http://localhost:9999"])
        assert rc == 0
        assert seen["url"] == "http://localhost:9999"
        assert json.loads(capsys.readouterr().out)["api_url"] == "http://localhost:9999"

    def test_token_refreshes_when_expired(self, monkeypatch, capsys):
        """Codex/audit: an expired token must be refreshed before emit, so a long
        `npm run dev` session isn't handed a stale token that 401s."""
        import json

        from bifrost.cli import handle_auth

        # First read: expired token. After a (faked) successful refresh: fresh one.
        state = {"refreshed": False}

        def _get(api_url=None):
            tok = "fresh-at" if state["refreshed"] else "stale-at"
            return {"api_url": "http://x", "access_token": tok, "refresh_token": "rt",
                    "expires_at": "2020-01-01T00:00:00+00:00"}

        def _expired(api_url=None):
            return not state["refreshed"]

        async def _refresh():
            state["refreshed"] = True
            return True

        monkeypatch.setattr("bifrost.cli.credentials.get_credentials", _get)
        monkeypatch.setattr("bifrost.cli.credentials.is_token_expired", _expired)
        monkeypatch.setattr("bifrost.client.refresh_tokens", _refresh)

        rc = handle_auth(["token"])
        assert rc == 0
        assert state["refreshed"] is True
        assert json.loads(capsys.readouterr().out)["access_token"] == "fresh-at"

    def test_token_warns_when_expired_and_refresh_fails(self, monkeypatch, capsys):
        """Refresh failure → warn on stderr but still emit (clear re-login signal,
        not silence)."""
        import json

        from bifrost.cli import handle_auth

        monkeypatch.setattr(
            "bifrost.cli.credentials.get_credentials",
            lambda api_url=None: {"api_url": "http://x", "access_token": "stale",
                                  "refresh_token": "rt", "expires_at": "2020-01-01T00:00:00+00:00"},
        )
        monkeypatch.setattr("bifrost.cli.credentials.is_token_expired", lambda api_url=None: True)

        async def _refresh_fail():
            return False

        monkeypatch.setattr("bifrost.client.refresh_tokens", _refresh_fail)

        rc = handle_auth(["token"])
        assert rc == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["access_token"] == "stale"  # still emitted
        assert "expired" in captured.err.lower()  # but warned

    def test_token_returns_error_when_not_authenticated(self, monkeypatch, capsys):
        from bifrost.cli import handle_auth

        monkeypatch.setattr(
            "bifrost.cli.credentials.get_credentials", lambda api_url=None: None
        )
        rc = handle_auth(["token"])
        assert rc == 1
        # Nothing token-shaped on stdout (vite must not parse a token from noise).
        assert "access_token" not in capsys.readouterr().out


class TestAuthConnectionCommands:
    def test_auth_list_marks_default_and_env_current(self, monkeypatch, capsys):
        from bifrost.cli import handle_auth

        monkeypatch.setattr(
            "bifrost.cli.credentials.list_credentials",
            lambda: ["http://first", "http://second"],
        )
        monkeypatch.setattr(
            "bifrost.cli.credentials.get_default_connection",
            lambda: "http://second",
        )
        monkeypatch.setenv("BIFROST_API_URL", "http://first")

        rc = handle_auth(["list"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "http://first  (current - from BIFROST_API_URL)" in out
        assert "http://second  (default)" in out

    def test_auth_use_sets_default_connection(self, monkeypatch, capsys):
        from bifrost.cli import handle_auth

        selected = {}
        monkeypatch.setattr(
            "bifrost.cli.credentials.list_credentials",
            lambda: ["http://first", "http://second"],
        )
        monkeypatch.setattr(
            "bifrost.cli.credentials.set_default_connection",
            lambda url: selected.setdefault("url", url),
        )

        rc = handle_auth(["use", "http://second"])

        assert rc == 0
        assert selected["url"] == "http://second"
        assert "Default connection set to http://second" in capsys.readouterr().out

    def test_auth_use_rejects_unknown_connection(self, monkeypatch, capsys):
        from bifrost.cli import handle_auth

        monkeypatch.setattr(
            "bifrost.cli.credentials.list_credentials",
            lambda: ["http://first"],
        )

        rc = handle_auth(["use", "http://missing"])

        assert rc == 1
        assert "No stored credentials for http://missing" in capsys.readouterr().err

    def test_auth_default_prints_default_and_current(self, monkeypatch, capsys):
        from bifrost.cli import handle_auth

        monkeypatch.setattr(
            "bifrost.cli.credentials.get_default_connection",
            lambda: "http://second",
        )
        monkeypatch.setattr(
            "bifrost.cli.credentials.resolve_current_connection",
            lambda: ("http://first", "BIFROST_API_URL"),
        )

        rc = handle_auth(["default"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "Default connection: http://second" in out
        assert "Current connection: http://first (from BIFROST_API_URL)" in out
