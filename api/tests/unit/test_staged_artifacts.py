"""Unit tests for StagedBuildArtifactStorage — exercised against the real
test-stack S3/SeaweedFS (no mocking), following the same convention as
``test_solution_source_artifact_lifecycle.py`` / ``SolutionSourceArtifactStorage``:
construct the storage class directly with a real ``build_job_id`` and call
its async methods, letting ``get_settings()`` resolve to the test stack's
S3 endpoint (``s3_configured`` is true there). ``asyncio_mode = auto`` in
pytest.ini means no explicit ``@pytest.mark.asyncio`` is required, but tests
are still plain ``async def`` functions.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from src.services.builder.staged_artifacts import (
    BuildArtifactIntegrityError,
    BuildOutputTooLarge,
    StagedBuildArtifactStorage,
)


async def _achunks(*parts: bytes):
    for p in parts:
        yield p


@pytest.fixture
def build_job_id() -> uuid.UUID:
    return uuid.uuid4()


async def test_write_and_open_input_round_trip(tmp_path: Path, build_job_id) -> None:
    storage = StagedBuildArtifactStorage(build_job_id)
    data = b"PK\x05\x06" + b"\x00" * 18  # minimal empty-zip EOCD
    local_zip = tmp_path / "input.zip"
    local_zip.write_bytes(data)

    sha = await storage.write_input(local_zip)
    assert sha == hashlib.sha256(data).hexdigest()

    reassembled = bytearray()
    async for chunk in storage.open_input_stream():
        reassembled.extend(chunk)
    assert bytes(reassembled) == data

    await storage.delete_job()


async def test_open_input_stream_raises_when_absent(build_job_id) -> None:
    storage = StagedBuildArtifactStorage(build_job_id)
    with pytest.raises(FileNotFoundError):
        async for _ in storage.open_input_stream():
            pass


async def test_write_output_streams_and_hashes(build_job_id) -> None:
    storage = StagedBuildArtifactStorage(build_job_id)
    app_id = uuid.uuid4()
    payload = b"console.log('hello world');\n" * 100

    sha, size = await storage.write_output(
        app_id, "assets/index.js", _achunks(payload[:50], payload[50:]), max_total_bytes=10_000_000
    )

    assert sha == hashlib.sha256(payload).hexdigest()
    assert size == len(payload)

    outputs = await storage.list_outputs(app_id)
    assert "assets/index.js" in outputs

    await storage.delete_job()


async def test_write_output_raises_when_cumulative_exceeds_cap(build_job_id) -> None:
    storage = StagedBuildArtifactStorage(build_job_id)
    app_id = uuid.uuid4()

    small_cap = 100
    first = b"a" * 60
    await storage.write_output(app_id, "a.txt", _achunks(first), max_total_bytes=small_cap)

    second = b"b" * 60
    with pytest.raises(BuildOutputTooLarge):
        await storage.write_output(app_id, "b.txt", _achunks(second), max_total_bytes=small_cap)

    await storage.delete_job()


async def test_write_output_rejects_unsafe_path(build_job_id) -> None:
    storage = StagedBuildArtifactStorage(build_job_id)
    with pytest.raises(ValueError, match="unsafe build output path"):
        await storage.write_output(
            uuid.uuid4(),
            "../escape.js",
            _achunks(b"nope"),
            max_total_bytes=100,
        )


def _dist_key(app_id: uuid.UUID, rel: str) -> str:
    """Mirrors SolutionAppBuilder._dist_key — AppStorageService's AppMode
    Literal doesn't include "dist" (that prefix is only ever written by the
    app builder), so list/cleanup here talk to the same raw S3 key shape."""
    return f"_apps/{app_id}/dist/{rel}"


async def test_copy_outputs_to_app_dist_lands_and_prunes_stale(build_job_id) -> None:
    from src.config import get_settings
    from src.services.repo_storage import _get_shared_session

    storage = StagedBuildArtifactStorage(build_job_id)
    app_id = uuid.uuid4()

    index_sha, index_size = await storage.write_output(
        app_id, "index.html", _achunks(b"<html></html>"), max_total_bytes=10_000_000
    )
    js_sha, js_size = await storage.write_output(
        app_id, "assets/app.js", _achunks(b"console.log(1)"), max_total_bytes=10_000_000
    )

    count = await storage.copy_outputs_to_app_dist(
        app_id,
        [
            {"path": "index.html", "sha256": index_sha, "size": index_size},
            {"path": "assets/app.js", "sha256": js_sha, "size": js_size},
        ],
    )
    assert count == 2

    settings = get_settings()
    bucket = settings.s3_bucket or ""
    session = _get_shared_session()

    async def _list_dist() -> set[str]:
        async with session.create_client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        ) as client:
            resp = await client.list_objects_v2(Bucket=bucket, Prefix=f"_apps/{app_id}/dist/")
            prefix_len = len(f"_apps/{app_id}/dist/")
            return {
                key[prefix_len:]
                for obj in resp.get("Contents", [])
                if (key := obj.get("Key")) is not None
            }

    dist_files = await _list_dist()
    assert dist_files == {"index.html", "assets/app.js"}

    # Second copy with a pruned manifest removes the stale key.
    # The exact-manifest invariant requires the staged set to match. A deploy
    # using only index.html is represented by a separate build job.
    second_storage = StagedBuildArtifactStorage(uuid.uuid4())
    second_sha, second_size = await second_storage.write_output(
        app_id,
        "index.html",
        _achunks(b"<html></html>"),
        max_total_bytes=10_000_000,
    )
    count2 = await second_storage.copy_outputs_to_app_dist(
        app_id,
        [{"path": "index.html", "sha256": second_sha, "size": second_size}],
    )
    assert count2 == 1
    dist_files_after = await _list_dist()
    assert dist_files_after == {"index.html"}

    await storage.delete_job()
    await second_storage.delete_job()
    # cleanup the dist/ output too, since it's a shared prefix outside build_artifacts
    async with session.create_client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    ) as client:
        for rel in await _list_dist():
            await client.delete_object(Bucket=bucket, Key=_dist_key(app_id, rel))


async def test_copy_rejects_manifest_hash_mismatch(build_job_id) -> None:
    storage = StagedBuildArtifactStorage(build_job_id)
    app_id = uuid.uuid4()
    _, size = await storage.write_output(
        app_id,
        "index.html",
        _achunks(b"<html></html>"),
        max_total_bytes=10_000_000,
    )

    with pytest.raises(BuildArtifactIntegrityError):
        await storage.copy_outputs_to_app_dist(
            app_id,
            [{"path": "index.html", "sha256": "0" * 64, "size": size}],
        )

    await storage.delete_job()


async def test_delete_job_empties_prefix(build_job_id) -> None:
    storage = StagedBuildArtifactStorage(build_job_id)
    app_id = uuid.uuid4()
    await storage.write_output(app_id, "a.txt", _achunks(b"hello"), max_total_bytes=10_000_000)

    deleted = await storage.delete_job()
    assert deleted >= 1

    async with storage._get_client() as client:  # noqa: SLF001 - verifying via list
        resp = await client.list_objects_v2(Bucket=storage._bucket, Prefix=storage.prefix)  # noqa: SLF001
        assert resp.get("Contents", []) == []
