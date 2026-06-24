"""FilePolicyService loads scoped policies and evaluates them fail-closed."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.contracts.policies import FilePolicies
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.file_policy_service import FilePolicyDenied, FilePolicyService


def _allow_all(action: str = "read") -> FilePolicies:
    return FilePolicies.model_validate({
        "policies": [{"name": "allow", "actions": [action], "when": None}]
    })


def _deny_all() -> FilePolicies:
    return FilePolicies.model_validate({"policies": []})


def _user(org_id, **overrides):
    base = {
        "user_id": str(uuid4()),
        "email": "u@example.com",
        "organization_id": str(org_id),
        "is_platform_admin": False,
        "role_ids": [],
        "role_names": [],
        "claims": {},
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_service_selects_longest_prefix_and_ignores_siblings(db_session) -> None:
    org = Organization(id=uuid4(), name=f"Files-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    service = FilePolicyService(db_session)
    await service.upsert_policy(
        organization_id=org.id,
        location="workspace",
        path="reports",
        policies=_deny_all(),
        created_by=uuid4(),
    )
    await service.upsert_policy(
        organization_id=org.id,
        location="workspace",
        path="reports/q1",
        policies=_allow_all("read"),
        created_by=uuid4(),
    )

    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        location="workspace",
        path="reports/q1/a.pdf",
        user=_user(org.id),
    ) is True
    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        location="workspace",
        path="reports2/a.pdf",
        user=_user(org.id),
    ) is False


@pytest.mark.asyncio
async def test_service_isolates_organization_scoped_policies(db_session) -> None:
    org_a = Organization(id=uuid4(), name=f"FilesA-{uuid4().hex[:8]}", created_by="test")
    org_b = Organization(id=uuid4(), name=f"FilesB-{uuid4().hex[:8]}", created_by="test")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    service = FilePolicyService(db_session)
    await service.upsert_policy(
        organization_id=org_a.id,
        location="workspace",
        path="shared",
        policies=_allow_all("read"),
        created_by=uuid4(),
    )
    await service.upsert_policy(
        organization_id=org_b.id,
        location="workspace",
        path="shared",
        policies=_deny_all(),
        created_by=uuid4(),
    )

    assert await service.is_allowed(
        "read",
        organization_id=org_a.id,
        location="workspace",
        path="shared/doc.txt",
        user=_user(org_a.id),
    ) is True
    assert await service.is_allowed(
        "read",
        organization_id=org_b.id,
        location="workspace",
        path="shared/doc.txt",
        user=_user(org_b.id),
    ) is False
    assert await service.is_allowed(
        "read",
        organization_id=org_a.id,
        location="workspace",
        path="shared/doc.txt",
        user=_user(org_b.id),
    ) is False


@pytest.mark.asyncio
async def test_global_policy_cascades_to_org_user(db_session) -> None:
    """A global (org=NULL) policy applies to an org user — the org→global
    cascade. Since every real user has an org, a global policy that did NOT
    cascade would be dead weight; this locks that it reaches org callers."""
    org = Organization(id=uuid4(), name=f"Files-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    service = FilePolicyService(db_session)
    # Only a GLOBAL policy exists — no org-specific row.
    await service.upsert_policy(
        organization_id=None,
        location="shared",
        path="gallery",
        policies=_allow_all("read"),
        created_by=uuid4(),
    )

    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        location="shared",
        path="gallery/logo.png",
        user=_user(org.id),
    ) is True


@pytest.mark.asyncio
async def test_org_policy_overrides_global_for_same_prefix(db_session) -> None:
    """Org-specific prefix wins over the global prefix (cascade-with-override),
    mirroring OrgScopedRepository.get's 'org-specific first, then global'."""
    org = Organization(id=uuid4(), name=f"Files-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    service = FilePolicyService(db_session)
    # Global allows read; the org overrides the SAME prefix with deny.
    await service.upsert_policy(
        organization_id=None,
        location="shared",
        path="gallery",
        policies=_allow_all("read"),
        created_by=uuid4(),
    )
    await service.upsert_policy(
        organization_id=org.id,
        location="shared",
        path="gallery",
        policies=_deny_all(),
        created_by=uuid4(),
    )

    # The org's deny override wins for the org user…
    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        location="shared",
        path="gallery/logo.png",
        user=_user(org.id),
    ) is False
    # …while a DIFFERENT org with no override still sees the global allow.
    other = Organization(id=uuid4(), name=f"Files-{uuid4().hex[:8]}", created_by="test")
    db_session.add(other)
    await db_session.flush()
    assert await service.is_allowed(
        "read",
        organization_id=other.id,
        location="shared",
        path="gallery/logo.png",
        user=_user(other.id),
    ) is True


@pytest.mark.asyncio
async def test_service_uses_metadata_for_creator_policy(db_session) -> None:
    org = Organization(id=uuid4(), name=f"Files-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    creator_id = str(uuid4())
    service = FilePolicyService(db_session)
    await service.upsert_metadata(
        organization_id=org.id,
        location="workspace",
        path="owned/doc.txt",
        s3_key="_repo/owned/doc.txt",
        created_by=creator_id,
        updated_by=creator_id,
        content_type="text/plain",
        size_bytes=5,
        sha256="a" * 64,
    )
    await service.upsert_policy(
        organization_id=org.id,
        location="workspace",
        path="owned",
        policies=FilePolicies.model_validate({
            "policies": [
                {
                    "name": "creator_read",
                    "actions": ["read"],
                    "when": {"eq": [{"file": "created_by"}, {"user": "user_id"}]},
                }
            ]
        }),
        created_by=uuid4(),
    )

    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        location="workspace",
        path="owned/doc.txt",
        user=_user(org.id, user_id=creator_id),
    ) is True
    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        location="workspace",
        path="owned/doc.txt",
        user=_user(org.id, user_id=str(uuid4())),
    ) is False


@pytest.mark.asyncio
async def test_service_uses_solution_metadata_for_solution_creator_policy(
    db_session,
) -> None:
    org = Organization(id=uuid4(), name=f"Files-{uuid4().hex[:8]}", created_by="test")
    solution = Solution(
        id=uuid4(),
        slug=f"files-{uuid4().hex[:8]}",
        name="Files",
        organization_id=org.id,
    )
    db_session.add_all([org, solution])
    await db_session.flush()

    path = "owned/solution-doc.txt"
    org_creator_id = str(uuid4())
    solution_creator_id = str(uuid4())
    service = FilePolicyService(db_session)
    await service.upsert_metadata(
        organization_id=org.id,
        location="finance",
        path=path,
        s3_key=f"finance/{org.id}/{path}",
        created_by=org_creator_id,
        updated_by=org_creator_id,
        content_type="text/plain",
        size_bytes=5,
        sha256="a" * 64,
    )
    await service.upsert_metadata(
        organization_id=org.id,
        solution_id=solution.id,
        location="finance",
        path=path,
        s3_key=f"finance/{solution.id}/{path}",
        created_by=solution_creator_id,
        updated_by=solution_creator_id,
        content_type="text/plain",
        size_bytes=5,
        sha256="b" * 64,
    )
    db_session.add(
        FilePolicy(
            organization_id=org.id,
            solution_id=solution.id,
            location="finance",
            path="owned",
            policies={
                "policies": [
                    {
                        "name": "solution_creator_read",
                        "actions": ["read"],
                        "when": {"eq": [{"file": "created_by"}, {"user": "user_id"}]},
                    }
                ]
            },
            created_by=uuid4(),
        )
    )
    await db_session.flush()

    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        solution_id=solution.id,
        location="finance",
        path=path,
        user=_user(org.id, user_id=solution_creator_id),
    ) is True
    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        solution_id=solution.id,
        location="finance",
        path=path,
        user=_user(org.id, user_id=org_creator_id),
    ) is False


@pytest.mark.asyncio
async def test_service_preresolves_custom_claims_before_evaluation(
    db_session, monkeypatch
) -> None:
    from src.services import file_policy_service as service_module

    org = Organization(id=uuid4(), name=f"Files-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    service = FilePolicyService(db_session)
    await service.upsert_policy(
        organization_id=org.id,
        location="workspace",
        path="claims",
        policies=FilePolicies.model_validate({
            "policies": [
                {
                    "name": "claim_read",
                    "actions": ["read"],
                    "when": {"in": [{"file": "path"}, {"claims": "allowed_file_paths"}]},
                }
            ]
        }),
        created_by=uuid4(),
    )

    async def fake_preresolve(user, policies, db, org_id, solution_id=None):
        assert org_id == org.id
        user.claims["allowed_file_paths"] = ["claims/allowed.txt"]

    monkeypatch.setattr(service_module, "preresolve_for_policies", fake_preresolve)

    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        location="workspace",
        path="claims/allowed.txt",
        user=_user(org.id),
    ) is True
    assert await service.is_allowed(
        "read",
        organization_id=org.id,
        location="workspace",
        path="claims/denied.txt",
        user=_user(org.id),
    ) is False


@pytest.mark.asyncio
async def test_service_malformed_policy_json_denies(db_session, caplog) -> None:
    org = Organization(id=uuid4(), name=f"Files-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    service = FilePolicyService(db_session)
    row = await service.upsert_policy(
        organization_id=org.id,
        location="workspace",
        path="bad",
        policies=_allow_all("read"),
        created_by=uuid4(),
    )
    row.policies = {"policies": [{"name": "bad", "actions": ["execute"], "when": None}]}
    await db_session.flush()

    with caplog.at_level("WARNING", logger="src.services.file_policy_service"):
        allowed = await service.is_allowed(
            "read",
            organization_id=org.id,
            location="workspace",
            path="bad/doc.txt",
            user=_user(org.id),
        )

    assert allowed is False
    assert "malformed file policies" in caplog.text


@pytest.mark.asyncio
async def test_service_denial_hook_emits_audit(monkeypatch, db_session) -> None:
    from src.services import file_policy_service as service_module

    org = Organization(id=uuid4(), name=f"Files-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    service = FilePolicyService(db_session)
    await service.upsert_policy(
        organization_id=org.id,
        location="workspace",
        path="private",
        policies=_deny_all(),
        created_by=uuid4(),
    )
    emitted = {}

    async def fake_emit(db, action, **kwargs):
        emitted["action"] = action
        emitted.update(kwargs)

    monkeypatch.setattr(service_module, "emit_audit", fake_emit)

    with pytest.raises(FilePolicyDenied):
        await service.check_allowed(
            "read",
            organization_id=org.id,
            location="workspace",
            path="private/doc.txt",
            user=_user(org.id),
        )

    assert emitted["action"] == "policy.deny"
    assert emitted["resource_type"] == "file"
    assert emitted["outcome"] == "failure"
    assert emitted["details"] == {
        "policy_action": "read",
        "location": "workspace",
        "path": "private/doc.txt",
    }
