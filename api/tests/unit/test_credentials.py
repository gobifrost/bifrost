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


class TestDotenvAllowlist:
    def test_load_allowed_dotenv_ignores_cwd_without_opt_in(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("BIFROST_API_URL=https://from-cwd.example\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("BIFROST_LOAD_CWD_ENV", raising=False)
        monkeypatch.delenv("BIFROST_API_URL", raising=False)

        creds_mod.load_allowed_dotenv()

        assert "BIFROST_API_URL" not in os.environ

    def test_load_allowed_dotenv_imports_cwd_only_with_opt_in(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("BIFROST_API_URL=https://from-cwd.example\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("BIFROST_LOAD_CWD_ENV", "1")
        monkeypatch.delenv("BIFROST_API_URL", raising=False)

        creds_mod.load_allowed_dotenv()

        assert os.environ["BIFROST_API_URL"] == "https://from-cwd.example"

    def test_load_allowed_dotenv_only_imports_api_url(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "\n".join(
                [
                    "BIFROST_API_URL=https://from-dotenv.example",
                    "BIFROST_ACCESS_TOKEN=secret-access",
                    "BIFROST_REFRESH_TOKEN=secret-refresh",
                    "HTTPS_PROXY=http://proxy.example",
                    "SSL_CERT_FILE=C:/tmp/ca.pem",
                ]
            ),
            encoding="utf-8",
        )
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)

        creds_mod.load_allowed_dotenv(env_file)

        assert os.environ["BIFROST_API_URL"] == "https://from-dotenv.example"
        assert "BIFROST_ACCESS_TOKEN" not in os.environ
        assert "BIFROST_REFRESH_TOKEN" not in os.environ
        assert "HTTPS_PROXY" not in os.environ
        assert "SSL_CERT_FILE" not in os.environ


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
        creds_mod._reset_persistent_backend_for_tests()
        # Clear any inherited env vars so the env-var arm of resolution is off.
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        return path

    def test_no_arg_get_returns_first_url_when_two_present(self, tmp_creds_path):
        # Insertion order on dict iteration is the contract for JsonBackend.
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        result = creds_mod.get_credentials()
        assert result is not None
        assert result["api_url"] == "http://first"

    def test_no_arg_clear_targets_same_url_as_no_arg_get(self, tmp_creds_path):
        """clear_credentials() with no arg must clear what get_credentials() returns."""
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")

        before = creds_mod.get_credentials()
        assert before is not None and before["api_url"] == "http://first"

        creds_mod.clear_credentials()  # no arg

        # 'first' should be gone; 'second' must remain
        assert creds_mod.get_credentials("http://first") is None
        assert creds_mod.get_credentials("http://second") is not None

    def test_no_arg_get_prefers_env_var_over_first_stored(self, tmp_creds_path, monkeypatch):
        creds_mod.save_credentials("http://first", "at1", "rt1", "2030-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://second", "at2", "rt2", "2030-01-01T00:00:00+00:00")
        monkeypatch.setenv("BIFROST_API_URL", "http://second")
        result = creds_mod.get_credentials()
        assert result is not None
        assert result["api_url"] == "http://second"


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
        from bifrost.credentials import JsonBackend, _select_persistent_backend
        creds_mod._reset_persistent_backend_for_tests()
        import keyring
        import keyring.errors

        monkeypatch.setattr(keyring, "get_keyring", FakeFailKeyring)

        def fake_get_password(_s, _u):
            raise keyring.errors.NoKeyringError("no backend")

        monkeypatch.setattr(keyring, "get_password", fake_get_password)
        backend = _select_persistent_backend()
        assert isinstance(backend, JsonBackend)
        # Stderr warning so users know.
        captured = capsys.readouterr()
        assert "keyring" in captured.err.lower()
        assert "fallback" in captured.err.lower() or "falling back" in captured.err.lower()

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
