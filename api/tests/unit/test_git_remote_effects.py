"""API Git I/O fencing and cancellation preserve checkout ownership."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.jobs.platform.base import PlatformJobCancelled
from src.services.github_sync import GitHubSyncService


def service(authorize):
    instance = GitHubSyncService.__new__(GitHubSyncService)
    instance._remote_authorization = authorize
    instance._token_provider = AsyncMock(return_value='isolated-fixture-credential')
    instance._git_env = {}
    instance.branch = "main"
    return instance


@pytest.mark.asyncio
@pytest.mark.parametrize('fence', ['before', 'after', 'current'])
async def test_remote_git_checks_attempt_and_runs_off_event_loop(fence):
    owner_thread = threading.get_ident()
    calls = []
    checks = 0
    async def authorize():
        nonlocal checks
        checks += 1
        if (fence == 'before' and checks == 1) or (fence == 'after' and checks == 2):
            raise PlatformJobCancelled
    instance = service(authorize)
    def remote(**kwargs):
        assert threading.get_ident() != owner_thread
        assert 'env' in kwargs
        calls.append('remote')
        return 'accepted'
    if fence == 'current':
        assert await instance._remote_git(remote) == 'accepted'
    else:
        with pytest.raises(PlatformJobCancelled):
            await instance._remote_git(remote)
    assert calls == ([] if fence == 'before' else ['remote'])


@pytest.mark.asyncio
async def test_cancel_joins_git_before_allowing_checkout_cleanup():
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    ended = threading.Event()
    def remote(**kwargs):
        loop.call_soon_threadsafe(started.set)
        release.wait()
        ended.set()
    instance = service(AsyncMock())
    task = asyncio.create_task(instance._remote_git(remote))
    try:
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)  # Deliver cancellation while the subprocess seam is held.
        assert not task.done() and not ended.is_set()
        task.cancel()  # A second shutdown signal must not detach Git I/O.
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ended.is_set()


@pytest.mark.asyncio
async def test_fetch_fence_failure_never_falls_through_to_push():
    instance = service(AsyncMock(side_effect=PlatformJobCancelled))
    instance._app_auth = False
    from unittest.mock import Mock
    fetch = Mock()
    push = Mock()
    repo = SimpleNamespace(head=SimpleNamespace(is_valid=lambda: True),
        remotes=SimpleNamespace(origin=SimpleNamespace(fetch=fetch, push=push)),
        git=SimpleNamespace(update_environment=Mock(), rev_list=Mock()))
    with pytest.raises(PlatformJobCancelled):
        await instance._do_push(None, repo)
    fetch.assert_not_called()
    push.assert_not_called()
