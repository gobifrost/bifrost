"""files.move end to end: scheduler job, real object storage, policy-following access."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import UUID

import pytest
from sqlalchemy import select, update

from shared.file_paths import resolve_s3_key
from src.config import get_settings
from src.models.orm.file_metadata import FileMetadata
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution
from src.services.file_storage.move import (
    MARK_ETAG,
    MARK_ROW,
    MARK_SOURCE,
    FileMover,
    MoveRequest,
    MoveTarget,
    source_marker,
)
from src.services.file_storage.s3_client import S3StorageClient
from tests.e2e.conftest import execute_workflow_sync, write_and_register
from tests.e2e.file_policy_helpers import grant_file_policy

pytestmark = pytest.mark.e2e

LOCATION = "docs"
ADMIN_RULE = {"name": "admin", "actions": ["read", "write", "delete", "list"], "when": {"user": "is_platform_admin"}}
READ_ALL_RULE = {"name": "read_all", "actions": ["read"], "when": None}


async def _no_rename_expected(path: str) -> bool:
    raise AssertionError(f"unexpected rename to {path}")


def _put_policy(e2e_client, headers, *, scope: str, prefix: str, rules: list[dict]) -> None:
    response = e2e_client.put(
        f"/api/files/policies/{quote(prefix or '/', safe='')}",
        headers=headers,
        params={"location": LOCATION, "scope": scope},
        json={"policies": {"policies": rules}},
    )
    assert response.status_code in (200, 201, 204), response.text


@pytest.fixture
async def solution(e2e_client, platform_admin, org1, db_session):
    slug = f"move-{uuid.uuid4().hex[:8]}"
    created = e2e_client.post(
        "/api/solutions", headers=platform_admin.headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": org1["id"]},
    )
    assert created.status_code in (200, 201), created.text
    body = created.json()
    db_session.add(SolutionFileLocation(solution_id=UUID(body["id"]), location=LOCATION))
    await db_session.commit()
    _put_policy(e2e_client, platform_admin.headers, scope=body["organization_id"], prefix="", rules=[ADMIN_RULE])
    _put_policy(
        e2e_client, platform_admin.headers, scope=body["organization_id"],
        prefix="campus-b", rules=[ADMIN_RULE, READ_ALL_RULE],
    )
    return body


def _write(e2e_client, headers, sid: str, path: str, content: str) -> None:
    response = e2e_client.post(
        f"/api/files/write?solution={sid}", headers=headers,
        json={"location": LOCATION, "path": path, "content": content, "mode": "cloud"},
    )
    assert response.status_code == 204, response.text


def _read(e2e_client, headers, sid: str, path: str):
    return e2e_client.post(
        f"/api/files/read?solution={sid}", headers=headers,
        json={"location": LOCATION, "path": path, "mode": "cloud", "binary": False},
    )


def _poll(e2e_client, headers, job_id: str) -> dict:
    for _ in range(240):
        response = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.25)
    raise AssertionError(f"move job {job_id} did not finish")


def _move_many(e2e_client, headers, sid: str, items: list[tuple[str, str]], on_conflict: str = "error") -> dict:
    response = e2e_client.post(
        f"/api/files/move-many?solution={sid}", headers=headers,
        json={"items": [{"source": s, "destination": d} for s, d in items],
              "location": LOCATION, "on_conflict": on_conflict},
    )
    assert response.status_code == 202, response.text
    assert response.headers["Location"] == f"/api/platform-jobs/{response.json()['job_id']}"
    return _poll(e2e_client, headers, response.json()["job_id"])


async def _row(db_session, sid: str, path: str) -> FileMetadata | None:
    db_session.expire_all()
    return (
        await db_session.execute(
            select(FileMetadata).where(
                FileMetadata.solution_id == UUID(sid),
                FileMetadata.location == LOCATION,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()


async def test_move_many_moves_files_and_access_follows(
    e2e_client, platform_admin, org1_user, solution, db_session
) -> None:
    sid, admin = solution["id"], platform_admin.headers
    _write(e2e_client, admin, sid, "campus-a/b1/plan.pdf", "plan")
    _write(e2e_client, admin, sid, "campus-a/b1/spec.pdf", "spec")
    before = await _row(db_session, sid, "campus-a/b1/plan.pdf")
    assert _read(e2e_client, org1_user.headers, sid, "campus-a/b1/plan.pdf").status_code == 403

    job = _move_many(
        e2e_client, admin, sid,
        [("campus-a/b1/plan.pdf", "campus-b/b1/plan.pdf"), ("campus-a/b1/spec.pdf", "campus-b/b1/spec.pdf")],
    )

    assert job["status"] == "succeeded", job
    assert [r["status_code"] for r in job["result"]["results"]] == [200, 200]
    assert _read(e2e_client, org1_user.headers, sid, "campus-b/b1/plan.pdf").json()["content"] == "plan"
    assert _read(e2e_client, admin, sid, "campus-a/b1/plan.pdf").status_code == 404
    after = await _row(db_session, sid, "campus-b/b1/plan.pdf")
    assert after is not None and after.id == before.id
    assert await _row(db_session, sid, "campus-a/b1/plan.pdf") is None



async def test_move_into_a_sealed_install_is_refused_like_a_read(
    e2e_client, platform_admin, solution, db_session
) -> None:
    sid, admin = solution["id"], platform_admin.headers
    _write(e2e_client, admin, sid, "campus-a/sealed.pdf", "sealed")
    await db_session.execute(
        update(Solution).where(Solution.id == UUID(sid)).values(allow_inbound_access=False)
    )
    await db_session.commit()
    assert _read(e2e_client, admin, sid, "campus-a/sealed.pdf").status_code == 403

    response = e2e_client.post(
        f"/api/files/move-many?solution={sid}", headers=admin,
        json={"items": [{"source": "campus-a/sealed.pdf", "destination": "campus-b/sealed.pdf"}],
              "location": LOCATION},
    )

    assert response.status_code == 403, response.text
    db_session.expire_all()
    jobs = (
        await db_session.execute(select(PlatformJob).where(PlatformJob.job_type == "files.move"))
    ).scalars().all()
    assert not any(job.payload.get("solution_id") == sid for job in jobs)
    assert (await _row(db_session, sid, "campus-a/sealed.pdf")) is not None


async def test_rename_is_refused_where_the_generated_name_denies_writes(
    e2e_client, platform_admin, org1_user, solution, db_session
) -> None:
    sid, admin, user = solution["id"], platform_admin.headers, org1_user.headers
    org = solution["organization_id"]
    everything = {"name": "all", "actions": ["read", "write", "delete", "list"], "when": None}
    _put_policy(e2e_client, admin, scope=org, prefix="renames", rules=[everything])
    _put_policy(e2e_client, admin, scope=org, prefix="renames/report (1).pdf", rules=[READ_ALL_RULE])
    _write(e2e_client, admin, sid, "renames/in/report.pdf", "mine")
    _write(e2e_client, admin, sid, "renames/report.pdf", "occupant")
    denied = e2e_client.post(
        f"/api/files/write?solution={sid}", headers=user,
        json={"location": LOCATION, "path": "renames/report (1).pdf", "content": "x", "mode": "cloud"},
    )
    assert denied.status_code == 403, denied.text

    job = _move_many(e2e_client, user, sid, [("renames/in/report.pdf", "renames/report.pdf")], "rename")

    [result] = job["result"]["results"]
    assert (result["status_code"], result["error"]) == (403, "forbidden"), job
    assert _read(e2e_client, user, sid, "renames/in/report.pdf").json()["content"] == "mine"
    assert await _row(db_session, sid, "renames/report (1).pdf") is None

@pytest.mark.parametrize("rename", [False, True])
async def test_retry_adopts_an_interrupted_move(
    e2e_client, platform_admin, solution, db_session, rename
) -> None:
    sid, admin = solution["id"], platform_admin.headers
    _write(e2e_client, admin, sid, "campus-a/r.pdf", "report")
    destination = "campus-b/r.pdf"
    landed_at = destination
    if rename:
        _write(e2e_client, admin, sid, destination, "someone else")
        landed_at = "campus-b/r (1).pdf"
    row = await _row(db_session, sid, "campus-a/r.pdf")
    storage = S3StorageClient(get_settings())
    source_key = resolve_s3_key(LOCATION, sid, "campus-a/r.pdf")
    head = await storage.head_object(source_key)
    # Simulate a run that copied and deleted the source, then died before commit.
    await storage.copy_object(
        source_key, resolve_s3_key(LOCATION, sid, landed_at), source=head,
        metadata={MARK_SOURCE: source_marker(source_key), MARK_ROW: str(row.id), MARK_ETAG: head.etag},
    )
    await storage.delete_object_strict(source_key)

    job = _move_many(e2e_client, admin, sid, [("campus-a/r.pdf", destination)], "rename" if rename else "error")

    [result] = job["result"]["results"]
    assert (result["status_code"], result["adopted"]) == (200, True)
    assert (result["path"] or destination) == landed_at
    assert (await _row(db_session, sid, landed_at)).id == row.id



@pytest.mark.parametrize("source_deleted", [False, True], ids=["resume", "adopt"])
async def test_recovering_a_renamed_copy_needs_the_retrying_users_write(
    e2e_client, platform_admin, org1_user, solution, db_session, source_deleted
) -> None:
    """An admin's interrupted copy at a name the retrying user may not write."""
    sid, admin, user = solution["id"], platform_admin.headers, org1_user.headers
    org = solution["organization_id"]
    everything = {"name": "all", "actions": ["read", "write", "delete", "list"], "when": None}
    _put_policy(e2e_client, admin, scope=org, prefix="recover", rules=[everything])
    _put_policy(e2e_client, admin, scope=org, prefix="recover/report (1).pdf", rules=[READ_ALL_RULE])
    _write(e2e_client, admin, sid, "recover/in/report.pdf", "mine")
    _write(e2e_client, admin, sid, "recover/report.pdf", "occupant")
    row = await _row(db_session, sid, "recover/in/report.pdf")
    storage = S3StorageClient(get_settings())
    source_key = resolve_s3_key(LOCATION, sid, "recover/in/report.pdf")
    head = await storage.head_object(source_key)
    candidate_key = resolve_s3_key(LOCATION, sid, "recover/report (1).pdf")
    await storage.copy_object(
        source_key, candidate_key, source=head,
        metadata={MARK_SOURCE: source_marker(source_key), MARK_ROW: str(row.id), MARK_ETAG: head.etag},
    )
    if source_deleted:
        await storage.delete_object_strict(source_key)

    job = _move_many(e2e_client, user, sid, [("recover/in/report.pdf", "recover/report.pdf")], "rename")

    [result] = job["result"]["results"]
    assert (result["status_code"], result["error"]) == (403, "forbidden"), job
    assert (await _row(db_session, sid, "recover/in/report.pdf")).id == row.id
    assert await _row(db_session, sid, "recover/report (1).pdf") is None
    assert await storage.head_object(candidate_key) is not None
    assert (await storage.head_object(source_key) is None) == source_deleted

def _record_s3_calls(storage: S3StorageClient) -> list[str]:
    """Record the name of every S3 client method ``storage`` calls from now on."""
    issued: list[str] = []
    open_client = storage.get_client

    class Recorder:
        def __init__(self, client) -> None:
            self._client = client

        def __getattr__(self, name: str):
            attribute = getattr(self._client, name)
            if callable(attribute):
                issued.append(name)
            return attribute

    @asynccontextmanager
    async def recording_client():
        async with open_client() as client:
            yield Recorder(client)

    storage.get_client = recording_client  # type: ignore[method-assign]
    return issued


async def test_multipart_copy_against_object_storage(
    platform_admin, solution, async_session_factory
) -> None:
    sid = solution["id"]
    storage = S3StorageClient(get_settings())
    source = "campus-a/Floor Plan – Rev 2 (final).bin"
    payload = bytes((i * 31) % 256 for i in range(11 * 1024 * 1024))

    async def chunks():
        yield payload

    await storage.put_object_from_chunks(resolve_s3_key(LOCATION, sid, source), chunks())
    mover = FileMover(
        storage,
        MoveTarget(
            location=LOCATION, scope=sid, solution_id=UUID(sid),
            organization_id=UUID(solution["organization_id"]), requested_by=platform_admin.user_id,
        ),
        authorize_rename=_no_rename_expected,
        open_session=async_session_factory,
        multipart_threshold=6 * 1024 * 1024,
        part_size=5 * 1024 * 1024,
    )

    source_head = await storage.head_object(resolve_s3_key(LOCATION, sid, source))
    issued = _record_s3_calls(storage)

    outcome = await mover.move(MoveRequest(source, "campus-b/Floor Plan – Rev 2 (final).bin"), "error")

    assert outcome.status_code == 200, outcome
    # The multipart branch ran (11 MiB in 5 MiB parts), not a single CopyObject.
    assert "copy_object" not in issued
    assert issued.count("create_multipart_upload") == 1
    assert issued.count("upload_part_copy") == 3
    assert issued.count("complete_multipart_upload") == 1
    destination_key = resolve_s3_key(LOCATION, sid, "campus-b/Floor Plan – Rev 2 (final).bin")
    landed = await storage.head_object(destination_key)
    # SeaweedFS gives a multipart object an "<md5>-<parts>" ETag. The source's
    # own chunked upload is multipart too ("-2" in 8 MiB parts), so only "-3"
    # proves the copy was assembled from this move's three part copies.
    assert source_head.etag.endswith('-2"'), source_head.etag
    assert landed.etag.endswith('-3"'), landed.etag
    moved = await storage.read_uploaded_file(destination_key)
    assert moved == payload
    assert await storage.head_object(resolve_s3_key(LOCATION, sid, source)) is None


@pytest.fixture
def _org_location_policy(e2e_client, platform_admin, org1):
    grant_file_policy(e2e_client, platform_admin.headers, location="move-e2e", scope=org1["id"], allow_all=True)


@pytest.mark.timeout(300)
async def test_workflow_moves_and_enqueue_fits_the_sdk_budget(
    e2e_client, platform_admin, org1, org1_user, _org_location_policy
) -> None:
    tag = uuid.uuid4().hex[:8]
    prefix = f"e2e-move-{tag}"
    write = e2e_client.post(
        "/api/files/write", headers=platform_admin.headers,
        json={"location": "move-e2e", "path": f"{prefix}/one.txt", "content": "one", "mode": "cloud", "scope": org1["id"]},
    )
    assert write.status_code == 204, write.text
    name = f"e2e_files_move_{tag}"
    content = f'''"""files.move e2e workflow."""
import time
from bifrost import workflow, files
from bifrost.client import get_client

@workflow(name="{name}", description="files.move e2e")
async def {name}():
    # The single move runs first: both jobs share the location/scope lock, so the reverse
    # order would queue it behind 500 items. The budget batch is only timed at enqueue;
    # its job finishes on its own with 404s after the workflow returns.
    single = await files.move_many(
        [{{"source": "{prefix}/one.txt", "destination": "{prefix}/two.txt"}}], location="move-e2e",
    )
    items = [{{"source": f"{prefix}/missing-{{i}}.pdf", "destination": f"{prefix}/moved-{{i}}.pdf"}} for i in range(500)]
    started = time.monotonic()
    response = await get_client().engine_request(
        "POST", "/api/files/move-many",
        json={{"items": items, "location": "move-e2e", "on_conflict": "error"}},
    )
    elapsed = time.monotonic() - started
    return {{"status": response.status_code, "elapsed": elapsed, "single": single}}
'''
    registered = write_and_register(
        e2e_client, platform_admin.headers, f"{name}.py", content, name, organization_id=org1["id"]
    )
    patched = e2e_client.patch(
        f"/api/workflows/{registered['id']}", headers=platform_admin.headers,
        json={"organization_id": org1["id"], "access_level": "authenticated"},
    )
    assert patched.status_code == 200, patched.text

    result = execute_workflow_sync(e2e_client, org1_user.headers, registered["id"], max_wait=240.0)

    assert result["status"] == "Success", result
    out = result["result"]
    assert out["status"] == 202
    # 500 items × three policy checks, through the engine socket, well inside the SDK's 30 s.
    assert out["elapsed"] < 10, out["elapsed"]
    assert [r["status_code"] for r in out["single"]] == [200]
