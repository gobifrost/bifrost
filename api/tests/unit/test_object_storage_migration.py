from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import migrate_object_storage as migrate


class FakeStore:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.writes = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def list_page(self, prefix="", **kwargs):
        from src.core.storage.contracts import ObjectPage, ObjectSummary, ObjectVersion

        return ObjectPage(
            tuple(
                ObjectSummary(key=key, size=len(content), version=ObjectVersion(f'"{key}"'), modified_at=None)
                for key, (content, _, _) in sorted(self.objects.items())
                if key.startswith(prefix)
            ),
            (), None,
        )

    def open_read(self, key, **kwargs):
        from contextlib import asynccontextmanager

        from src.core.storage.contracts import StorageError

        store = self

        @asynccontextmanager
        async def read():
            if key not in store.objects:
                raise StorageError("not_found", provider_code="NoSuchKey")
            expected = kwargs.get("expected_version")
            content, version, properties = store.objects[key]
            if expected is not None and expected != version:
                raise StorageError("precondition_failed", provider_code="ConditionNotMet")
            body = SimpleNamespace(
                read=AsyncMock(return_value=content),
                iter_chunks=lambda chunk_size=8 * 1024 * 1024: _chunks(content),
                close=AsyncMock(),
            )
            yield SimpleNamespace(
                info=SimpleNamespace(key=key, version=version, size=len(content),
                                     properties=properties, metadata={}),
                body=body,
            )

        return read()

    async def stat(self, key):
        from src.core.storage.contracts import StorageError

        if key not in self.objects:
            raise StorageError("not_found", provider_code="NoSuchKey")

        content, version, properties = self.objects[key]
        return SimpleNamespace(key=key, version=version, size=len(content),
                               properties=properties, metadata={})

    async def write(self, key, content, *, options=None):
        from src.core.storage.contracts import ObjectVersion, WriteResult

        self.writes.append(key)
        version = ObjectVersion(f'"dest-{key}"')
        self.objects[key] = (bytes(content), version, options.properties if options else None)
        return WriteResult(version)


async def _chunks(content):
    yield content


@pytest.fixture
def stores(monkeypatch):
    from src.core.storage.contracts import ContentProperties

    source = FakeStore({
        "a": (b"aaa", '"v1"', ContentProperties(content_type="text/plain")),
        "b": (b"bb", '"v2"', ContentProperties()),
    })
    dest = FakeStore()
    monkeypatch.setattr(migrate.S3ObjectStore, "from_settings", lambda settings: source)
    monkeypatch.setattr(migrate.AzureObjectStore, "from_settings", lambda settings: dest)
    monkeypatch.setattr(migrate, "_source_settings", lambda: SimpleNamespace(s3_configured=True))
    monkeypatch.setattr(
        migrate, "_dest_settings", lambda: SimpleNamespace(azure_blob_configured=True),
    )
    return source, dest


@pytest.mark.asyncio
async def test_migrate_copies_everything_once(stores):
    assert await migrate.migrate(prefix="", dry_run=False, verify_digests=False, allow_nonempty=False) == 0
    _, dest = stores
    assert sorted(dest.objects) == ["a", "b"]
    assert dest.objects["a"][0] == b"aaa"


@pytest.mark.asyncio
async def test_migrate_resumes_by_skipping_identical_keys(stores):
    source, dest = stores
    assert await migrate.migrate(prefix="", dry_run=False, verify_digests=False, allow_nonempty=False) == 0
    writes = len(dest.writes)
    assert await migrate.migrate(prefix="", dry_run=False, verify_digests=False, allow_nonempty=True) == 0
    assert len(dest.writes) == writes


@pytest.mark.asyncio
async def test_migrate_refuses_a_dirty_destination_without_resume(stores, monkeypatch):
    _, dest = stores
    dest.objects["stale"] = (b"x", '"v"', None)
    assert await migrate.migrate(prefix="", dry_run=False, verify_digests=False, allow_nonempty=False) == 2


@pytest.mark.asyncio
async def test_migrate_dry_run_writes_nothing(stores, capsys):
    assert await migrate.migrate(prefix="", dry_run=True, verify_digests=False, allow_nonempty=False) == 0
    _, dest = stores
    assert dest.writes == []
    assert "would copy a" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_migrate_reports_unreadable_keys(stores):
    from src.core.storage.contracts import StorageError

    source, _ = stores
    # Simulate the source losing a key between listing and copy.
    original_open = source.open_read

    def flaky_open(key, **kwargs):
        if key == "a":
            raise StorageError("not_found", provider_code="NoSuchKey")
        return original_open(key, **kwargs)

    source.open_read = flaky_open
    assert await migrate.migrate(prefix="", dry_run=False, verify_digests=False, allow_nonempty=False) == 1


def test_migrate_requires_explicit_acknowledgment(monkeypatch, capsys):
    import sys

    monkeypatch.setattr(sys, "argv", ["migrate_object_storage.py"])
    monkeypatch.delenv("BIFROST_MIGRATE_OBJECT_STORAGE_ACK", raising=False)
    assert migrate.main() == 2
    assert "ACK" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("destination_bytes,expected_writes", [(b"aaa", []), (b"zzz", ["a"])])
async def test_digest_verified_resume_compares_existing_content(stores, destination_bytes, expected_writes):
    source, dest = stores
    content, _, properties = source.objects["a"]
    dest.objects["a"] = (destination_bytes, '"dest-v1"', properties)
    assert await migrate._copy_key(source, dest, "a", verify_digests=True) == (
        ("skipped", None) if destination_bytes == content else ("copied", None)
    )
    assert dest.objects["a"][0] == content
    assert dest.writes == expected_writes


@pytest.mark.asyncio
async def test_digest_verified_resume_refuses_a_changed_destination(stores):
    source, dest = stores
    _, _, properties = source.objects["a"]
    dest.objects["a"] = (b"aaa", '"dest-v1"', properties)
    original_stat = dest.stat

    async def stat_then_change(key):
        info = await original_stat(key)
        dest.objects[key] = (b"zzz", '"dest-v2"', properties)
        return info

    dest.stat = stat_then_change
    outcome, detail = await migrate._copy_key(source, dest, "a", verify_digests=True)
    assert outcome == "failed"
    assert detail is not None and "precondition_failed" in detail
    assert dest.writes == []
