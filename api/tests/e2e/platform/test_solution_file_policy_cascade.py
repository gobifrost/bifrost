"""E2E: file policy cascade own-solution → org → global (Task 3).

Verifies:
- A solution-scoped FilePolicy for a given prefix WINS over an org/global
  policy for the same logical prefix when resolving in that solution's context.
- An uncovered path (no solution policy) falls back to org/global.
- A non-solution caller's resolution is unchanged (solution arm does not fire).
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_allow_all_policies() -> dict:
    return {
        "policies": [
            {"name": "allow_all", "actions": ["read", "write", "delete", "list"]}
        ]
    }


def _make_deny_all_policies() -> dict:
    return {
        "policies": [
            {"name": "deny_all", "actions": [], "when": {"user": "never"}}
        ]
    }


async def _seed_policy(
    db_session,
    *,
    organization_id: UUID | None,
    solution_id: UUID | None,
    location: str,
    path: str,
    policies: dict,
) -> None:
    """Insert a FilePolicy row directly (mirrors Task 15 test pattern).

    ORM insert of a NEW row is fine — the read-only guard only catches
    update/delete of solution-managed rows, not inserts.
    """
    from src.models.orm.file_metadata import FilePolicy

    row = FilePolicy(
        organization_id=organization_id,
        solution_id=solution_id,
        location=location,
        path=path,
        policies=policies,
    )
    db_session.add(row)
    await db_session.flush()


def _create_solution(e2e_client, headers: dict, slug: str, org_id: str | None = None) -> dict:
    r = e2e_client.post(
        "/api/solutions",
        headers=headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": org_id},
    )
    assert r.status_code in (200, 201), f"create solution failed: {r.text}"
    return r.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solution_policy_wins_over_org_policy(
    e2e_client, platform_admin, org1, db_session
):
    """A solution-scoped policy for the covered prefix beats the org policy.

    Setup:
    - Org policy: location=solutions, path="" (root), deny-all (empty actions)
    - Solution policy: location=solutions, path="" (root), allow-all

    Resolution in solution context should return the allow-all solution policy,
    not the deny-all org policy.
    """
    from src.services.file_policy_service import FilePolicyService

    org_id = UUID(org1["id"])
    slug = f"cascade-win-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, platform_admin.headers, slug, org_id=str(org_id))
    install_id = UUID(sol["id"])

    # Seed org-level deny-all for the same prefix.
    await _seed_policy(
        db_session,
        organization_id=org_id,
        solution_id=None,
        location="solutions",
        path="",
        policies=_make_deny_all_policies(),
    )

    # Seed solution-own allow-all for the same prefix.
    await _seed_policy(
        db_session,
        organization_id=org_id,
        solution_id=install_id,
        location="solutions",
        path="",
        policies=_make_allow_all_policies(),
    )
    await db_session.commit()

    svc = FilePolicyService(db_session)
    resolved = await svc.load_policy(
        organization_id=org_id,
        location="solutions",
        path="report.pdf",
        solution_id=install_id,
    )

    assert resolved is not None, "load_policy returned None — no policy found"
    assert resolved.solution_id == install_id, (
        f"Expected solution-own policy (solution_id={install_id}), "
        f"got solution_id={resolved.solution_id}"
    )
    # Confirm it's the allow-all policy, not the deny-all org policy.
    assert resolved.policies.get("policies", [{}])[0].get("actions") == [
        "read", "write", "delete", "list"
    ], f"Wrong policy returned: {resolved.policies}"


@pytest.mark.asyncio
async def test_uncovered_path_falls_back_to_org(
    e2e_client, platform_admin, org1, db_session
):
    """When no solution policy covers the path, org policy is used as fallback."""
    from src.services.file_policy_service import FilePolicyService

    org_id = UUID(org1["id"])
    slug = f"cascade-fallback-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, platform_admin.headers, slug, org_id=str(org_id))
    install_id = UUID(sol["id"])

    # Solution policy ONLY covers the "restricted/" sub-prefix.
    await _seed_policy(
        db_session,
        organization_id=org_id,
        solution_id=install_id,
        location="solutions",
        path="restricted/",
        policies=_make_deny_all_policies(),
    )

    # Org policy covers the root.
    await _seed_policy(
        db_session,
        organization_id=org_id,
        solution_id=None,
        location="solutions",
        path="",
        policies=_make_allow_all_policies(),
    )
    await db_session.commit()

    svc = FilePolicyService(db_session)

    # Path NOT under "restricted/" — solution policy doesn't match, falls back to org.
    resolved = await svc.load_policy(
        organization_id=org_id,
        location="solutions",
        path="open/data.txt",
        solution_id=install_id,
    )

    assert resolved is not None, "load_policy returned None — fallback to org failed"
    assert resolved.solution_id is None, (
        f"Expected org policy (solution_id=None), got solution_id={resolved.solution_id}"
    )
    assert resolved.organization_id == org_id, (
        f"Expected org_id={org_id}, got {resolved.organization_id}"
    )


@pytest.mark.asyncio
async def test_uncovered_path_falls_back_to_global(
    e2e_client, platform_admin, db_session
):
    """When no solution or org policy covers the path, global policy is used."""
    from src.services.file_policy_service import FilePolicyService

    slug = f"cascade-global-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, platform_admin.headers, slug)
    install_id = UUID(sol["id"])
    org_id = UUID(sol["organization_id"]) if sol.get("organization_id") else None

    # Solution policy ONLY covers "private/".
    await _seed_policy(
        db_session,
        organization_id=org_id,
        solution_id=install_id,
        location="solutions",
        path="private/",
        policies=_make_deny_all_policies(),
    )

    # Global policy covers root.
    await _seed_policy(
        db_session,
        organization_id=None,
        solution_id=None,
        location="solutions",
        path="",
        policies=_make_allow_all_policies(),
    )
    await db_session.commit()

    svc = FilePolicyService(db_session)

    # Path under "open/" — no solution policy matches → falls back to global.
    resolved = await svc.load_policy(
        organization_id=org_id,
        location="solutions",
        path="open/readme.txt",
        solution_id=install_id,
    )

    assert resolved is not None, "load_policy returned None — fallback to global failed"
    assert resolved.solution_id is None, (
        f"Expected global policy (solution_id=None), got solution_id={resolved.solution_id}"
    )
    assert resolved.organization_id is None, (
        f"Expected global policy (org=None), got org_id={resolved.organization_id}"
    )


@pytest.mark.asyncio
async def test_non_solution_caller_unaffected(
    e2e_client, platform_admin, org1, db_session
):
    """A non-solution load_policy call (solution_id=None) ignores solution policies.

    A solution policy inserted for the same prefix must NOT be returned when
    the caller passes no solution_id.
    """
    from src.services.file_policy_service import FilePolicyService

    org_id = UUID(org1["id"])
    slug = f"cascade-nosol-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, platform_admin.headers, slug, org_id=str(org_id))
    install_id = UUID(sol["id"])

    # Solution policy that would incorrectly match a non-solution query.
    await _seed_policy(
        db_session,
        organization_id=org_id,
        solution_id=install_id,
        location="workspace",
        path="",
        policies=_make_deny_all_policies(),
    )

    # Org policy for the same prefix.
    await _seed_policy(
        db_session,
        organization_id=org_id,
        solution_id=None,
        location="workspace",
        path="",
        policies=_make_allow_all_policies(),
    )
    await db_session.commit()

    svc = FilePolicyService(db_session)

    # No solution_id — must NOT return the solution policy.
    resolved = await svc.load_policy(
        organization_id=org_id,
        location="workspace",
        path="notes.txt",
        solution_id=None,
    )

    assert resolved is not None, "load_policy returned None — org policy not found"
    assert resolved.solution_id is None, (
        f"Non-solution caller got solution policy (solution_id={resolved.solution_id})"
    )
    assert resolved.organization_id == org_id, (
        f"Expected org policy, got org_id={resolved.organization_id}"
    )


# ---------------------------------------------------------------------------
# Collision regression tests (Fix 2 & Fix 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_policy_exact_with_colocated_solution_row_returns_org_row(
    e2e_client, platform_admin, org1, db_session
):
    """Fix 2: _get_policy_exact must return the org row when a solution row shares
    (org, location, path).  Without solution_id IS NULL it raises MultipleResultsFound.
    """
    from src.services.file_policy_service import FilePolicyService

    org_id = UUID(org1["id"])
    slug = f"collision-policy-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, platform_admin.headers, slug, org_id=str(org_id))
    install_id = UUID(sol["id"])

    # Seed BOTH a solution row AND an org row at the same (org, location, path).
    await _seed_policy(
        db_session,
        organization_id=org_id,
        solution_id=install_id,
        location="solutions",
        path="docs",
        policies=_make_deny_all_policies(),
    )
    await _seed_policy(
        db_session,
        organization_id=org_id,
        solution_id=None,
        location="solutions",
        path="docs",
        policies=_make_allow_all_policies(),
    )
    await db_session.commit()

    svc = FilePolicyService(db_session)
    # get_policy_exact is the org-management path — it must return the ORG row only.
    row = await svc.get_policy_exact(
        organization_id=org_id,
        location="solutions",
        path="docs",
    )
    assert row is not None, "get_policy_exact returned None — org row not found"
    assert row.solution_id is None, (
        f"get_policy_exact returned the solution row (solution_id={row.solution_id}); "
        "expected the org row (solution_id IS NULL)"
    )


@pytest.mark.asyncio
async def test_get_metadata_with_colocated_solution_row_returns_org_row(
    e2e_client, platform_admin, org1, db_session
):
    """Fix 3: get_metadata must return the org row when a solution row shares
    (org, location, path).  Without solution_id IS NULL it raises MultipleResultsFound.
    """
    from src.models.orm.file_metadata import FileMetadata
    from src.services.file_policy_service import FilePolicyService

    org_id = UUID(org1["id"])
    slug = f"collision-meta-{uuid.uuid4().hex[:8]}"
    sol = _create_solution(e2e_client, platform_admin.headers, slug, org_id=str(org_id))
    install_id = UUID(sol["id"])

    # Seed solution-scoped metadata row.
    sol_meta = FileMetadata(
        organization_id=org_id,
        solution_id=install_id,
        location="solutions",
        path="report.pdf",
        s3_key="solutions/report.pdf",
        content_type="application/pdf",
        size_bytes=100,
        sha256="aabbcc",
    )
    db_session.add(sol_meta)

    # Seed org-scoped (non-solution) metadata row at the SAME path.
    org_meta = FileMetadata(
        organization_id=org_id,
        solution_id=None,
        location="solutions",
        path="report.pdf",
        s3_key="solutions/report-org.pdf",
        content_type="application/pdf",
        size_bytes=200,
        sha256="ddeeff",
    )
    db_session.add(org_meta)
    await db_session.flush()
    await db_session.commit()

    svc = FilePolicyService(db_session)
    row = await svc.get_metadata(
        organization_id=org_id,
        location="solutions",
        path="report.pdf",
    )
    assert row is not None, "get_metadata returned None — org row not found"
    assert row.solution_id is None, (
        f"get_metadata returned the solution row (solution_id={row.solution_id}); "
        "expected the org row (solution_id IS NULL)"
    )
    assert row.size_bytes == 200, (
        f"Wrong row returned: size_bytes={row.size_bytes} (expected 200 for org row)"
    )
