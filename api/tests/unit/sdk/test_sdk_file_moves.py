# api/tests/unit/sdk/test_sdk_file_moves.py
"""Enqueue-time validation, authorization, and job metadata for files.move."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from shared.file_access import FileCaller, FileServiceError
from shared.sdk_files import file_move_dedupe_key, sdk_enqueue_file_moves
from src.core.principal import UserPrincipal
from src.services.file_policy_service import FilePolicyEvalCache

SOLUTION_ID = UUID("11111111-1111-1111-1111-111111111111")
ORG_ID = UUID("22222222-2222-2222-2222-222222222222")


def _caller(*, solution_id: UUID | None = SOLUTION_ID, is_superuser: bool = False) -> FileCaller:
    user = UserPrincipal(
        user_id=uuid4(), email="mover@example.com", organization_id=ORG_ID, name="Mover",
        is_active=True, is_superuser=is_superuser, is_verified=True,
    )
    db = MagicMock()
    db.commit = AsyncMock()
    return FileCaller(
        user=user, db=db, org_id=ORG_ID,
        solution_id=str(solution_id) if solution_id else None,
    )


@pytest.fixture(autouse=True)
def _verified_caller_context(monkeypatch):
    """Scope resolution loads the caller and the active install from the DB."""
    from shared.builtin_roles import USER_BASE_PERMISSIONS, USER_ROLE_ID
    from src.services.authorization.context import AuthorizationContext
    from src.services.authorization.enforce import Caller

    async def load_verified_caller(_db, user):
        return Caller(user, AuthorizationContext(
            user_id=user.user_id, home_organization_id=user.organization_id,
            base_role_id=USER_ROLE_ID, base_permissions=USER_BASE_PERMISSIONS,
            is_external=user.is_external,
        ))

    monkeypatch.setattr("src.services.authorization.enforce.load_caller", load_verified_caller)
    monkeypatch.setattr(
        "src.services.solution_scope.get_active_solution",
        AsyncMock(return_value=SimpleNamespace(id=SOLUTION_ID, organization_id=ORG_ID)),
    )
    monkeypatch.setattr(
        "src.services.solution_scope.inbound_allowed_for_context", AsyncMock(return_value=True)
    )


@pytest.fixture
def env(monkeypatch):
    denied: set[tuple[str, str]] = set()
    checks: list[dict] = []

    async def authorize(_caller, *, action, location, scope, path, solution_id, organization_id, policy_cache):
        checks.append({"action": action, "path": path, "organization_id": organization_id, "cache": policy_cache})
        return (action, path) not in denied

    job = SimpleNamespace(id=uuid4(), notification_id=None, status="queued")
    state = SimpleNamespace(
        denied=denied,
        checks=checks,
        emit=AsyncMock(),
        enqueue=AsyncMock(return_value=(job, False)),
        find=AsyncMock(return_value=None),
        declared=AsyncMock(),
        job=job,
    )
    monkeypatch.setattr("shared.sdk_files.authorize_file_policy", authorize)
    monkeypatch.setattr("shared.sdk_files.emit_file_policy_deny", state.emit)
    monkeypatch.setattr("shared.sdk_files.require_declared_solution_file_location", state.declared)
    monkeypatch.setattr("shared.sdk_files.install_org_id", AsyncMock(return_value=ORG_ID))
    monkeypatch.setattr("src.services.platform_jobs.enqueue_platform_job", state.enqueue)
    monkeypatch.setattr("src.services.platform_jobs.find_active_platform_job", state.find)
    return state


def _payload(env):
    return env.enqueue.await_args.args[2]


async def test_valid_items_enqueue_a_locked_priority_job(env) -> None:
    caller = _caller()
    job, reused = await sdk_enqueue_file_moves(
        caller, items=[("a/1.pdf", "b/1.pdf"), ("a/2.pdf", "b/2.pdf")], location="docs", scope=None
    )
    assert (job, reused) == (env.job, False)
    payload = _payload(env)
    assert [(i.index, i.source, i.destination) for i in payload.items] == [
        (0, "a/1.pdf", "b/1.pdf"), (1, "a/2.pdf", "b/2.pdf"),
    ]
    assert (payload.scope, payload.solution_id, payload.organization_id, payload.total) == (
        str(SOLUTION_ID), SOLUTION_ID, ORG_ID, 2,
    )
    kwargs = env.enqueue.await_args.kwargs
    assert kwargs["resource_lock_key"] == "files.move:" + hashlib.sha256(
        f"docs:{SOLUTION_ID}".encode()
    ).hexdigest()
    assert kwargs["priority"] == 50
    assert kwargs["title"] == "Move 2 files in docs"
    assert kwargs["resource_type"] == "file_location"
    assert kwargs["resource_id"] == f"docs:{SOLUTION_ID}"
    assert kwargs["organization_id"] == ORG_ID
    assert kwargs["requested_by_user_id"] == caller.user.user_id
    assert (payload.policy_user_id, payload.policy_authorities) == (caller.user.user_id, [])


@pytest.mark.parametrize(
    ("source", "destination", "code"),
    [
        ("a/r.pdf", "a/r.pdf", "same_path"),
        ("", "b/r.pdf", "invalid_path"),
        ("a/r.pdf", "b/", "invalid_path"),
        ("a//r.pdf", "b/r.pdf", "invalid_path"),
        ("a/./r.pdf", "b/r.pdf", "invalid_path"),
        ("a/../r.pdf", "b/r.pdf", "invalid_path"),
        ("/a/r.pdf", "b/r.pdf", "invalid_path"),
        ("a/r.pdf", "b/" + "x" * 1995, "path_too_long"),
    ],
    ids=["same", "empty", "trailing-slash", "empty-segment", "dot", "dotdot", "leading-slash", "too-long"],
)
async def test_invalid_paths_are_per_item_400s(env, source, destination, code) -> None:
    await sdk_enqueue_file_moves(
        _caller(), items=[(source, destination), ("a/ok.pdf", "b/ok.pdf")], location="docs", scope=None
    )
    payload = _payload(env)
    [rejected] = payload.rejected
    assert (rejected.index, rejected.result.status_code) == (0, 400)
    assert rejected.result.error.split(":")[0] == code
    assert [i.index for i in payload.items] == [1]


async def test_read_denial_is_an_audited_403_and_stops_further_checks(env) -> None:
    env.denied.add(("read", "a/secret.pdf"))
    await sdk_enqueue_file_moves(_caller(), items=[("a/secret.pdf", "b/s.pdf")], location="docs", scope=None)
    [rejected] = _payload(env).rejected
    assert (rejected.result.status_code, rejected.result.error) == (403, "forbidden")
    assert env.emit.await_args.kwargs["policy_action"] == "read"
    assert [c["action"] for c in env.checks] == ["read"]


async def test_destination_write_denial_is_403(env) -> None:
    env.denied.add(("write", "b/r.pdf"))
    await sdk_enqueue_file_moves(_caller(), items=[("a/r.pdf", "b/r.pdf")], location="docs", scope=None)
    assert [c["action"] for c in env.checks] == ["read", "delete", "write"]
    assert env.emit.await_args.kwargs["path"] == "b/r.pdf"


async def test_checks_share_one_cache_and_the_resolved_org(env) -> None:
    await sdk_enqueue_file_moves(
        _caller(), items=[("a/1.pdf", "b/1.pdf"), ("a/2.pdf", "b/2.pdf")], location="docs", scope=None
    )
    caches = {id(c["cache"]) for c in env.checks}
    assert len(caches) == 1 and isinstance(env.checks[0]["cache"], FilePolicyEvalCache)
    assert {c["organization_id"] for c in env.checks} == {ORG_ID}


async def test_all_rejected_still_enqueues_without_lock_or_priority(env) -> None:
    await sdk_enqueue_file_moves(_caller(), items=[("a/r.pdf", "a/r.pdf")], location="docs", scope=None)
    kwargs = env.enqueue.await_args.kwargs
    assert (kwargs["resource_lock_key"], kwargs["priority"]) == (None, 100)
    assert kwargs["title"] == "Move 1 file in docs"
    assert _payload(env).items == []


async def test_identical_retry_reuses_active_job_before_policy(env) -> None:
    active = SimpleNamespace(id=uuid4())
    env.find.return_value = active
    job, reused = await sdk_enqueue_file_moves(
        _caller(), items=[("a/r.pdf", "b/r.pdf")], location="docs", scope=None
    )
    assert (job, reused) == (active, True)
    assert env.checks == [] and env.emit.await_count == 0
    env.enqueue.assert_not_awaited()


def test_dedupe_key_is_stable_and_order_sensitive() -> None:
    user = uuid4()
    items = [("a", "b"), ("c", "d")]
    one = file_move_dedupe_key(user, user, [], "docs", "s", "error", items)
    assert one == file_move_dedupe_key(user, user, [], "docs", "s", "error", items)
    assert one != file_move_dedupe_key(user, user, [], "docs", "s", "error", items[::-1])
    assert one != file_move_dedupe_key(user, user, [], "docs", "s", "rename", items)


def test_dedupe_key_separates_policy_principals_of_one_acting_user() -> None:
    """Two runs by one acting user, as different run users or with Full Access."""
    acting, run_user = uuid4(), uuid4()
    items = [("a", "b")]
    own = file_move_dedupe_key(acting, acting, [], "docs", "s", "error", items)
    assert own != file_move_dedupe_key(acting, run_user, [], "docs", "s", "error", items)
    assert own != file_move_dedupe_key(acting, acting, ["platform_admin"], "docs", "s", "error", items)


async def test_reuse_lookup_uses_the_enforced_run_user(env, monkeypatch) -> None:
    run_user = SimpleNamespace(user_id=uuid4(), policy_authorities=frozenset({"platform_admin"}))
    monkeypatch.setattr(
        "src.services.access_check_policies.enforced_policy_principal",
        AsyncMock(return_value=run_user),
    )
    caller = _caller()

    await sdk_enqueue_file_moves(caller, items=[("a/r.pdf", "b/r.pdf")], location="docs", scope=None)

    expected = file_move_dedupe_key(
        caller.user.user_id, run_user.user_id, ["platform_admin"],
        "docs", str(SOLUTION_ID), "error", [("a/r.pdf", "b/r.pdf")],
    )
    assert env.find.await_args.args[2] == expected


@pytest.mark.parametrize("location", ["workspace", "_repo", "Bad Name"])
async def test_request_level_location_errors(env, location) -> None:
    with pytest.raises(FileServiceError) as caught:
        await sdk_enqueue_file_moves(_caller(), items=[("a", "b")], location=location, scope=None)
    assert caught.value.status_code == 400
    env.enqueue.assert_not_awaited()


async def test_undeclared_solution_location_is_404(env) -> None:
    env.declared.side_effect = FileServiceError(404, "File location 'docs' not found")
    with pytest.raises(FileServiceError) as caught:
        await sdk_enqueue_file_moves(_caller(), items=[("a", "b")], location="docs", scope=None)
    assert caught.value.status_code == 404


async def test_malformed_scope_is_422_like_other_file_operations(env) -> None:
    with pytest.raises(FileServiceError) as caught:
        await sdk_enqueue_file_moves(
            _caller(solution_id=None, is_superuser=True),
            items=[("a", "b")], location="docs", scope="not-a-uuid",
        )
    assert caught.value.status_code == 422


async def test_org_scope_keys_metadata_by_scope(env) -> None:
    await sdk_enqueue_file_moves(
        _caller(solution_id=None), items=[("a/r.pdf", "b/r.pdf")], location="docs", scope=None
    )
    payload = _payload(env)
    assert (payload.scope, payload.solution_id, payload.organization_id) == (str(ORG_ID), None, ORG_ID)


async def test_sealed_install_is_an_audited_403_before_any_policy_check(env, monkeypatch) -> None:
    emit = AsyncMock()
    monkeypatch.setattr(
        "src.services.solution_scope.inbound_allowed_for_context", AsyncMock(return_value=False)
    )
    monkeypatch.setattr("shared.file_access.emit_file_policy_deny", emit)

    with pytest.raises(FileServiceError) as caught:
        await sdk_enqueue_file_moves(
            _caller(), items=[("a/r.pdf", "b/r.pdf")], location="docs", scope=None
        )

    assert caught.value.status_code == 403
    assert emit.await_args.kwargs["path"] == "a/r.pdf"
    assert env.checks == []
    env.enqueue.assert_not_awaited()
