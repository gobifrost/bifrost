"""Usage-limit policy management endpoints."""

from __future__ import annotations

from uuid import uuid4

import pytest


def _platform_headers(user):
    return {**user.headers, "X-Bifrost-Boundary": "platform"}


def _org_headers(user, org_id: str):
    return {**user.headers, "X-Bifrost-Boundary": f"organization:{org_id}"}


@pytest.mark.e2e
def test_platform_admin_manages_org_policy_and_audits(
    e2e_client,
    platform_admin,
    org1,
) -> None:
    response = e2e_client.put(
        f"/api/settings/ai/usage-limits/organization/{org1['id']}",
        headers=_org_headers(platform_admin, org1["id"]),
        json={
            "per_run": {"total_tokens": 1000},
            "aggregate": {"model_requests": 10},
            "aggregate_period": "monthly",
        },
    )
    assert response.status_code == 200, response.text
    policy = response.json()
    assert policy["scope"] == "organization"
    assert policy["organization_id"] == org1["id"]
    assert policy["per_run"]["total_tokens"] == 1000

    effective = e2e_client.get(
        f"/api/settings/ai/usage-limits/effective/organization/{org1['id']}",
        headers=_org_headers(platform_admin, org1["id"]),
    )
    assert effective.status_code == 200, effective.text
    body = effective.json()
    assert body["effective_per_run_scope"] == "organization"
    assert body["aggregate"][0]["dimensions"][0]["percentage"] == 0

    audit = e2e_client.get(
        "/api/audit?action=usage_limit_policy.",
        headers=_platform_headers(platform_admin),
    )
    assert audit.status_code == 200, audit.text
    assert any(
        entry["action"] == "usage_limit_policy.upsert"
        and entry["details"]["scope"] == "organization"
        and entry["details"]["scope_key"] == org1["id"]
        for entry in audit.json()["entries"]
    )


@pytest.mark.e2e
def test_usage_limit_policy_requires_exact_selected_boundary(
    e2e_client,
    platform_admin,
    org1,
    org2,
) -> None:
    response = e2e_client.put(
        f"/api/settings/ai/usage-limits/organization/{org1['id']}",
        headers=_org_headers(platform_admin, org2["id"]),
        json={"aggregate": {"model_requests": 10}},
    )

    assert response.status_code == 409
    assert f"select organization:{org1['id']}" in response.text


@pytest.mark.e2e
def test_usage_limit_policy_denies_non_metrics_reader_and_empty_policy(
    e2e_client,
    platform_admin,
    org1_user,
    org1,
) -> None:
    denied = e2e_client.get(
        "/api/settings/ai/usage-limits",
        headers=_org_headers(org1_user, org1["id"]),
    )
    assert denied.status_code == 403

    empty = e2e_client.put(
        f"/api/settings/ai/usage-limits/organization/{org1['id']}",
        headers=_org_headers(platform_admin, org1["id"]),
        json={},
    )
    assert empty.status_code == 422


@pytest.mark.e2e
def test_usage_report_exact_org_reader_is_locked_to_selected_org(
    e2e_client,
    platform_admin,
    org1_user,
    org1,
    org2,
) -> None:
    role = e2e_client.post(
        "/api/roles",
        headers=_platform_headers(platform_admin),
        json={
            "name": f"Usage Metrics Reader {uuid4().hex[:8]}",
            "description": "Usage report exact-boundary test role",
            "capabilities": ["metrics.read"],
        },
    )
    assert role.status_code == 201, role.text
    role_id = role.json()["id"]
    try:
        assignment = e2e_client.post(
            f"/api/roles/{role_id}/users",
            headers=_platform_headers(platform_admin),
            json={
                "user_ids": [str(org1_user.user_id)],
                "boundaries": [
                    {
                        "boundary_kind": "organization",
                        "organization_id": org1["id"],
                    }
                ],
            },
        )
        assert assignment.status_code == 204, assignment.text

        allowed = e2e_client.get(
            "/api/reports/usage",
            headers=_org_headers(org1_user, org1["id"]),
            params={
                "start_date": "2026-08-01",
                "end_date": "2026-08-20",
                "source": "all",
            },
        )
        assert allowed.status_code == 200, allowed.text

        denied = e2e_client.get(
            "/api/reports/usage",
            headers=_org_headers(org1_user, org1["id"]),
            params={
                "start_date": "2026-08-01",
                "end_date": "2026-08-20",
                "source": "all",
                "org_id": org2["id"],
            },
        )
        assert denied.status_code == 409
    finally:
        e2e_client.delete(
            f"/api/roles/{role_id}",
            headers=_platform_headers(platform_admin),
        )


@pytest.mark.e2e
def test_current_user_can_read_own_effective_limits_without_metrics_read(
    e2e_client,
    platform_admin,
    org1_user,
    org1,
) -> None:
    create = e2e_client.put(
        f"/api/settings/ai/usage-limits/user/{org1_user.user_id}",
        headers=_org_headers(platform_admin, org1["id"]),
        json={
            "per_run": {"model_requests": 2},
            "aggregate": {"output_tokens": 50},
            "aggregate_period": "daily",
        },
    )
    assert create.status_code == 200, create.text

    effective = e2e_client.get(
        f"/api/settings/ai/usage-limits/effective/user/{org1_user.user_id}",
        headers=_org_headers(org1_user, org1["id"]),
    )

    assert effective.status_code == 200, effective.text
    body = effective.json()
    assert body["subject_scope"] == "user"
    assert body["effective_per_run_scope"] == "user"
    assert body["effective_per_run"]["model_requests"] == 2


@pytest.mark.e2e
def test_inaccessible_private_solution_effective_read_without_metrics_is_hidden(
    e2e_client,
    platform_admin,
    org1_user,
    org1,
) -> None:
    create = e2e_client.post(
        "/api/builder/solutions",
        headers=_org_headers(platform_admin, org1["id"]),
        json={
            "slug": f"usage-private-{uuid4().hex}",
            "name": "Usage Private",
            "target_kind": "solution",
        },
    )
    assert create.status_code == 201, create.text
    solution_id = create.json()["id"]

    effective = e2e_client.get(
        f"/api/settings/ai/usage-limits/effective/solution/{solution_id}",
        headers=org1_user.headers,
    )

    assert effective.status_code == 404


@pytest.mark.e2e
def test_private_solution_collaborator_with_metrics_cannot_mutate_policy(
    e2e_client,
    platform_admin,
    org1_user,
    org1,
) -> None:
    role = e2e_client.post(
        "/api/roles",
        headers=_platform_headers(platform_admin),
        json={
            "name": f"Usage Metrics Writer {uuid4().hex[:8]}",
            "description": "Usage-limit policy mutation test role",
            "capabilities": ["metrics.readwrite"],
        },
    )
    assert role.status_code == 201, role.text
    role_id = role.json()["id"]
    try:
        assignment = e2e_client.post(
            f"/api/roles/{role_id}/users",
            headers=_platform_headers(platform_admin),
            json={
                "user_ids": [str(org1_user.user_id)],
                "boundaries": [
                    {
                        "boundary_kind": "organization",
                        "organization_id": org1["id"],
                    }
                ],
            },
        )
        assert assignment.status_code == 204, assignment.text

        create = e2e_client.post(
            "/api/builder/solutions",
            headers=_org_headers(platform_admin, org1["id"]),
            json={
                "slug": f"usage-collab-{uuid4().hex}",
                "name": "Usage Collaborator",
                "target_kind": "solution",
            },
        )
        assert create.status_code == 201, create.text
        solution_id = create.json()["id"]

        collaborator = e2e_client.put(
            f"/api/builder/solutions/{solution_id}/collaborators",
            headers=_org_headers(platform_admin, org1["id"]),
            json={"email": org1_user.email, "access": "view"},
        )
        assert collaborator.status_code == 200, collaborator.text

        readable = e2e_client.get(
            f"/api/settings/ai/usage-limits/effective/solution/{solution_id}",
            headers=_org_headers(org1_user, org1["id"]),
        )
        assert readable.status_code == 200, readable.text

        denied = e2e_client.put(
            f"/api/settings/ai/usage-limits/solution/{solution_id}",
            headers=_org_headers(org1_user, org1["id"]),
            json={"aggregate": {"model_requests": 1}},
        )

        assert denied.status_code == 404
    finally:
        e2e_client.delete(
            f"/api/roles/{role_id}",
            headers=_platform_headers(platform_admin),
        )


@pytest.mark.e2e
def test_platform_list_does_not_expose_private_null_org_solution_policy(
    e2e_client,
    platform_admin,
    org1,
) -> None:
    create = e2e_client.post(
        "/api/builder/solutions",
        headers=_org_headers(platform_admin, org1["id"]),
        json={
            "slug": f"usage-hidden-{uuid4().hex}",
            "name": "Usage Hidden",
            "target_kind": "solution",
        },
    )
    assert create.status_code == 201, create.text
    solution_id = create.json()["id"]
    policy = e2e_client.put(
        f"/api/settings/ai/usage-limits/solution/{solution_id}",
        headers=_org_headers(platform_admin, org1["id"]),
        json={"aggregate": {"model_requests": 1}},
    )
    assert policy.status_code == 200, policy.text

    listed = e2e_client.get(
        "/api/settings/ai/usage-limits",
        headers=_platform_headers(platform_admin),
    )

    assert listed.status_code == 200, listed.text
    assert all(
        row["solution_id"] != solution_id for row in listed.json()["policies"]
    )
