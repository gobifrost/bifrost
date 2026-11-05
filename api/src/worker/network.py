"""Optional private TCP transport for physical workers, independent of job claims.

Each local connection gets one authenticated WebSocket. Bytes are never replayed
after a broken connection. Credit frames bound buffering in both directions;
database transactions, TLS, retries, and durable ownership remain their callers'.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import ctypes
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import struct
import tempfile
import shutil
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)
PROTOCOL = "bifrost-tcp.v1"
CHUNK = 64 * 1024
WINDOW = 4 * CHUNK
DATA, CREDIT, FIN = 0, 1, 2
MAX_CONNECTIONS = 64


@dataclass(frozen=True)
class Tunnel:
    hostname: str
    address: str
    port: int
    url: str


def configured_tunnels(raw: str | None) -> list[Tunnel]:
    if raw is None:
        return []
    entries = json.loads(raw)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 16:
        raise ValueError("Worker network requires between one and sixteen explicit tunnels")
    tunnels = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"hostname", "address", "port", "url"}:
            raise ValueError("Invalid worker network tunnel fields")
        tunnel = Tunnel(**entry)
        if not isinstance(tunnel.hostname, str) or len(tunnel.hostname) > 253 or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in tunnel.hostname.split(".")
        ):
            raise ValueError("Invalid worker network hostname")
        if not isinstance(tunnel.address, str) or not isinstance(tunnel.url, str):
            raise ValueError("Invalid worker network address or URL")
        address = ipaddress.ip_address(tunnel.address)
        if address.version != 4 or not address.is_loopback:
            raise ValueError("Worker network listeners must use IPv4 loopback addresses")
        if isinstance(tunnel.port, bool) or not isinstance(tunnel.port, int) or not 1 <= tunnel.port <= 65535:
            raise ValueError("Invalid worker network port")
        url = urlsplit(tunnel.url)
        # Accessing port validates malformed/out-of-range authorities eagerly.
        _ = url.port
        if url.scheme != "wss" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Worker network requires an authenticated WSS gateway without URL credentials")
        tunnels.append(tunnel)
    if len({(entry.address, entry.port) for entry in tunnels}) != len(tunnels) or len({entry.hostname.lower() for entry in tunnels}) != len(tunnels):
        raise ValueError("Duplicate worker network listener or hostname")
    return tunnels


def prepare_network_environment(
    tunnels: list[Tunnel], environment: dict[str, str],
    source_hosts: Path = Path("/etc/hosts"), directory: Path | None = None,
) -> dict[str, str]:
    """Use a private NSS hosts snapshot without modifying managed system files.

    The next exec loads the resolver for Python and native subprocesses alike.
    Original hostnames remain available to TLS certificate verification.
    """
    if not tunnels:
        raise ValueError("Private network bootstrap requires explicit tunnels")
    if environment.get("NSS_WRAPPER_HOSTS"):
        raise ValueError("Private hosts resolution is already configured")
    # The dynamic loader otherwise only warns and ignores an unavailable preload.
    # Fail before exec rather than silently resolving private hosts on public DNS.
    ctypes.CDLL("libnss_wrapper.so")
    existing = source_hosts.read_text()
    lines = []
    for tunnel in tunnels:
        matches = [line.split("#", 1)[0].split() for line in existing.splitlines()]
        addresses = {
            parts[0] for parts in matches
            if len(parts) > 1 and tunnel.hostname.lower() in {name.lower() for name in parts[1:]}
        }
        if addresses and addresses != {tunnel.address}:
            raise ValueError("Worker network hostname conflicts with existing host configuration")
        if not addresses:
            lines.append(f"{tunnel.address} {tunnel.hostname} # bifrost worker private transport\n")
    snapshot = Path(tempfile.mkdtemp(prefix="bifrost-worker-network-", dir=directory))
    try:
        hosts = snapshot / "hosts"
        hosts.write_text(existing + "\n" + "".join(lines))
        hosts.chmod(0o644)
        # The standard image entrypoint drops privileges after this bootstrap.
        # Its children may read the non-secret snapshot but cannot edit root's file.
        snapshot.chmod(0o755)
        return {
            **environment,
            "NSS_WRAPPER_HOSTS": str(hosts),
            "LD_PRELOAD": "libnss_wrapper.so" + (
                " " + environment["LD_PRELOAD"] if environment.get("LD_PRELOAD") else ""
            ),
        }
    except BaseException:
        shutil.rmtree(snapshot)
        raise


async def bridge_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, websocket) -> None:
    """One bounded, ordered byte stream with explicit credit and half-close."""
    credit = WINDOW
    outstanding = 0
    received = 0
    incoming: deque[bytearray] = deque()
    incoming_ready = asyncio.Event()
    credit_ready = asyncio.Condition()
    remote_fin = False
    local_fin = False

    async def receive() -> None:
        nonlocal credit, outstanding, received, remote_fin
        async for frame in websocket:
            if not isinstance(frame, bytes) or not frame:
                raise ValueError("Invalid private transport frame")
            kind, payload = frame[0], frame[1:]
            if kind == CREDIT and len(payload) == 4:
                amount = struct.unpack("!I", payload)[0]
                if not 0 < amount <= outstanding:
                    raise ValueError("Invalid private transport credit")
                async with credit_ready:
                    outstanding -= amount
                    credit += amount
                    credit_ready.notify_all()
            elif kind == DATA and 0 < len(payload) <= CHUNK and not remote_fin:
                received += len(payload)
                if received > WINDOW:
                    raise ValueError("Private transport receive window exceeded")
                # Coalesce tiny frames: byte credit must also bound Python
                # object overhead without rejecting a legitimate fragmented TCP stream.
                if incoming and len(incoming[-1]) + len(payload) <= CHUNK:
                    incoming[-1].extend(payload)
                else:
                    incoming.append(bytearray(payload))
                incoming_ready.set()
            elif kind == FIN and not payload and not remote_fin:
                remote_fin = True
                incoming_ready.set()
            else:
                raise ValueError("Invalid private transport frame")
        if not (remote_fin and local_fin and outstanding == 0):
            raise ConnectionError("Private transport closed before stream completion")

    async def send_tcp() -> None:
        nonlocal received
        while True:
            await incoming_ready.wait()
            while incoming:
                payload = incoming.popleft()
                writer.write(payload)
                await writer.drain()
                received -= len(payload)
                await websocket.send(bytes([CREDIT]) + struct.pack("!I", len(payload)))
            if remote_fin:
                writer.write_eof()
                await writer.drain()
                return
            incoming_ready.clear()

    async def read_tcp() -> None:
        nonlocal credit, outstanding, local_fin
        while True:
            async with credit_ready:
                await credit_ready.wait_for(lambda: credit > 0)
                size = min(CHUNK, credit)
            payload = await reader.read(size)
            if not payload:
                local_fin = True
                await websocket.send(bytes([FIN]))
                return
            async with credit_ready:
                credit -= len(payload)
                outstanding += len(payload)
            await websocket.send(bytes([DATA]) + payload)

    async with asyncio.TaskGroup() as group:
        receiving = group.create_task(receive())
        sending = group.create_task(read_tcp())
        writing = group.create_task(send_tcp())
        await asyncio.gather(sending, writing)
        async with credit_ready:
            await credit_ready.wait_for(lambda: outstanding == 0)
        receiving.cancel()
        await websocket.close()


@asynccontextmanager
async def worker_network():
    """Keep private connections available through the launcher's full drain."""
    tunnels = configured_tunnels(os.environ.get("BIFROST_WORKER_NETWORK"))
    if not tunnels:
        yield
        return
    token = os.environ.get("BIFROST_WORKER_NETWORK_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("A dedicated worker network bearer token is required")
    from websockets.asyncio.client import connect

    connections: set[asyncio.Task] = set()
    servers = []

    async def connected(reader, writer, tunnel):
        current = asyncio.current_task()
        assert current is not None
        if len(connections) >= MAX_CONNECTIONS:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            return
        connections.add(current)
        try:
            async with connect(
                tunnel.url, additional_headers={"Authorization": "Bearer " + token},
                subprotocols=[PROTOCOL], max_size=CHUNK + 1, max_queue=4,
                open_timeout=10, close_timeout=5, write_limit=2 * CHUNK,
            ) as websocket:
                if websocket.subprotocol != PROTOCOL:
                    raise ValueError("Private transport protocol was not accepted")
                await bridge_connection(reader, writer, websocket)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never log network credentials, protocol bytes, or upstream errors.
            logger.warning("Worker private connection ended (%s)", type(exc).__name__)
        finally:
            connections.discard(current)
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    try:
        for tunnel in tunnels:
            server = await asyncio.start_server(
                lambda reader, writer, selected=tunnel: connected(reader, writer, selected),
                tunnel.address, tunnel.port, limit=2 * CHUNK,
            )
            servers.append(server)
        yield
    finally:
        for server in servers:
            server.close()
        await asyncio.gather(*(server.wait_closed() for server in servers))
        for connection in tuple(connections):
            connection.cancel()
        await asyncio.gather(*connections, return_exceptions=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exec", nargs=argparse.REMAINDER, required=True)
    command = parser.parse_args().exec
    if not command:
        parser.error("--exec requires a command")
    environment = prepare_network_environment(
        configured_tunnels(os.environ.get("BIFROST_WORKER_NETWORK")), dict(os.environ),
    )
    os.execvpe(command[0], command, environment)
