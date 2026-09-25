"""Engine-local transport for ``bifrost.files``.

Covers the acceptance surface that does not need a forked child, plus one
real forked child:

- the parent dispatcher calls the exact shared services
  (``shared.sdk_files`` + ``src.services.editor.search.search_files_db``)
  the HTTP handlers call, with a parent-derived ``FileCaller`` — tier
  order, Solution gates, policy probes, 404/403/400/409/422 semantics,
  text/base64 encoding, metadata commit-before-publish, and denial audits
  are identical by construction;
- the dispatcher builds token-equivalent identities (engine superuser vs
  service non-superuser — never the initiating user's admin flag), keeps
  the service's statuses, ignores forged child install/actor claims, and
  serves each request on one short session;
- the child transport performs real file round trips for all ten facade
  methods with zero HTTP requests, maps errors without HTTP fallback, and
  moves bytes over bounded chunked frames in both directions;
- a real ``TemplateProcess`` fork proves the worker-process path with API
  HTTP disabled.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import multiprocessing
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from bifrost._local_transport import MAX_FRAME_BYTES
from src.models.contracts.policies import FilePolicies


LOCATION = "reports"


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _context_data(org_id=None, **kwargs):
    """Parent-owned dispatch context shaped like the consumer's."""
    data = {
        "organization": {"id": str(org_id)} if org_id is not None else None,
        "is_platform_admin": kwargs.get("is_platform_admin", False),
        "execution_id": kwargs.get("execution_id", "exec-1"),
    }
    if kwargs.get("solution_id") is not None:
        data["solution_id"] = str(kwargs["solution_id"])
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _engine_principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(org_id, **kwargs))


def _service_principal(org_id, **kwargs):
    service_id = str(kwargs.get("service_id", uuid4()))
    attempt_id = str(kwargs.get("attempt_id", uuid4()))
    return _engine_principal(
        org_id,
        service={"service_id": service_id, "attempt_id": attempt_id},
        solution_id=kwargs.get("solution_id"),
        execution_id=attempt_id,
    )


async def _seed_org(db_session, *, is_provider=False):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-files-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-files-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _allow_policies(*actions: str) -> FilePolicies:
    return FilePolicies.model_validate({
        "policies": [
            {"name": f"allow-{a}", "actions": [a], "when": None}
            for a in actions
        ]
    })


async def _seed_policy(db_session, org_id, *, location=LOCATION, path=""):
    from src.services.file_policy_service import FilePolicyService

    await FilePolicyService(db_session).upsert_policy(
        organization_id=org_id,
        location=location,
        path=path,
        policies=_allow_policies("read", "write", "delete", "list"),
        created_by=uuid4(),
        seed_admin_bypass=False,
    )
    await db_session.flush()


class _DictBackend:
    """In-memory file backend keyed by (path, scope)."""

    def __init__(self, files: dict | None = None):
        self.files: dict[tuple[str, str | None], bytes] = dict(files or {})

    async def read(self, path, location, scope=None):
        try:
            return self.files[(path, scope)]
        except KeyError:
            raise FileNotFoundError(f"File not found: {path}") from None

    async def write(self, path, content, location, updated_by="system", scope=None):
        self.files[(path, scope)] = content

    async def delete(self, path, location, scope=None):
        self.files.pop((path, scope), None)

    async def list(self, directory, location, scope=None):
        prefix = f"{directory.strip('/')}/" if directory.strip("/") else ""
        return sorted(
            path for (path, file_scope) in self.files
            if file_scope == scope and path.startswith(prefix)
        )

    async def exists(self, path, location, scope=None):
        return (path, scope) in self.files


def _mock_backend(monkeypatch, backend: _DictBackend) -> _DictBackend:
    monkeypatch.setattr(
        "src.services.file_backend.get_backend",
        lambda mode, db=None: backend,
    )
    return backend


def _mock_tiers(monkeypatch, tiers) -> None:
    monkeypatch.setattr(
        "src.services.solution_scope.file_read_tiers",
        AsyncMock(return_value=tiers),
    )


def _tier(scope: str, org_id=None, solution_id=None):
    from src.services.solution_scope import FileTier

    return FileTier(
        name="solution" if solution_id is not None else "org",
        scope=scope,
        organization_id=org_id,
        solution_id=solution_id,
    )


def _mock_storage(monkeypatch) -> AsyncMock:
    storage = AsyncMock()
    storage.record_file_write_metadata = AsyncMock()
    storage.generate_presigned_upload_url = AsyncMock(
        return_value="https://s3/put-url"
    )
    storage.generate_presigned_download_url = AsyncMock(
        return_value="https://s3/get-url"
    )
    monkeypatch.setattr(
        "src.services.file_storage.FileStorageService", lambda db: storage
    )
    return storage


def _mock_publish(monkeypatch) -> AsyncMock:
    publish = AsyncMock()
    monkeypatch.setattr("src.core.pubsub.publish_file_change", publish)
    return publish


def _allow_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        "shared.sdk_files.authorize_file_policy",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "shared.sdk_files.require_file_policy",
        AsyncMock(return_value=None),
    )
    # ``filter_listed_paths`` (used by list) calls the ``file_access``
    # module's own ``authorize_file_policy`` — not the service's import —
    # so allow it there too.
    monkeypatch.setattr(
        "shared.file_access.authorize_file_policy",
        AsyncMock(return_value=True),
    )


def _deny_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        "shared.file_access.authorize_file_policy",
        AsyncMock(return_value=False),
    )


async def _local(frame, principal, db_session):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session), principal, frame
    )


def _file_frame(op, request_id, **fields):
    return {"v": 1, "id": request_id, "op": op, **fields}


@pytest.mark.asyncio
class TestFilesDispatchParity:
    async def test_write_then_read_text_roundtrip(
        self, db_session, monkeypatch
    ):
        org = await _seed_org(db_session)
        store = _DictBackend()
        _mock_backend(monkeypatch, store)
        _mock_tiers(monkeypatch, [_tier(str(org.id), org.id)])
        _mock_storage(monkeypatch)
        _mock_publish(monkeypatch)
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        written = await _local(
            _file_frame(
                "files.write", "w-1", path="note.txt", content="hello",
                location=LOCATION, mode="cloud", binary=False,
                expected_version=None, create_only=False,
                scope=str(org.id), solution=None,
            ),
            principal, db_session,
        )
        assert written["ok"] is True, written
        assert written["result"] is None

        read = await _local(
            _file_frame(
                "files.read", "r-1", path="note.txt",
                location=LOCATION, mode="cloud", binary=False,
                scope=str(org.id), solution=None,
            ),
            principal, db_session,
        )
        assert read["ok"] is True, read
        assert read["result"] == {"content": "hello", "binary": False}
        assert store.files[("note.txt", str(org.id))] == b"hello"

    async def test_write_bytes_then_read_bytes_roundtrip(
        self, db_session, monkeypatch
    ):
        org = await _seed_org(db_session)
        store = _DictBackend()
        _mock_backend(monkeypatch, store)
        _mock_tiers(monkeypatch, [_tier(str(org.id), org.id)])
        _mock_storage(monkeypatch)
        _mock_publish(monkeypatch)
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)
        payload = bytes(range(256)) * 4
        encoded = base64.b64encode(payload).decode()

        written = await _local(
            _file_frame(
                "files.write", "wb-1", path="blob.bin", content=encoded,
                location=LOCATION, mode="cloud", binary=True,
                expected_version=None, create_only=False,
                scope=str(org.id), solution=None,
            ),
            principal, db_session,
        )
        assert written["ok"] is True, written
        assert store.files[("blob.bin", str(org.id))] == payload

        read = await _local(
            _file_frame(
                "files.read", "rb-1", path="blob.bin",
                location=LOCATION, mode="cloud", binary=True,
                scope=str(org.id), solution=None,
            ),
            principal, db_session,
        )
        assert read["ok"] is True, read
        assert read["result"]["binary"] is True
        assert base64.b64decode(read["result"]["content"]) == payload

    async def test_list_exists_stat_delete_flow(
        self, db_session, monkeypatch
    ):
        org = await _seed_org(db_session)
        scope = str(org.id)
        store = _DictBackend({("a.txt", scope): b"a", ("sub/b.txt", scope): b"b"})
        _mock_backend(monkeypatch, store)
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _mock_storage(monkeypatch)
        _mock_publish(monkeypatch)
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        listed = await _local(
            _file_frame(
                "files.list", "l-1", directory="", location=LOCATION,
                mode="cloud", scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert listed["ok"] is True, listed
        assert listed["result"] == {"files": ["a.txt", "sub/b.txt"]}

        exists = await _local(
            _file_frame(
                "files.exists", "e-1", path="a.txt", location=LOCATION,
                mode="cloud", scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert exists["ok"] is True, exists
        assert exists["result"] is True

        missing = await _local(
            _file_frame(
                "files.exists", "e-2", path="gone.txt", location=LOCATION,
                mode="cloud", scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert missing["ok"] is True, missing
        assert missing["result"] is False

        stat = await _local(
            _file_frame(
                "files.stat", "s-1", path="a.txt", location=LOCATION,
                mode="cloud", scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert stat["ok"] is True, stat
        assert stat["result"]["exists"] is True
        assert stat["result"]["path"] == "a.txt"
        assert stat["result"]["size"] == 1

        absent = await _local(
            _file_frame(
                "files.stat", "s-2", path="gone.txt", location=LOCATION,
                mode="cloud", scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert absent["ok"] is True, absent
        assert absent["result"]["exists"] is False

        deleted = await _local(
            _file_frame(
                "files.delete", "d-1", path="a.txt", location=LOCATION,
                mode="cloud", expected_version=None, scope=scope,
                solution=None,
            ),
            principal, db_session,
        )
        assert deleted["ok"] is True, deleted
        assert deleted["result"] is None
        assert ("a.txt", scope) not in store.files

    async def test_signed_url_get_and_put(self, db_session, monkeypatch):
        org = await _seed_org(db_session)
        scope = str(org.id)
        store = _DictBackend({("doc.pdf", scope): b"%pdf"})
        _mock_backend(monkeypatch, store)
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _mock_storage(monkeypatch)
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        put = await _local(
            _file_frame(
                "files.signed_url", "u-put", path="up.bin", method="PUT",
                content_type="application/octet-stream", location="uploads",
                scope=scope, expires_in=600, solution=None,
            ),
            principal, db_session,
        )
        assert put["ok"] is True, put
        assert put["result"]["url"] == "https://s3/put-url"
        assert put["result"]["expires_in"] == 600

        get = await _local(
            _file_frame(
                "files.signed_url", "u-get", path="doc.pdf", method="GET",
                content_type="application/octet-stream", location="uploads",
                scope=scope, expires_in=600, solution=None,
            ),
            principal, db_session,
        )
        # Single-tier GET requires the signed_get policy (mocked allow).
        assert get["ok"] is True, get
        assert get["result"]["url"] == "https://s3/get-url"

    async def test_read_missing_is_404_parity(
        self, db_session, monkeypatch
    ):
        from shared.file_access import FileServiceError
        from shared.sdk_files import sdk_read_file

        org = await _seed_org(db_session)
        scope = str(org.id)
        _mock_backend(monkeypatch, _DictBackend())
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        async with _db_factory(db_session) as session:
            from src.services.execution.sdk_local_dispatch import (
                _file_caller_for_principal,
            )

            caller = _file_caller_for_principal(session, principal, None)
            with pytest.raises(FileServiceError) as exc:
                await sdk_read_file(
                    caller, path="gone.txt", location=LOCATION,
                    scope=scope, mode="cloud", binary=False,
                )
            assert exc.value.status_code == 404

        response = await _local(
            _file_frame(
                "files.read", "r-404", path="gone.txt",
                location=LOCATION, mode="cloud", binary=False,
                scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_denied_is_403_with_audit_parity(
        self, db_session, monkeypatch
    ):
        org = await _seed_org(db_session)
        scope = str(org.id)
        _mock_backend(monkeypatch, _DictBackend({("doc.txt", scope): b"x"}))
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _deny_policy(monkeypatch)
        principal = _engine_principal(org.id)

        for op, frame in (
            ("files.read", {"path": "doc.txt", "binary": False}),
            ("files.write", {"path": "doc.txt", "content": "x", "binary": False,
                             "expected_version": None, "create_only": False}),
            ("files.delete", {"path": "doc.txt", "expected_version": None}),
        ):
            base = {
                "location": LOCATION, "mode": "cloud", "scope": scope,
                "solution": None,
            }
            response = await _local(
                _file_frame(op, f"deny-{op}", **base, **frame),
                principal, db_session,
            )
            assert response["ok"] is False, (op, response)
            assert response["status"] == 403, (op, response)

    async def test_binary_without_flag_is_400_parity(
        self, db_session, monkeypatch
    ):
        org = await _seed_org(db_session)
        scope = str(org.id)
        _mock_backend(
            monkeypatch, _DictBackend({("img.bin", scope): b"\xff\x00raw"})
        )
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        response = await _local(
            _file_frame(
                "files.read", "r-bin", path="img.bin",
                location=LOCATION, mode="cloud", binary=False,
                scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert response["ok"] is False
        assert response["status"] == 400

    async def test_create_only_conflict_is_409_parity(
        self, db_session, monkeypatch
    ):
        org = await _seed_org(db_session)
        scope = str(org.id)
        _mock_backend(monkeypatch, _DictBackend({("taken.txt", scope): b"v1"}))
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _mock_storage(monkeypatch)
        _mock_publish(monkeypatch)
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        conflict = await _local(
            _file_frame(
                "files.write", "w-409", path="taken.txt", content="v2",
                location=LOCATION, mode="cloud", binary=False,
                expected_version=None, create_only=True,
                scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert conflict["ok"] is False
        assert conflict["status"] == 409

        stat = await _local(
            _file_frame(
                "files.stat", "s-v", path="taken.txt", location=LOCATION,
                mode="cloud", scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert stat["ok"] is True, stat
        version = stat["result"]["version"]

        stale = await _local(
            _file_frame(
                "files.write", "w-stale", path="taken.txt", content="v3",
                location=LOCATION, mode="cloud", binary=False,
                expected_version="sha256:stale", create_only=False,
                scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert stale["ok"] is False
        assert stale["status"] == 409

        guarded = await _local(
            _file_frame(
                "files.write", "w-guarded", path="taken.txt", content="v2",
                location=LOCATION, mode="cloud", binary=False,
                expected_version=version, create_only=False,
                scope=scope, solution=None,
            ),
            principal, db_session,
        )
        assert guarded["ok"] is True, guarded

    async def test_invalid_scope_is_400_parity(
        self, db_session, monkeypatch
    ):
        from shared.file_access import FileServiceError
        from shared.sdk_files import sdk_read_file

        org = await _seed_org(db_session)
        _mock_backend(monkeypatch, _DictBackend())
        # Real tiers (not mocked): scope resolution itself rejects the
        # garbage scope with ValueError → 400 on both paths.
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        async with _db_factory(db_session) as session:
            from src.services.execution.sdk_local_dispatch import (
                _file_caller_for_principal,
            )

            caller = _file_caller_for_principal(session, principal, None)
            with pytest.raises(FileServiceError) as exc:
                await sdk_read_file(
                    caller, path="a.txt", location=LOCATION,
                    scope="not-a-uuid", mode="cloud", binary=False,
                )
            assert exc.value.status_code == 400

        response = await _local(
            _file_frame(
                "files.read", "r-scope", path="a.txt",
                location=LOCATION, mode="cloud", binary=False,
                scope="not-a-uuid", solution=None,
            ),
            principal, db_session,
        )
        assert response["ok"] is False
        assert response["status"] == 400

    async def test_signed_put_invalid_scope_is_422_parity(
        self, db_session, monkeypatch
    ):
        # The shared service lets PUT scope-resolution ValueErrors escape
        # raw (the HTTP router lets them reach the 422 middleware); the
        # local dispatcher maps them to 422.
        org = await _seed_org(db_session)
        _mock_backend(monkeypatch, _DictBackend())
        _mock_storage(monkeypatch)
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        response = await _local(
            _file_frame(
                "files.signed_url", "u-422", path="up.bin", method="PUT",
                content_type="application/octet-stream", location="uploads",
                scope="not-a-uuid", expires_in=600, solution=None,
            ),
            principal, db_session,
        )
        assert response["ok"] is False
        assert response["status"] == 422


@pytest.mark.asyncio
class TestFilesCrossOrgAndSolution:
    async def test_cross_org_read_denied_without_policy(
        self, db_session, monkeypatch
    ):
        # Real policy evaluation: only org A declares the location. A
        # caller from org B (same engine superuser shape) is denied.
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        await _seed_policy(db_session, org_a.id)
        store = _DictBackend({("doc.txt", str(org_b.id)): b"secret"})
        _mock_backend(monkeypatch, store)
        _mock_tiers(
            monkeypatch, [_tier(str(org_b.id), org_b.id)]
        )

        denied = await _local(
            _file_frame(
                "files.read", "x-1", path="doc.txt",
                location=LOCATION, mode="cloud", binary=False,
                scope=str(org_b.id), solution=None,
            ),
            _engine_principal(org_b.id), db_session,
        )
        assert denied["ok"] is False
        assert denied["status"] == 403

        _mock_tiers(
            monkeypatch, [_tier(str(org_a.id), org_a.id)]
        )
        store.files[("doc.txt", str(org_a.id))] = b"shared"
        allowed = await _local(
            _file_frame(
                "files.read", "x-2", path="doc.txt",
                location=LOCATION, mode="cloud", binary=False,
                scope=str(org_a.id), solution=None,
            ),
            _engine_principal(org_a.id), db_session,
        )
        assert allowed["ok"] is True, allowed
        assert allowed["result"] == {"content": "shared", "binary": False}

    async def test_undeclared_solution_location_is_404(
        self, db_session, monkeypatch
    ):
        from uuid import uuid4 as _uuid4

        from shared.file_access import FileServiceError

        org = await _seed_org(db_session)
        solution_id = _uuid4()
        _mock_backend(monkeypatch, _DictBackend())
        _mock_tiers(monkeypatch, [_tier(str(solution_id), org.id, solution_id)])

        async def _undeclared(*args, **kwargs):
            raise FileServiceError(404, f"File location '{LOCATION}' not found")

        monkeypatch.setattr(
            "shared.sdk_files.require_declared_solution_file_location",
            _undeclared,
        )
        principal = _engine_principal(org.id, solution_id=solution_id)

        response = await _local(
            _file_frame(
                "files.read", "sol-404", path="doc.txt",
                location=LOCATION, mode="cloud", binary=False,
                scope=str(org.id), solution=str(solution_id),
            ),
            principal, db_session,
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_forged_caller_claims_are_ignored(
        self, db_session, monkeypatch
    ):
        org = await _seed_org(db_session)
        scope = str(org.id)
        _mock_backend(monkeypatch, _DictBackend({("doc.txt", scope): b"ok"}))
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        response = await _local(
            {
                "v": 1, "id": "forge-1", "op": "files.read",
                "path": "doc.txt", "location": LOCATION, "mode": "cloud",
                "binary": False, "scope": scope, "solution": None,
                # Trusted-claim fields a hostile child might stuff into the
                # frame — the dispatcher never reads them.
                "caller_solution": str(uuid4()),
                "app_id": "whatever",
                "user": {"is_superuser": True},
                "actor": "attacker@example.com",
            },
            principal, db_session,
        )
        assert response["ok"] is True, response
        assert response["result"] == {"content": "ok", "binary": False}

    async def test_malformed_fields_are_422(self, db_session, monkeypatch):
        _mock_backend(monkeypatch, _DictBackend())
        principal = _engine_principal(is_platform_admin=True)
        frames = [
            _file_frame("files.read", "m-1", location=LOCATION),
            _file_frame(
                "files.read", "m-2", path={"nested": 1},
                location=LOCATION, mode="cloud", binary=False,
            ),
            _file_frame(
                "files.read", "m-3", path="a.txt", location=LOCATION,
                mode="ftp", binary=False,
            ),
            _file_frame(
                "files.write", "m-4", path="a.txt", location=LOCATION,
                mode="cloud", binary="yes",
            ),
            _file_frame(
                "files.list", "m-5", directory="d", location=LOCATION,
                mode="nope",
            ),
            _file_frame(
                "files.signed_url", "m-6", path="a.txt", method="POST",
            ),
            _file_frame(
                "files.signed_url", "m-7", path="a.txt", method="PUT",
                expires_in=0,
            ),
            _file_frame("files.search", "m-8", query=""),
            _file_frame(
                "files.search", "m-9", query="x", max_results=100000,
            ),
            _file_frame("files.stat", "m-10"),
        ]
        for frame in frames:
            response = await _local(frame, principal, db_session)
            assert response["ok"] is False, frame
            assert response["status"] == 422, frame

    async def test_unknown_operation_and_version_rejected(
        self, db_session
    ):
        principal = _engine_principal(is_platform_admin=True)

        unknown = await _local(
            {"v": 1, "id": "bad-op", "op": "files.drop", "path": "a.txt"},
            principal, db_session,
        )
        assert unknown["ok"] is False
        assert unknown["status"] == 404

        bad_version_frame = dict(
            _file_frame("files.read", "bad-v", path="a.txt"), v=999
        )
        rejected = await _local(bad_version_frame, principal, db_session)
        assert rejected["ok"] is False
        assert rejected["status"] == 400


@pytest.mark.asyncio
class TestFilesSearchDispatch:
    async def _seed_index(self, db_session, entries: dict[str, str]):
        from src.models.orm.file_index import FileIndex

        for path, content in entries.items():
            db_session.add(FileIndex(path=path, content=content))
        await db_session.flush()

    async def test_search_success_parity(self, db_session):
        from src.models.contracts.editor import SearchRequest
        from src.services.editor.search import search_files_db

        token = f"needle-{uuid4().hex[:8]}"
        await self._seed_index(db_session, {
            f"sdk-files/{uuid4().hex[:8]}/a.py": f"# {token} here\nsecond\n",
            f"sdk-files/{uuid4().hex[:8]}/b.txt": "unrelated content\n",
        })
        principal = _engine_principal()

        async with _db_factory(db_session) as session:
            expected = await search_files_db(
                session,
                SearchRequest(query=token, max_results=100),
                root_path="",
            )

        response = await _local(
            _file_frame(
                "files.search", "q-1", query=token,
                case_sensitive=False, is_regex=False,
                include_pattern="**/*", max_results=100,
            ),
            principal, db_session,
        )
        assert response["ok"] is True, response
        assert response["result"]["query"] == expected.query
        assert response["result"]["total_matches"] == expected.total_matches
        assert response["result"]["results"] == [
            r.model_dump(mode="json") for r in expected.results
        ]

    async def test_search_invalid_regex_is_400(self, db_session):
        principal = _engine_principal()
        response = await _local(
            _file_frame(
                "files.search", "q-400", query="([", case_sensitive=False,
                is_regex=True, include_pattern="**/*", max_results=100,
            ),
            principal, db_session,
        )
        assert response["ok"] is False
        assert response["status"] == 400

    async def test_search_service_principal_is_403(self, db_session):
        org = await _seed_org(db_session)
        principal = _service_principal(org.id)
        response = await _local(
            _file_frame(
                "files.search", "q-403", query="anything",
                case_sensitive=False, is_regex=False,
                include_pattern="**/*", max_results=100,
            ),
            principal, db_session,
        )
        assert response["ok"] is False
        assert response["status"] == 403


@pytest.mark.asyncio
class TestFilesDispatchSessionsAndChunking:
    async def test_one_short_session_per_file_request(
        self, db_session, monkeypatch
    ):
        org = await _seed_org(db_session)
        scope = str(org.id)
        _mock_backend(monkeypatch, _DictBackend({("a.txt", scope): b"a"}))
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)
        opened = 0
        closed = 0

        @contextlib.asynccontextmanager
        async def _counting_factory():
            nonlocal opened, closed
            opened += 1
            try:
                yield db_session
            finally:
                closed += 1

        from src.services.execution.sdk_local_dispatch import dispatch_frame

        frames = [
            _file_frame(
                "files.read", "c-1", path="a.txt", location=LOCATION,
                mode="cloud", binary=False, scope=scope, solution=None,
            ),
            _file_frame(
                "files.list", "c-2", directory="", location=LOCATION,
                mode="cloud", scope=scope, solution=None,
            ),
            _file_frame(
                "files.exists", "c-3", path="a.txt", location=LOCATION,
                mode="cloud", scope=scope, solution=None,
            ),
            _file_frame(
                "files.stat", "c-4", path="a.txt", location=LOCATION,
                mode="cloud", scope=scope, solution=None,
            ),
        ]
        for frame in frames:
            response = await dispatch_frame(_counting_factory, principal, frame)
            assert response["ok"] is True, (frame, response)
        assert opened == 4
        assert closed == 4

    async def test_large_read_result_splits_into_bounded_frames(
        self, db_session, monkeypatch
    ):
        import json as _json

        from src.services.execution.sdk_local_dispatch import dispatch_frames

        org = await _seed_org(db_session)
        scope = str(org.id)
        big = "y" * 200_000
        _mock_backend(monkeypatch, _DictBackend({("big.txt", scope): big.encode()}))
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        @contextlib.asynccontextmanager
        async def _stub_factory():
            yield object()

        frames = await dispatch_frames(
            _stub_factory,
            principal,
            _file_frame(
                "files.read", "big-r", path="big.txt",
                location=LOCATION, mode="cloud", binary=False,
                scope=scope, solution=None,
            ),
        )
        collected = list(frames)
        assert len(collected) > 1
        header, parts = collected[0], collected[1:]
        assert header["ok"] is True and header["chunked"] is True
        assert header["parts"] == len(parts) >= 2
        for i, part in enumerate(parts):
            assert part["id"] == "big-r" and part["part"] == i
            raw = _json.dumps(part, separators=(",", ":")).encode()
            assert len(raw) <= MAX_FRAME_BYTES


class TestFilesChildTransport:
    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    async def _pump(self, req_conn, resp_conn, handler, count=1):
        for _ in range(count):
            raw = await asyncio.to_thread(req_conn.recv_bytes, 65537)
            frame = json.loads(raw.decode("utf-8"))
            response = handler(frame)
            await asyncio.to_thread(resp_conn.send_bytes, json.dumps(response).encode())

    @pytest.mark.asyncio
    async def test_all_ten_methods_round_trip_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        seen: dict[str, dict] = {}
        stat_doc = {
            "path": "a.txt", "exists": True, "version": "sha256:abc",
            "size": 5, "last_modified": None, "updated_by": "t",
        }
        search_doc = {
            "query": "TODO", "total_matches": 1, "files_searched": 2,
            "results": [], "truncated": False, "search_time_ms": 3,
        }

        def _handler(frame):
            seen[frame["op"]] = frame
            # The caller's install identity is parent-owned: the child
            # never sends caller_solution, app_id, or user claims.
            assert "caller_solution" not in frame, frame
            assert "app_id" not in frame, frame
            assert "user" not in frame, frame
            op = frame["op"]
            if op == "files.read":
                body = "aGVsbG8=" if frame["binary"] else "hello"
                return {
                    "v": 1, "id": frame["id"], "ok": True,
                    "result": {"content": body, "binary": frame["binary"]},
                }
            if op == "files.write":
                return {"v": 1, "id": frame["id"], "ok": True, "result": None}
            if op == "files.list":
                return {
                    "v": 1, "id": frame["id"], "ok": True,
                    "result": {"files": ["a.txt"]},
                }
            if op == "files.delete":
                return {"v": 1, "id": frame["id"], "ok": True, "result": None}
            if op == "files.exists":
                return {"v": 1, "id": frame["id"], "ok": True, "result": True}
            if op == "files.stat":
                return {
                    "v": 1, "id": frame["id"], "ok": True, "result": stat_doc,
                }
            if op == "files.signed_url":
                return {
                    "v": 1, "id": frame["id"], "ok": True,
                    "result": {
                        "url": "https://s3/x", "path": "k",
                        "expires_in": frame["expires_in"],
                    },
                }
            if op == "files.search":
                return {
                    "v": 1, "id": frame["id"], "ok": True,
                    "result": search_doc,
                }
            raise AssertionError(op)

        pump = asyncio.create_task(
            self._pump(req_recv, resp_send, _handler, count=10)
        )
        try:
            from bifrost._context import (
                clear_execution_context,
                set_execution_context,
            )
            from bifrost.files import files
            from src.sdk.context import ExecutionContext

            ctx = ExecutionContext(
                user_id="u1", email="e@e.com", name="T", scope="org-1",
                organization=None, is_platform_admin=False,
                is_function_key=False, execution_id="exec-1",
            )
            set_execution_context(ctx)
            try:
                with patch("bifrost.files.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    assert await files.read("a.txt") == "hello"
                    assert await files.read_bytes("a.bin") == b"hello"
                    await files.write("a.txt", "hello")
                    await files.write_bytes("a.bin", b"hello")
                    assert await files.list("") == ["a.txt"]
                    await files.delete("a.txt")
                    assert await files.exists("a.txt") is True
                    assert await files.stat("a.txt") == stat_doc
                    signed = await files.get_signed_url("a.txt")
                    assert signed["url"] == "https://s3/x"
                    assert signed["expires_in"] == 600
                    hits = await files.search("TODO")
                    assert hits["total_matches"] == 1
            finally:
                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        assert set(seen) == {
            "files.read", "files.write", "files.list", "files.delete",
            "files.exists", "files.stat", "files.signed_url",
            "files.search",
        }
        # read_bytes rides files.read with binary=true; write_bytes rides
        # files.write with binary=true and base64 content.
        assert seen["files.read"]["binary"] in (True, False)
        assert seen["files.search"]["query"] == "TODO"

    @pytest.mark.asyncio
    async def test_local_errors_raise_without_http_fallback(self):
        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAPIError, BifrostAuthorizationError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        seen: list[str] = []

        async def _pump_statuses():
            for status in (404, 403, 409, 500):
                raw = await asyncio.to_thread(req_recv.recv_bytes, 65537)
                frame = json.loads(raw.decode("utf-8"))
                seen.append(frame["op"])
                await asyncio.to_thread(
                    resp_send.send_bytes,
                    json.dumps(
                        {"v": 1, "id": frame["id"], "ok": False,
                         "status": status, "detail": "denied"}
                    ).encode(),
                )

        pump = asyncio.create_task(_pump_statuses())
        try:
            from bifrost.files import files

            with patch("bifrost.files.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                with pytest.raises(BifrostAPIError):
                    await files.read("ghost.txt")
                with pytest.raises(BifrostAuthorizationError):
                    await files.write("a.txt", "x")
                with pytest.raises(BifrostAPIError):
                    await files.delete("a.txt")
                with pytest.raises(Exception):
                    await files.list("")
            await pump
            assert seen == [
                "files.read", "files.write", "files.delete", "files.list",
            ]
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_chunked_bytes_round_trip_over_pipes(
        self, db_session, monkeypatch
    ):
        """Bytes ride bounded chunked frames in both directions end to end."""
        import base64 as _b64

        from bifrost import _local_transport as lt
        from src.services.execution.sdk_local_dispatch import serve_channel

        org = await _seed_org(db_session)
        scope = str(org.id)
        store = _DictBackend()
        _mock_backend(monkeypatch, store)
        _mock_tiers(monkeypatch, [_tier(scope, org.id)])
        _mock_storage(monkeypatch)
        _mock_publish(monkeypatch)
        _allow_policy(monkeypatch)
        principal = _engine_principal(org.id)

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=lambda: _db_factory(db_session),
                principal=principal,
            )
        )
        try:
            from bifrost.files import files

            payload = bytes((i * 7) % 256 for i in range(200_000))
            with patch("bifrost.files.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                # Chunked request: the 200KB base64 body exceeds one frame.
                await files.write_bytes(
                    "big.bin", payload, location=LOCATION, scope=scope
                )
                # Chunked response: the stored bytes come back in parts.
                assert await files.read_bytes(
                    "big.bin", location=LOCATION, scope=scope
                ) == payload
                assert _b64.b64encode(store.files[("big.bin", scope)]).decode() == (
                    _b64.b64encode(payload).decode()
                )
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))


@pytest.mark.slow
@pytest.mark.asyncio
class TestFilesForkedTransport:
    async def test_forked_child_files_roundtrip_without_http(
        self, db_session, monkeypatch
    ):
        """A real forked child runs all ten methods with HTTP disabled."""
        import base64 as _b64
        import os
        import time

        from src.services.execution.sdk_local_dispatch import (
            LocalDispatchPrincipal,
            serve_channel,
        )
        from src.services.execution.template_process import TemplateProcess

        tag = uuid4().hex[:8]
        prefix = f"sdk-files-fork-{tag}"
        monkeypatch.setenv("BIFROST_API_URL", "http://127.0.0.1:9")

        source = (
            "import base64 as _b64\n"
            "from bifrost import files\n"
            f"_p = {prefix!r} + '/note.txt'\n"
            f"_b = {prefix!r} + '/blob.bin'\n"
            "await files.write(_p, 'forked-hello')\n"
            "await files.write_bytes(_b, b'\\x00\\x01forked')\n"
            "_text = await files.read(_p)\n"
            "_raw = await files.read_bytes(_b)\n"
            "_exists = await files.exists(_p)\n"
            "_missing = await files.exists(_p + '.gone')\n"
            "_stat = await files.stat(_p)\n"
            "_listed = await files.list(" + repr(prefix) + ")\n"
            "_signed = await files.get_signed_url(\n"
            "    _b, method='GET', location='workspace',\n"
            "    content_type='application/octet-stream')\n"
            "_hits = await files.search('forked-hello')\n"
            "await files.delete(_p)\n"
            "await files.delete(_b)\n"
            "_gone = await files.exists(_p)\n"
            "result = {\n"
            "    'text': _text,\n"
            "    'raw': _b64.b64encode(_raw).decode(),\n"
            "    'exists': _exists,\n"
            "    'missing': _missing,\n"
            "    'stat_exists': _stat['exists'],\n"
            "    'listed': sorted(_listed),\n"
            "    'signed_path': _signed['path'],\n"
            "    'search_keys': sorted(_hits.keys()),\n"
            "    'gone': _gone,\n"
            "    'had_db_url': (\n"
            "        'BIFROST_DATABASE_URL' in __import__('os').environ\n"
            "        or 'BIFROST_DATABASE_URL_SYNC' in __import__('os').environ\n"
            "    ),\n"
            "}\n"
        )
        code = _b64.b64encode(source.encode("utf-8")).decode("utf-8")
        context = {
            "execution_id": f"fork-files-{tag}",
            "name": "sdk-files-fork-test",
            "code": code,
            "parameters": {},
            "caller": {
                "user_id": "fork-test-user",
                "email": "fork@test.local",
                "name": "Fork Test",
            },
            "organization": None,
            "tags": [],
            "timeout_seconds": 180,
            "cache_ttl_seconds": 0,
            "transient": True,
            "no_cache": True,
            "is_platform_admin": False,
            "engine_token": "fork-test-dead-token",
        }

        @contextlib.asynccontextmanager
        async def _factory():
            yield db_session

        template = TemplateProcess()
        template.start()
        pump = None
        conns = []
        try:
            child_pid, work_queue, result_queue, sdk_req, sdk_resp = (
                template.fork(worker_id="sdk-files-fork", with_sdk=True)
            )
            conns = [sdk_req, sdk_resp]
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=sdk_req,
                    send_conn=sdk_resp,
                    session_factory=lambda: _factory(),
                    principal=LocalDispatchPrincipal(caller_org_id=None),
                )
            )
            work_queue.put((f"exec-fork-files-{tag}", context))
            envelope = await asyncio.to_thread(result_queue.get, True, 180.0)
            assert envelope["success"] is True, envelope
            result = envelope["result"]
            assert result["text"] == "forked-hello"
            assert _b64.b64decode(result["raw"]) == b"\x00\x01forked"
            assert result["exists"] is True
            assert result["missing"] is False
            assert result["stat_exists"] is True
            assert result["listed"] == sorted([f"{prefix}/blob.bin", f"{prefix}/note.txt"])
            assert result["signed_path"].endswith("blob.bin")
            assert set(result["search_keys"]) == {
                "query", "total_matches", "files_searched", "results",
                "truncated", "search_time_ms",
            }
            assert result["gone"] is False
            assert result["had_db_url"] is False

            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                    time.sleep(0.05)
                except OSError:
                    break
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
            pump = None
        finally:
            if pump is not None:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            template.shutdown()
