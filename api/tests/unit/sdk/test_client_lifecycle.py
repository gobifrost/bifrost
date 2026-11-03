"""SDK pools follow ordinary Python loop lifetimes, without workflow hooks."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bifrost.client import BifrostClient, _clear_engine_socket, _install_engine_socket


@pytest.fixture
def http_server():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_concurrent_loops_keep_their_usable_connection_pools(http_server):
    client = BifrostClient(http_server, "test-token")
    ready = threading.Barrier(2)
    pools = []

    async def request_twice():
        first = client._get_async_client()
        response = await client.get("/first")
        response.raise_for_status()
        connection = response.extensions["network_stream"]
        # Both live loops have made a request before either makes its second.
        await asyncio.to_thread(ready.wait, 5)
        second = client._get_async_client()
        response = await client.get("/second")
        response.raise_for_status()
        assert second is first
        assert response.extensions["network_stream"] is connection
        pools.append(first)

    with ThreadPoolExecutor(max_workers=2) as threads:
        futures = [threads.submit(asyncio.run, request_twice()) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert len(pools) == 2 and pools[0] is not pools[1]
    assert all(pool.is_closed for pool in pools)
    assert client._loop_clients == {}
    asyncio.run(client.close())


def test_repeated_asyncio_run_closes_network_and_engine_pools(http_server):
    client = BifrostClient(http_server, "test-token")
    pools = []
    _install_engine_socket("/tmp/sdk-lifecycle-not-connected.sock")

    async def request():
        response = await client.get("/one")
        response.raise_for_status()
        pools.extend([client._get_async_client(), client._get_engine_async_client()])

    try:
        for _ in range(4):
            asyncio.run(request())
            assert all(pool.is_closed for pool in pools)
            assert client._loop_clients == {}
        assert len({id(pool) for pool in pools}) == 8
    finally:
        _clear_engine_socket()
        asyncio.run(client.close())


@pytest.mark.asyncio
async def test_socket_replacement_preserves_active_clients_and_refreshes_all_headers():
    client = BifrostClient("https://example.invalid", "old-test-token")
    try:
        network = client._get_async_client()
        _install_engine_socket("/tmp/sdk-lifecycle-first.sock")
        first = client._get_engine_async_client()
        _install_engine_socket("/tmp/sdk-lifecycle-second.sock")
        second = client._get_engine_async_client()
        assert second is not first and not first.is_closed
        client.install_access_token("new-test-token")
        assert all(
            pool.headers["Authorization"] == "Bearer new-test-token"
            for pool in [network, first, second, client._sync_http]
        )
        await client.close()
        assert all(pool.is_closed for pool in [network, first, second])
        assert client._loop_clients == {}
        await client.close()
    finally:
        _clear_engine_socket()
        await client.close()


def test_stream_stays_usable_until_context_exit_and_closes_at_runner_shutdown(http_server):
    client = BifrostClient(http_server, "test-token")
    pools = []

    async def read_stream():
        async with client.stream("GET", "/stream") as response:
            pool = client._get_async_client()
            pools.append(pool)
            assert not pool.is_closed
            assert await response.aread() == b'{"ok":true}'
        assert not pool.is_closed

    asyncio.run(read_stream())
    assert pools[0].is_closed
    assert client._loop_clients == {}
    asyncio.run(client.close())


def test_fork_discards_parent_transport_locks_and_implicit_context():
    """A child can use a held client without inheriting parent locks/pools."""
    import contextvars
    import os
    import select
    import signal

    import bifrost.client as client_module

    client = BifrostClient("https://example.invalid", "parent-test-token")
    runner = asyncio.Runner()

    async def acquire():
        pool = client._get_async_client()
        await client._clients_for_loop().started.wait()
        return pool

    parent_pool = runner.run(acquire())
    parent_sync = client._sync_http
    locked = threading.Event()
    release = threading.Event()

    def hold_parent_lock():
        with client._clients_lock:
            locked.set()
            release.wait()

    thread = threading.Thread(target=hold_parent_lock)
    thread.start()
    assert locked.wait(5)
    local_token = client_module._local_dev_client.set(client)
    inherited_context = contextvars.copy_context()
    client_module._set_client(client)
    _install_engine_socket("/tmp/parent-only-sdk.sock")
    reader, writer = os.pipe()
    child = None
    try:
        child = os.fork()
        if child == 0:
            os.close(reader)
            try:
                assert client_module._injected_client is None
                assert client_module._local_dev_client.get() is None
                assert inherited_context.run(lambda: client_module._local_dev_client.get()) is None
                assert client_module.get_engine_socket_path() is None
                # Cancelling inherited cleanup tasks must not call HTTPX in
                # the child, even when the parent's client lock is held.
                runner.close()
                assert not parent_pool.is_closed
                client.install_access_token("child-test-token")
                assert client._sync_http is not parent_sync
                assert client._sync_http.headers["Authorization"] == "Bearer child-test-token"
                child_pool = asyncio.run(acquire())
                assert child_pool is not parent_pool and child_pool.is_closed
                asyncio.run(client.close())
                os.write(writer, b"ok")
            except BaseException:
                os.write(writer, b"failed")
                os._exit(1)
            os._exit(0)
        os.close(writer)
        writer = -1
        ready, _, _ = select.select([reader], [], [], 5)
        assert ready, "Forked SDK client blocked on inherited state"
        assert os.read(reader, 20) == b"ok"
        _, status = os.waitpid(child, 0)
        child = None
        assert os.waitstatus_to_exitcode(status) == 0
        assert not parent_pool.is_closed and not parent_sync.is_closed
    finally:
        if child is not None:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
        if writer != -1:
            os.close(writer)
        os.close(reader)
        release.set()
        thread.join()
        client_module._local_dev_client.reset(local_token)
        client_module._clear_client()
        _clear_engine_socket()
        runner.run(client.close())
        runner.close()
