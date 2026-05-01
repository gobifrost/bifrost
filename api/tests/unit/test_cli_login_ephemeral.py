"""Tests for `bifrost login` and `bifrost logout` flows.

Two login modes:
  * Browser device-code (default) — token stored in keychain (or JSON
    fallback). On success, login also writes BIFROST_API_URL=<url> to the
    CWD .env so subsequent CLI commands in this folder target this stack.
  * Password-grant (when --email and --password are passed) — URL +
    access + refresh tokens written to .env in CWD so subsequent CLI
    commands from that directory inherit the session. Tokens are NOT
    written to the OS keychain. Refuses MFA-enabled instances.
"""

from bifrost import cli


def _stub_post(json_payload: dict, status_code: int = 200):
    """Build an httpx.AsyncClient stand-in whose .post() returns the given payload."""
    class StubResponse:
        def __init__(self, code, payload):
            self.status_code = code
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    class StubClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, _url, json=None, data=None, headers=None):
            return StubResponse(status_code, json_payload)

    return StubClient


class TestPasswordLoginFlagParsing:
    def test_email_without_password_errors(self, capsys):
        rc = cli.handle_login(["--email", "x@y", "--url", "http://localhost:38421"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "--email" in err and "--password" in err

    def test_password_without_email_errors(self, capsys):
        rc = cli.handle_login(["--password", "p", "--url", "http://localhost:38421"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "--email" in err and "--password" in err

    def test_password_grant_without_url_or_env_errors(self, capsys, monkeypatch):
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        rc = cli.handle_login(["--email", "x@y", "--password", "p"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "URL" in err or "url" in err


class TestPasswordLoginSuccess:
    def test_writes_three_vars_to_env_and_warning_to_stderr(
        self, capsys, monkeypatch, tmp_path,
    ):
        stub = _stub_post({
            "access_token": "at_value",
            "refresh_token": "rt_value",
            "expires_in": 1800,
        })
        monkeypatch.setattr("httpx.AsyncClient", stub)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login([
            "--email", "dev@gobifrost.com",
            "--password", "password",
            "--url", "http://localhost:38421",
        ])
        assert rc == 0

        env_text = (tmp_path / ".env").read_text()
        assert "BIFROST_API_URL=http://localhost:38421" in env_text
        assert "BIFROST_ACCESS_TOKEN=at_value" in env_text
        assert "BIFROST_REFRESH_TOKEN=rt_value" in env_text

        # Warning to stderr
        captured = capsys.readouterr()
        assert "MFA" in captured.err
        assert "ephemeral" in captured.err.lower()

    def test_does_not_write_to_keychain(self, capsys, monkeypatch, tmp_path):
        """Password-grant must not touch the persistent (keychain/JSON) store.

        The session is per-directory via .env; persistence is intentionally
        excluded so a password-grant login on a dev machine doesn't end up
        in the long-lived auth list visible to `bifrost auth list`.
        """
        stub = _stub_post({
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 1800,
        })
        monkeypatch.setattr("httpx.AsyncClient", stub)
        monkeypatch.setattr(
            "bifrost.credentials.get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        monkeypatch.chdir(tmp_path)

        cli.handle_login([
            "--email", "dev@gobifrost.com",
            "--password", "password",
            "--url", "http://localhost:38421",
        ])

        # No keychain / JSON-fallback record was written.
        assert not (tmp_path / "credentials.json").exists()
        # .env IS written (that's the new contract).
        assert (tmp_path / ".env").exists()


class TestPasswordLoginMfaRefusal:
    def test_mfa_required_returns_exit_2(self, capsys, monkeypatch):
        stub = _stub_post({"mfa_required": True, "mfa_token": "mt", "expires_in": 300})
        monkeypatch.setattr("httpx.AsyncClient", stub)

        rc = cli.handle_login([
            "--email", "dev@gobifrost.com",
            "--password", "password",
            "--url", "http://localhost:38421",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "MFA" in err

    def test_mfa_setup_required_returns_exit_2(self, capsys, monkeypatch):
        stub = _stub_post({"mfa_setup_required": True, "mfa_token": "mt", "expires_in": 300})
        monkeypatch.setattr("httpx.AsyncClient", stub)

        rc = cli.handle_login([
            "--email", "dev@gobifrost.com",
            "--password", "password",
            "--url", "http://localhost:38421",
        ])
        assert rc == 2


class TestPasswordLoginUsesBifrostApiUrl:
    def test_falls_back_to_env_var_for_url(self, capsys, monkeypatch, tmp_path):
        stub = _stub_post({
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 1800,
        })
        monkeypatch.setattr("httpx.AsyncClient", stub)
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login([
            "--email", "dev@gobifrost.com",
            "--password", "password",
        ])
        assert rc == 0
        env_text = (tmp_path / ".env").read_text()
        assert "BIFROST_API_URL=http://localhost:38421" in env_text


class TestBrowserLoginWritesEnv:
    """Browser flow on success writes BIFROST_API_URL=<url> to CWD .env."""

    def test_writes_env_after_successful_browser_login(self, monkeypatch, tmp_path, capsys):
        async def fake_login(api_url=None, auto_open=True):
            return True

        monkeypatch.setattr(cli, "login_flow", fake_login)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login(["--url", "https://prod.example.com"])
        assert rc == 0

        env_text = (tmp_path / ".env").read_text()
        assert "BIFROST_API_URL=https://prod.example.com" in env_text

    def test_updates_existing_bifrost_api_url_line_in_place(self, monkeypatch, tmp_path):
        async def fake_login(api_url=None, auto_open=True):
            return True

        monkeypatch.setattr(cli, "login_flow", fake_login)
        monkeypatch.chdir(tmp_path)

        # Pre-existing .env with another var and a stale BIFROST_API_URL line
        (tmp_path / ".env").write_text(
            "OTHER_VAR=keep-me\nBIFROST_API_URL=http://stale.example.com\n"
        )

        cli.handle_login(["--url", "https://prod.example.com"])
        env_text = (tmp_path / ".env").read_text()

        assert "OTHER_VAR=keep-me" in env_text
        assert "BIFROST_API_URL=https://prod.example.com" in env_text
        assert "stale.example.com" not in env_text

    def test_appends_env_to_gitignore_if_absent(self, monkeypatch, tmp_path):
        async def fake_login(api_url=None, auto_open=True):
            return True

        monkeypatch.setattr(cli, "login_flow", fake_login)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("node_modules\n*.pyc\n")

        cli.handle_login(["--url", "https://prod.example.com"])

        gi = (tmp_path / ".gitignore").read_text()
        assert ".env" in gi.splitlines()

    def test_does_not_duplicate_env_in_gitignore(self, monkeypatch, tmp_path):
        async def fake_login(api_url=None, auto_open=True):
            return True

        monkeypatch.setattr(cli, "login_flow", fake_login)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("node_modules\n.env\n*.pyc\n")

        cli.handle_login(["--url", "https://prod.example.com"])

        gi = (tmp_path / ".gitignore").read_text()
        assert gi.count(".env") == 1

    def test_does_not_write_env_when_browser_login_fails(self, monkeypatch, tmp_path):
        async def fake_login(api_url=None, auto_open=True):
            return False

        monkeypatch.setattr(cli, "login_flow", fake_login)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login(["--url", "https://prod.example.com"])
        assert rc == 1
        assert not (tmp_path / ".env").exists()


class TestLogoutClearsKeychainAndPromptsEnv:
    def test_logout_clears_specific_url(self, monkeypatch, tmp_path):
        from bifrost import credentials as creds_mod
        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)

        creds_mod.save_credentials("https://prod.example.com", "at", "rt", "2099-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://localhost:38421", "at2", "rt2", "2099-01-01T00:00:00+00:00")

        rc = cli.handle_logout(["--url", "https://prod.example.com", "--no-prompt"])
        assert rc == 0
        assert creds_mod.get_credentials("https://prod.example.com") is None
        assert creds_mod.get_credentials("http://localhost:38421") is not None

    def test_logout_yes_removes_matching_env_line(self, monkeypatch, tmp_path):
        from bifrost import credentials as creds_mod
        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)

        creds_mod.save_credentials("https://prod.example.com", "at", "rt", "2099-01-01T00:00:00+00:00")
        (tmp_path / ".env").write_text(
            "OTHER_VAR=keep-me\nBIFROST_API_URL=https://prod.example.com\n"
        )

        rc = cli.handle_logout([
            "--url", "https://prod.example.com",
            "--yes",
        ])
        assert rc == 0
        env_text = (tmp_path / ".env").read_text()
        assert "OTHER_VAR=keep-me" in env_text
        assert "BIFROST_API_URL=" not in env_text

    def test_logout_no_prompt_leaves_env_alone(self, monkeypatch, tmp_path):
        from bifrost import credentials as creds_mod
        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.chdir(tmp_path)

        creds_mod.save_credentials("https://prod.example.com", "at", "rt", "2099-01-01T00:00:00+00:00")
        (tmp_path / ".env").write_text("BIFROST_API_URL=https://prod.example.com\n")

        rc = cli.handle_logout([
            "--url", "https://prod.example.com",
            "--no-prompt",
        ])
        assert rc == 0
        assert (tmp_path / ".env").read_text() == "BIFROST_API_URL=https://prod.example.com\n"


class TestAuthList:
    def test_auth_list_with_no_credentials(self, monkeypatch, tmp_path, capsys):
        from bifrost import credentials as creds_mod
        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()

        rc = cli.handle_auth(["list"])
        assert rc == 0
        assert "No stored credentials" in capsys.readouterr().out

    def test_auth_list_marks_current_via_env_var(self, monkeypatch, tmp_path, capsys):
        from bifrost import credentials as creds_mod
        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)

        creds_mod.save_credentials("https://prod.example.com", "at", "rt", "2099-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://localhost:38421", "at2", "rt2", "2099-01-01T00:00:00+00:00")
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")

        rc = cli.handle_auth(["list"])
        assert rc == 0
        listed_urls = []
        current_urls = []
        for raw_line in capsys.readouterr().out.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            url_part = stripped.split()[0]
            listed_urls.append(url_part)
            if "current" in raw_line:
                current_urls.append(url_part)
        assert sorted(listed_urls) == sorted([
            "https://prod.example.com",
            "http://localhost:38421",
        ])
        assert current_urls == ["http://localhost:38421"], (
            f"expected only the env-var URL flagged, got {current_urls}"
        )
