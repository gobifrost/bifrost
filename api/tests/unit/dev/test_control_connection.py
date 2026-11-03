"""Service-auth configuration never changes launcher ownership or leaks secrets."""
import asyncio
import json
import logging
import os
import ssl
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.worker.remote.control_connection import (
    HEADERS_ENV,
    connect_control,
    load_control_headers,
)

URL = "wss://api.example/api/worker-control"
HEADERS = {"CF-Access-Client-Id": "synthetic-client-id", "CF-Access-Client-Secret": "synthetic-edge-secret"}


def test_unconfigured_headers_preserve_default_connection(monkeypatch):
    monkeypatch.delenv(HEADERS_ENV, raising=False)
    assert load_control_headers("ws://api:8000/api/worker-control") == {}
    with patch("websockets.asyncio.client.connect") as connect:
        assert connect_control(URL, {}) is connect.return_value
    connect.assert_called_once_with(URL, max_size=2**20, open_timeout=10)


def test_service_headers_preserve_values_and_case(monkeypatch):
    monkeypatch.setenv(HEADERS_ENV, json.dumps(HEADERS))
    assert load_control_headers(URL) == HEADERS


@pytest.mark.asyncio
async def test_invalid_configuration_refuses_before_identity_or_connection(monkeypatch):
    from src.worker.remote.launcher import _run_launcher

    monkeypatch.setenv(HEADERS_ENV, "not-json")
    monkeypatch.delenv("BIFROST_SECRET_KEY", raising=False)
    with (
        patch("src.worker.remote.launcher.connect_control") as connect,
        patch("src.worker.remote.launcher.load_launcher_identity") as identity,
    ):
        with pytest.raises(ValueError, match="JSON object"):
            await _run_launcher()
    connect.assert_not_called()
    identity.assert_not_called()


@pytest.mark.parametrize("raw", [
    "", "not-json", "null", "true", "1", '[["X-Auth", "secret"]]',
    '{"X-Auth": 1}', '{"X-Auth": true}', '{"X-Auth": null}',
    '{"X-Auth": {"token":"secret"}}', '{"X-Auth": ["secret"]}',
    '{"X-Auth":""}', '{"X-Auth":"   "}', '{"X-Auth":"secret\\r\\nInjected: yes"}',
    '{"X-Auth":"secret\\u0000"}', '{"X-Auth":"secret\\u007f"}',
    '{"X-Auth":"secret\\u00e9"}', '{"Bad Name":"secret"}',
    '{"X-Auth":"secret","x-auth":"another-secret"}',
    '{"X-Auth":"secret","X-Auth":"another-secret"}',
])
def test_bad_configuration_fails_without_echoing_values(monkeypatch, raw):
    monkeypatch.setenv(HEADERS_ENV, raw)
    with pytest.raises(ValueError) as error:
        load_control_headers(URL)
    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize("name", [
    "Host", "cOnNeCtIoN", "Upgrade", "Content-Length", "Transfer-Encoding",
    "Sec-WebSocket-Key", "sec-websocket-version", "Proxy-Authorization", "TE",
])
def test_transport_header_overrides_are_refused(monkeypatch, name):
    monkeypatch.setenv(HEADERS_ENV, json.dumps({name: "synthetic-edge-secret"}))
    with pytest.raises(ValueError, match="reserved"):
        load_control_headers(URL)


def test_configuration_size_and_count_are_bounded(monkeypatch):
    for headers in ({"X-Auth": "x" * 8192}, {f"X-{i}": "value" for i in range(17)}):
        monkeypatch.setenv(HEADERS_ENV, json.dumps(headers))
        with pytest.raises(ValueError):
            load_control_headers(URL)


def test_valid_configuration_boundaries_are_inclusive(monkeypatch):
    raw = '{"X":"' + 'x' * (8192 - len('{"X":""}')) + '"}'
    assert len(raw.encode("utf-8")) == 8192
    monkeypatch.setenv(HEADERS_ENV, raw)
    assert load_control_headers(URL)["X"] == 'x' * (8192 - len('{"X":""}'))
    headers = {f"X-{i}": "value" for i in range(16)}
    monkeypatch.setenv(HEADERS_ENV, json.dumps(headers))
    assert load_control_headers(URL) == headers


@pytest.mark.parametrize("url", [
    "ws://api.example/api/worker-control", "https://api.example/api/worker-control",
    "wss:///api/worker-control", "wss://user:password@api.example/api/worker-control",
    "wss://api.example/api/worker-control?token=value", "wss://api.example/api/worker-control#fragment",
])
def test_credentials_require_a_clean_wss_destination(monkeypatch, url):
    monkeypatch.setenv(HEADERS_ENV, json.dumps(HEADERS))
    with pytest.raises(ValueError):
        load_control_headers(url)


@pytest.mark.asyncio
async def test_authenticated_connect_disables_redirects_and_wire_logging():
    from websockets.datastructures import Headers
    from websockets.exceptions import InvalidStatus, SecurityError
    from websockets.http11 import Response

    connection = connect_control(URL, HEADERS)
    assert connection.additional_headers == HEADERS
    # These invoke redirect policy only; no connection, DNS lookup or worker starts.
    for destination in (URL, "wss://another.example/control", "ws://api.example/control"):
        error = InvalidStatus(Response(302, "Found", Headers({"Location": destination})))
        assert isinstance(connection.process_redirect(error), SecurityError)
    denied = InvalidStatus(Response(403, "Forbidden", Headers()))
    assert connection.process_redirect(denied) is denied


@pytest.fixture
def tls_context(tmp_path, monkeypatch):
    """Ephemeral loopback-only test certificate; never a deployment credential."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1")),
            ]), critical=False).sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    return context


@pytest.mark.asyncio
async def test_real_tls_handshakes_preserve_headers_across_connections_without_logging(tls_context, caplog):
    from websockets.asyncio.server import serve

    observed = []

    async def handler(socket):
        observed.append({name: socket.request.headers[name] for name in HEADERS})
        await socket.send("ready")

    # The fixture server also keeps synthetic credential headers out of wire logs.
    server_logger = logging.Logger("worker-control-test-server")
    server_logger.disabled = True
    caplog.set_level(logging.DEBUG)
    async with serve(handler, "127.0.0.1", 0, ssl=tls_context, logger=server_logger) as server:
        port = server.sockets[0].getsockname()[1]
        for _ in range(2):
            async with connect_control(f"wss://127.0.0.1:{port}/api/worker-control", HEADERS) as socket:
                assert await socket.recv() == "ready"
    assert observed == [HEADERS, HEADERS]
    assert all(value not in caplog.text for value in HEADERS.values())


@pytest.mark.asyncio
async def test_real_tls_redirect_is_refused_before_another_request(tls_context):
    from websockets.asyncio.server import serve
    from websockets.datastructures import Headers
    from websockets.exceptions import SecurityError
    from websockets.http11 import Response

    requests = []

    def redirect(_connection, request):
        requests.append(request.path)
        return Response(302, "Found", Headers({"Location": f"wss://127.0.0.1:{port}/redirected"}))

    async def unexpected_handler(_socket):
        pytest.fail("An HTTP redirect must not establish a control session")

    server_logger = logging.Logger("worker-control-redirect-test-server")
    server_logger.disabled = True
    async with serve(unexpected_handler, "127.0.0.1", 0, ssl=tls_context,
                     process_request=redirect, logger=server_logger) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(SecurityError, match="redirects are disabled"):
            async with connect_control(f"wss://127.0.0.1:{port}/api/worker-control", HEADERS):
                pytest.fail("An HTTP redirect must not establish a control session")
    assert requests == ["/api/worker-control"]


@pytest.mark.asyncio
async def test_transport_exception_does_not_log_header_values(monkeypatch, caplog):
    from src.worker.remote.launcher import _run_launcher

    monkeypatch.setenv(HEADERS_ENV, json.dumps(HEADERS))
    monkeypatch.setenv("BIFROST_API_URL", "https://api.example")
    monkeypatch.setenv("BIFROST_SECRET_KEY", "synthetic-instance-secret")
    attempts = []

    @asynccontextmanager
    async def refused(url, headers):
        attempts.append((url, headers))
        raise RuntimeError(json.dumps(headers))
        yield  # pragma: no cover - context manager that fails during handshake

    caplog.set_level(logging.DEBUG)
    with (
        patch("src.worker.remote.launcher.connect_control", new=refused),
        patch("src.worker.remote.launcher.load_launcher_identity", return_value=Mock()),
        patch("src.worker.remote.launcher.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
        patch.object(asyncio.get_running_loop(), "add_signal_handler"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await _run_launcher()
    assert attempts == [(URL, HEADERS)]
    assert "RuntimeError" in caplog.text
    assert all(value not in caplog.text for value in HEADERS.values())


@pytest.mark.parametrize("profile", ["workflow", "agent"])
def test_neither_template_profile_inherits_control_credentials(monkeypatch, profile):
    from src.services.execution.template_process import TemplateProcess

    monkeypatch.setenv(HEADERS_ENV, json.dumps(HEADERS))
    monkeypatch.setenv("BIFROST_SECRET_KEY", "synthetic-instance-secret")
    with patch("src.services.execution.template_process.subprocess.Popen", side_effect=OSError("synthetic spawn refusal")) as spawn:
        with pytest.raises(OSError, match="synthetic spawn refusal"):
            TemplateProcess(profile=profile).start()
    environment = spawn.call_args.kwargs["env"]
    assert HEADERS_ENV not in environment
    assert environment["BIFROST_SECRET_KEY"] == "synthetic-instance-secret"
    assert HEADERS_ENV in os.environ


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated", [True, False])
@pytest.mark.parametrize("error_kind", ["close", "exception"])
async def test_background_transport_logging_respects_control_authentication(
    monkeypatch, caplog, authenticated, error_kind,
):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close

    from src.worker.remote.launcher import Launcher, _run_launcher

    if authenticated:
        monkeypatch.setenv(HEADERS_ENV, json.dumps(HEADERS))
    else:
        monkeypatch.delenv(HEADERS_ENV, raising=False)
    monkeypatch.setenv("BIFROST_API_URL", "https://api.example")
    monkeypatch.setenv("BIFROST_SECRET_KEY", "synthetic-instance-secret")
    detail = json.dumps(HEADERS)
    error = (ConnectionClosedError(Close(1011, detail), None) if error_kind == "close"
             else RuntimeError(detail))
    # The initial inventory succeeds, then the background job's send fails.
    socket = Mock(send=AsyncMock(side_effect=[None, error]), close=AsyncMock())

    @asynccontextmanager
    async def connected(_url, _headers):
        yield socket

    async def hello(launcher, _requirements):
        launcher.session_generation = 1

    async def receive(launcher, *_args):
        process = Mock(wait=AsyncMock(return_value=0))
        launcher.jobs["route"] = process
        background = launcher.task(launcher.wait_job("route", process))
        await asyncio.gather(background, return_exceptions=True)
        assert "route" not in launcher.jobs
        assert len(launcher.pending_terminal) == 1
        launcher.retired.set()

    caplog.set_level(logging.ERROR)
    with (
        patch("src.worker.remote.launcher.connect_control", new=connected),
        patch("src.worker.remote.launcher.load_launcher_identity", return_value=Mock()),
        patch.object(Launcher, "hello", new=hello),
        patch.object(Launcher, "start", new=AsyncMock()),
        patch.object(Launcher, "stop", new=AsyncMock()),
        patch("src.worker.remote.launcher._receive_control_commands", new=receive),
        patch.object(asyncio.get_running_loop(), "add_signal_handler"),
    ):
        assert await _run_launcher() == 0

    assert "Launcher supervision failed" in caplog.text
    assert all((value not in caplog.text) == authenticated for value in HEADERS.values())
    socket.close.assert_awaited_once_with(code=1011, reason="Launcher supervision failed")
