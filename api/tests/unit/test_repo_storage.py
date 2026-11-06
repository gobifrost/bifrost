"""Source storage consumes only the neutral provider contract."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.storage.contracts import ContentProperties, ObjectInfo, ObjectPage, ObjectRead, ObjectSummary, ObjectVersion, StorageError, WriteResult
from src.services.repo_storage import RepoStorage
from src.services.solutions.storage import SolutionStorage


@pytest.fixture
def provider(monkeypatch):
    store = MagicMock()
    store.__aenter__.return_value = store
    store.__aexit__.return_value = None
    store.write = AsyncMock(return_value=WriteResult(ObjectVersion("opaque")))
    store.delete = AsyncMock()
    store.stat = AsyncMock(return_value=ObjectInfo("key", 11, ObjectVersion("opaque"), datetime.now(UTC), ContentProperties(), {}, None))
    store.list_page = AsyncMock(return_value=ObjectPage((), (), None))
    body = MagicMock()
    body.read = AsyncMock(return_value=b"hello world")
    body.close = AsyncMock()
    async def chunks():
        yield b"hello "
        yield b"world"
    body.iter_chunks = chunks
    @asynccontextmanager
    async def open_read(key):
        try:
            yield ObjectRead(await store.stat(key), body)
        finally:
            await body.close()
    store.open_read = MagicMock(side_effect=open_read)
    store.body = body
    monkeypatch.setattr("src.services.repo_storage.object_store", lambda settings: store)
    monkeypatch.setattr("src.services.solutions.storage.object_store", lambda settings: store)
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize("solution", [False, True])
async def test_source_reads_writes_and_deletes_keep_their_scope(provider, solution):
    source = SolutionStorage("install-a") if solution else RepoStorage()
    key = "_solutions/install-a/workflows/test.py" if solution else "_repo/workflows/test.py"
    written_hash = await source.write("/workflows/test.py", b"hello world")
    assert written_hash == hashlib.sha256(b"hello world").hexdigest()
    provider.write.assert_awaited_once_with(key, b"hello world")
    assert await source.read("/workflows/test.py") == b"hello world"
    provider.open_read.assert_called_once_with(key)
    provider.body.close.assert_awaited_once()
    await source.delete("/workflows/test.py")
    provider.delete.assert_awaited_once_with(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("solution", [False, True])
async def test_hash_streams_without_materializing_content(provider, solution):
    source = SolutionStorage("install-a") if solution else RepoStorage()
    assert await source.content_hash("modules/test.py") == hashlib.sha256(b"hello world").hexdigest()
    provider.body.read.assert_not_awaited()
    provider.body.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("solution", [False, True])
async def test_list_accumulates_neutral_pages_and_preserves_opaque_versions(provider, solution):
    source = SolutionStorage("install-a") if solution else RepoStorage()
    prefix = "_solutions/install-a/" if solution else "_repo/"
    now = datetime.now(UTC)
    provider.list_page.side_effect = [
        ObjectPage((ObjectSummary(prefix+"a.py", 1, ObjectVersion("a"), now),), (), "cursor"),
        ObjectPage((ObjectSummary(prefix+"b.py", 2, ObjectVersion("b"), now),), (), None),
    ]
    result = await source.list_with_metadata()
    assert list(result) == ["a.py", "b.py"]
    assert result["b.py"].version == "b" and result["b.py"].size == 2
    assert provider.list_page.await_args_list[1].kwargs["cursor"] == "cursor"


@pytest.mark.asyncio
@pytest.mark.parametrize("solution", [False, True])
async def test_listing_returns_relative_paths(provider, solution):
    source = SolutionStorage("install-a") if solution else RepoStorage()
    prefix = "_solutions/install-a/" if solution else "_repo/"
    provider.list_page.return_value = ObjectPage((ObjectSummary(prefix+"a.py", 1, ObjectVersion("v"), None),), (), None)
    assert await source.list() == ["a.py"]


@pytest.mark.asyncio
async def test_directory_filters_and_sorts_entries_without_backend_fields(provider):
    provider.list_page.return_value = ObjectPage(tuple(
        ObjectSummary("_repo/"+name, 1, ObjectVersion("v"), None)
        for name in ["b.py", "a.py", ".git/config", "__pycache__/a.pyc"]
    ), ("_repo/workflows/", "_repo/apps/", "_repo/.git/"), None)
    assert await RepoStorage().list_directory() == (["a.py", "b.py"], ["apps/", "workflows/"])
    provider.list_page.assert_awaited_once_with("_repo/", delimiter="/", cursor=None, limit=1000)


@pytest.mark.asyncio
async def test_directory_honors_custom_exclusions_and_prefix(provider):
    provider.list_page.return_value = ObjectPage((ObjectSummary("_repo/apps/a.py", 1, ObjectVersion("v"), None),), ("_repo/apps/components/",), None)
    assert await RepoStorage().list_directory("apps/", exclude_fn=lambda path: path.endswith("a.py")) == ([], ["apps/components/"])
    assert provider.list_page.await_args.args == ("_repo/apps/",)


@pytest.mark.asyncio
async def test_empty_directory_is_empty(provider):
    assert await RepoStorage().list_directory() == ([], [])


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["not_found", "permission_denied", "unavailable"])
async def test_exists_only_hides_confirmed_missing_objects(provider, kind):
    error = StorageError(kind, provider_code="fixture")
    provider.stat.side_effect = error
    if kind == "not_found":
        assert not await RepoStorage().exists("a.py")
    else:
        with pytest.raises(StorageError) as caught:
            await RepoStorage().exists("a.py")
        assert caught.value is error


@pytest.mark.asyncio
async def test_head_exposes_version_and_does_not_guess_a_missing_timestamp(provider):
    source = RepoStorage()
    info = await source.head("a.py")
    assert info.version == "opaque" and info.size == 11
    provider.stat.return_value = ObjectInfo("a.py", 11, ObjectVersion("v"), None, ContentProperties(), {}, None)
    with pytest.raises(StorageError):
        await source.head("a.py")


@pytest.mark.asyncio
@pytest.mark.parametrize("solution", [False, True])
async def test_prefix_exists_limits_listing_and_preserves_directory_boundary(provider, solution):
    source = SolutionStorage("install-a") if solution else RepoStorage()
    prefix = "_solutions/install-a/" if solution else "_repo/"
    assert not await source.prefix_exists("apps/")
    provider.list_page.assert_awaited_once_with(prefix+"apps/", delimiter=None, cursor=None, limit=1)


@pytest.mark.asyncio
async def test_prefix_exists_continues_after_an_empty_page(provider):
    provider.list_page.side_effect = [
        ObjectPage((), (), "next"),
        ObjectPage((ObjectSummary("_repo/apps/a.py", 1, ObjectVersion("v"), None),), (), None),
    ]
    assert await RepoStorage().prefix_exists("apps")


@pytest.mark.asyncio
@pytest.mark.parametrize("solution", [False, True])
@pytest.mark.parametrize("kind", ["not_found", "permission_denied"])
async def test_hash_only_hides_confirmed_absence(provider, solution, kind):
    source = SolutionStorage("install-a") if solution else RepoStorage()
    provider.stat.side_effect = StorageError(kind, provider_code="fixture")
    if kind == "not_found":
        assert await source.content_hash("a.py") is None
    else:
        with pytest.raises(StorageError):
            await source.content_hash("a.py")


def test_compute_hash():
    assert RepoStorage.compute_hash(b"hello") == hashlib.sha256(b"hello").hexdigest()
