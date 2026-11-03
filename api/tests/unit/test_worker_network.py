"""Private transport preserves bytes and TLS hostnames without owning jobs."""
import asyncio
import json
import struct
import os
import subprocess
import sys
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from src.worker.network import (
    CREDIT, DATA, FIN, PROTOCOL, WINDOW, Tunnel, bridge_connection, prepare_network_environment,
    configured_tunnels,
)


def configuration(**changes):
    return json.dumps([{
        "hostname": "database.example.test", "address": "127.0.0.2",
        "port": 15432, "url": "wss://gateway.example.test/network/postgres",
        **changes,
    }])


@pytest.mark.parametrize("changes", [
    {"address": "0.0.0.0"}, {"address": "::1"}, {"port": True},
    {"port": 0}, {"hostname": "bad\nhost"}, {"hostname": "-bad.test"},
    {"url": "ws://gateway.test/"}, {"url": "wss://user:secret@gateway.test/"},
    {"url": "wss://gateway.test/?token=secret"},
    {"url": "wss://gateway.test:99999/"},
])
def test_rejects_unsafe_network_configuration(changes):
    with pytest.raises(ValueError):
        configured_tunnels(configuration(**changes))


def test_private_hosts_snapshot_preserves_read_only_system_file(tmp_path):
    assert configured_tunnels(None) == []
    tunnels = configured_tunnels(configuration())
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    hosts.chmod(0o444)
    environment = {"PATH": os.environ["PATH"], "LD_PRELOAD": "existing.so"}
    result = prepare_network_environment(tunnels, environment, hosts, tmp_path)
    private_hosts = Path(result["NSS_WRAPPER_HOSTS"])
    assert hosts.read_text() == "127.0.0.1 localhost\n"
    assert "127.0.0.2 database.example.test" in private_hosts.read_text()
    assert private_hosts.stat().st_mode & 0o777 == 0o644
    assert private_hosts.parent.stat().st_mode & 0o777 == 0o755
    assert result["LD_PRELOAD"] == "libnss_wrapper.so existing.so"
    assert "NSS_WRAPPER_HOSTS" not in environment
    with pytest.raises(ValueError, match="already configured"):
        prepare_network_environment(tunnels, result, hosts, tmp_path)
    hosts.chmod(0o644)
    hosts.write_text("127.0.0.2 database.example.test\n")
    with pytest.raises(ValueError, match="conflicts"):
        prepare_network_environment([Tunnel(tunnels[0].hostname, "127.0.0.3", 15432, tunnels[0].url)], environment, hosts, tmp_path)


def test_private_resolver_is_inherited_by_python_and_native_subprocesses(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    environment = prepare_network_environment(configured_tunnels(configuration()), dict(os.environ), hosts, tmp_path)
    script = (
        "import socket,subprocess; "
        "assert socket.getaddrinfo('database.example.test',5432)[0][4][0]=='127.0.0.2'; "
        "assert socket.getaddrinfo('localhost',80); "
        "assert b'127.0.0.2' in subprocess.check_output(['getent','hosts','database.example.test'])"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_bootstrap_fails_closed_when_resolver_library_is_missing(monkeypatch, tmp_path):
    def missing_library(name):
        raise OSError("Resolver library is unavailable")

    monkeypatch.setattr("src.worker.network.ctypes.CDLL", missing_library)
    with pytest.raises(OSError, match="unavailable"):
        prepare_network_environment(configured_tunnels(configuration()), {}, directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_bootstrap_executes_requested_command_with_private_resolution():
    environment = {**os.environ, "BIFROST_WORKER_NETWORK": configuration()}
    result = subprocess.run([
        sys.executable, "-m", "src.worker.network", "--exec", sys.executable, "-c",
        "import socket; assert socket.gethostbyname('database.example.test')=='127.0.0.2'",
    ], env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_private_resolution_preserves_certificate_hostname_validation(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "database.example.test")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("database.example.test")]), critical=False)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n127.0.0.2 wrong.example.test\n")
    environment = prepare_network_environment(configured_tunnels(configuration()), dict(os.environ), hosts, tmp_path)

    async def echo(reader, writer):
        try:
            assert await reader.readexactly(4) == b"ping"
            writer.write(b"pong")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    client = """
import socket,ssl,sys
context=ssl.create_default_context(cafile=sys.argv[1])
try:
    with context.wrap_socket(socket.create_connection((sys.argv[2],int(sys.argv[3]))),server_hostname=sys.argv[2]) as stream:
        stream.sendall(b'ping')
        assert stream.recv(4)==b'pong'
except ssl.SSLCertVerificationError:
    assert sys.argv[2]=='wrong.example.test'
else:
    assert sys.argv[2]=='database.example.test'
"""
    async with await asyncio.start_server(echo, "127.0.0.2", 0, ssl=context) as server:
        for hostname in ("database.example.test", "wrong.example.test"):
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", client, str(cert_path), hostname,
                str(server.sockets[0].getsockname()[1]), env=environment,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, error = await process.communicate()
            assert process.returncode == 0, error.decode()


@pytest.mark.parametrize("field,value", [
    ("hostname", "DATABASE.EXAMPLE.TEST"), ("url", "wss://other.test/"),
])
def test_duplicate_names_or_listeners_are_rejected(field, value):
    first = json.loads(configuration())[0]
    with pytest.raises(ValueError, match="Duplicate"):
        configured_tunnels(json.dumps([first, {**first, field: value}]))


@pytest.mark.asyncio
async def test_network_outlives_launcher_execution_and_drains_on_failure(monkeypatch):
    from src.worker import network
    from src.worker.remote import launcher

    events = []

    @asynccontextmanager
    async def transport():
        events.append("network-start")
        try:
            yield
        finally:
            events.append("network-stop")

    async def run(**kwargs):
        events.append("launcher-drained")
        raise RuntimeError("launcher failed")

    monkeypatch.setattr(network, "worker_network", transport)
    monkeypatch.setattr(launcher, "_run_launcher", AsyncMock(side_effect=run))
    with pytest.raises(RuntimeError, match="launcher failed"):
        await launcher.run_launcher()
    assert events == ["network-start", "launcher-drained", "network-stop"]


@pytest.mark.asyncio
async def test_duplex_stream_larger_than_window_preserves_bytes_and_half_close():
    payload = bytes(range(256)) * (WINDOW // 256 * 8)
    failures = []
    tasks = set()

    async def echo(reader, writer):
        try:
            while data := await reader.read(32768):
                writer.write(data)
                await writer.drain()
            writer.write_eof()
        finally:
            writer.close()
            await writer.wait_closed()

    async def track_bridge(reader, writer, websocket):
        current = asyncio.current_task()
        tasks.add(current)
        try:
            await bridge_connection(reader, writer, websocket)
        except Exception as exc:
            failures.append(exc)
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(current)

    async with await asyncio.start_server(echo, "127.0.0.1", 0) as upstream:
        upstream_port = upstream.sockets[0].getsockname()[1]

        async def gateway(websocket):
            reader, writer = await asyncio.open_connection("127.0.0.1", upstream_port)
            await track_bridge(reader, writer, websocket)

        async with serve(gateway, "127.0.0.1", 0, subprotocols=[PROTOCOL]) as gateway_server:
            gateway_port = gateway_server.sockets[0].getsockname()[1]

            async def local(reader, writer):
                async with connect(f"ws://127.0.0.1:{gateway_port}", subprotocols=[PROTOCOL]) as websocket:
                    await track_bridge(reader, writer, websocket)

            async with await asyncio.start_server(local, "127.0.0.1", 0) as local_server:
                reader, writer = await asyncio.open_connection("127.0.0.1", local_server.sockets[0].getsockname()[1])

                async def produce():
                    writer.write(payload)
                    await writer.drain()
                    writer.write_eof()

                try:
                    async with asyncio.timeout(10):
                        _, result = await asyncio.gather(produce(), reader.read())
                        assert result == payload
                        while tasks:
                            await asyncio.gather(*tuple(tasks))
                finally:
                    writer.close()
                    await writer.wait_closed()
    assert failures == []


@pytest.mark.asyncio
async def test_unsolicited_credit_terminates_connection():
    class InvalidPeer:
        def __aiter__(self):
            return self

        async def __anext__(self):
            return bytes([CREDIT]) + struct.pack("!I", 1)

    reader = asyncio.StreamReader()
    with pytest.raises(ExceptionGroup) as caught:
        await bridge_connection(reader, None, InvalidPeer())
    assert any(isinstance(exc, ValueError) for exc in caught.value.exceptions)


@pytest.mark.asyncio
async def test_tiny_frames_use_byte_credit_without_a_false_frame_limit():
    class FragmentedPeer:
        async def __aiter__(self):
            for _ in range(1024):
                yield bytes([DATA]) + b"x"
            yield bytes([FIN])
            await asyncio.Event().wait()

        async def send(self, frame):
            pass

        async def close(self):
            pass

    class Writer:
        def __init__(self):
            self.data = bytearray()
            self.eof = False

        def write(self, payload):
            self.data.extend(payload)

        async def drain(self):
            pass

        def write_eof(self):
            self.eof = True

    reader = asyncio.StreamReader()
    reader.feed_eof()
    writer = Writer()
    async with asyncio.timeout(5):
        await bridge_connection(reader, writer, FragmentedPeer())
    assert writer.data == b"x" * 1024
    assert writer.eof
