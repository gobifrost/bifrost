"""Device login must distinguish platform outages from rejected authorization."""

import asyncio

import httpx
import pytest
from click.testing import CliRunner

from bifrost import cli, client as client_mod
from bifrost.commands.tables import tables_group


def _install_device_server(monkeypatch, responder):
    real_client = httpx.AsyncClient
    requests = []

    def respond(request):
        if request.method == "GET":
            assert request.url.path == "/auth/me"
            return httpx.Response(200, json={"email": "user@example.test"})
        requests.append(request.url.path)
        # Exercise HTTPX's timeout contract, including both login phases.
        assert request.extensions["timeout"] == dict.fromkeys(
            ["connect", "read", "write", "pool"], 5.0
        )
        if request.url.path == "/auth/device/code":
            data = {
                "device_code": "secret-device",
                "user_code": "TEST-CODE",
                "verification_url": "/device",
                "expires_in": 300,
                "interval": 5,
            }
        else:
            assert request.url.path == "/auth/device/token"
            data = {"access_token": "secret-access", "refresh_token": "secret-refresh"}
        return responder(request, data)

    def make_client(**kwargs):
        return real_client(**kwargs, transport=httpx.MockTransport(respond))

    async def no_wait(_seconds):
        pass

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    monkeypatch.setattr(cli, "_check_cli_version", lambda: None)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(cli.webbrowser, "open", lambda _url: True)
    return requests


@pytest.mark.parametrize("command", ["login", "api", "tables"])
@pytest.mark.parametrize("phase", ["code", "token"])
@pytest.mark.parametrize("failure", [500, 503, 599, "connect", "connect-timeout", "read-timeout"])
def test_device_outage_exits_without_retry_or_credential_changes(
    isolated_credentials, monkeypatch, tmp_path, capsys, command, phase, failure
):
    api_url = "https://example.test"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BIFROST_API_URL", api_url)
    binding = tmp_path / ".env"
    binding.write_text("OTHER=keep\n")
    if command == "login":
        # An explicit re-login must also leave the old session intact.
        isolated_credentials.save_credentials(
            api_url, "secret-access", "secret-refresh", "2099-01-01T00:00:00+00:00"
        )
    before = isolated_credentials.get_credentials(api_url)

    def respond(request, data):
        if request.url.path.endswith(f"/{phase}"):
            errors = {
                "connect": httpx.ConnectError,
                "connect-timeout": httpx.ConnectTimeout,
                "read-timeout": httpx.ReadTimeout,
            }
            if failure in errors:
                raise errors[failure]("secret-device must not be printed", request=request)
            return httpx.Response(failure, json={"detail": "secret-device"})
        return httpx.Response(200, json=data)

    requests = _install_device_server(monkeypatch, respond)
    if command == "tables":
        result = CliRunner().invoke(tables_group, ["list"])
        code, error = result.exit_code, result.stderr
    else:
        args = (
            ["login", "--no-browser", "--url", api_url]
            if command == "login" else ["api", "GET", "/api/tables"]
        )
        code = cli.main(args)
        error = capsys.readouterr().err

    assert code == 3
    assert "unavailable" in error or "could not reach" in error
    assert "Retry once the platform is reachable" in error
    for forbidden in ["Not logged in", "Login failed", "Traceback", "secret-device", "secret-access", "secret-refresh"]:
        assert forbidden not in error
    assert requests == ["/auth/device/code"] + (
        ["/auth/device/token"] if phase == "token" else []
    )
    assert isolated_credentials.get_credentials(api_url) == before
    assert binding.read_text() == "OTHER=keep\n"


@pytest.mark.parametrize("module", [cli, client_mod])
@pytest.mark.parametrize("phase", ["code", "token"])
@pytest.mark.parametrize("status", [400, 401, 403])
def test_device_4xx_remains_a_login_failure(
    isolated_credentials, monkeypatch, tmp_path, module, phase, status, capsys
):
    def respond(request, data):
        if request.url.path.endswith(f"/{phase}"):
            return httpx.Response(status, json={"error": "access_denied"})
        return httpx.Response(200, json=data)

    monkeypatch.chdir(tmp_path)
    _install_device_server(monkeypatch, respond)
    if module is cli:
        assert cli.handle_login(["--url", "https://example.test", "--no-browser"]) == 1
    else:
        assert asyncio.run(module.login_flow("https://example.test", auto_open=False)) is False
    error = capsys.readouterr().err
    assert str(status) in error or "Authorization denied" in error
    assert "Retry" not in error


@pytest.mark.parametrize("module", [cli, client_mod])
@pytest.mark.parametrize("error", ["access_denied", "expired_token"])
def test_device_authorization_rejection_remains_a_login_failure(
    isolated_credentials, monkeypatch, module, error, capsys
):
    def respond(request, data):
        if request.url.path.endswith("/token"):
            data = {"error": error}
        return httpx.Response(200, json=data)

    _install_device_server(monkeypatch, respond)
    assert asyncio.run(module.login_flow("https://example.test", auto_open=False)) is False
    message = capsys.readouterr().err
    assert "denied" in message or "expired" in message
    assert "Retry" not in message


@pytest.mark.parametrize("module", [cli, client_mod])
def test_device_pending_then_success_stores_credentials(
    isolated_credentials, monkeypatch, module
):
    polls = 0

    def respond(request, data):
        nonlocal polls
        if request.url.path.endswith("/token"):
            polls += 1
            if polls == 1:
                data = {"error": "authorization_pending"}
        return httpx.Response(200, json=data)

    _install_device_server(monkeypatch, respond)
    assert asyncio.run(module.login_flow("https://example.test", auto_open=False)) is True
    assert polls == 2
    creds = isolated_credentials.get_credentials("https://example.test")
    assert creds["access_token"] == "secret-access"
    assert creds["refresh_token"] == "secret-refresh"
