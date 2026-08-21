"""E2E coverage for the private Builder Solution API surface.

Adapted from the withdrawn Builder tests to the current Pydantic/PlatformJob
architecture. These tests deliberately stay at the REST boundary: capability
gate, private invisibility, support catalog, slug identity, sessions/revisions,
promotion gates, and non-owner 404s.
"""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from datetime import datetime, timezone
from uuid import UUID

import pytest

from tests.e2e.fixtures.setup import _login_user

BUILDER_URL = "/api/builder/solutions"


def _slug(prefix: str = "priv") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _create(e2e_client, headers, slug: str, name: str | None = None):
    return e2e_client.post(
        BUILDER_URL,
        headers=headers,
        json={"slug": slug, "name": name or f"Private {slug}"},
    )


@pytest.fixture(scope="module")
def builder_role(e2e_client, platform_admin):
    platform_headers = {
        **platform_admin.headers,
        "X-Bifrost-Boundary": "platform",
    }
    resp = e2e_client.post(
        "/api/roles",
        headers=platform_headers,
        json={
            "name": f"E2E Builder {uuid.uuid4().hex[:8]}",
            "description": "private builder e2e",
            "capabilities": [
                "builder.read",
                "builder.execute",
                "solutions.read",
                "solutions.readwrite",
                "solutions.build.execute",
                "solutions.deploy.execute",
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    role = resp.json()
    yield role
    e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_headers)


@pytest.fixture(scope="module")
def builder_alice(e2e_client, platform_admin, builder_role, alice_user):
    resp = e2e_client.post(
        f"/api/roles/{builder_role['id']}/users",
        headers=platform_admin.headers,
        json={
            "user_ids": [str(alice_user.user_id)],
            "boundaries": [
                {
                    "boundary_kind": "organization",
                    "organization_id": str(alice_user.organization_id),
                }
            ],
        },
    )
    assert resp.status_code == 204, resp.text
    _login_user(e2e_client, alice_user)
    yield alice_user
    e2e_client.delete(
        f"/api/roles/{builder_role['id']}/users/{alice_user.user_id}",
        headers=platform_admin.headers,
    )
    _login_user(e2e_client, alice_user)


@pytest.fixture
def alice_solution(e2e_client, builder_alice):
    resp = _create(e2e_client, builder_alice.headers, _slug())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    yield body
    e2e_client.delete(f"{BUILDER_URL}/{body['id']}", headers=builder_alice.headers)


@pytest.fixture
def alice_session(e2e_client, builder_alice, alice_solution):
    resp = e2e_client.post(
        f"{BUILDER_URL}/{alice_solution['id']}/sessions",
        headers=builder_alice.headers,
        json={"title": "Build me a thing"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def builder_bob(e2e_client, platform_admin, builder_role, bob_user):
    resp = e2e_client.post(
        f"/api/roles/{builder_role['id']}/users",
        headers=platform_admin.headers,
        json={
            "user_ids": [str(bob_user.user_id)],
            "boundaries": [
                {
                    "boundary_kind": "organization",
                    "organization_id": str(bob_user.organization_id),
                }
            ],
        },
    )
    assert resp.status_code == 204, resp.text
    _login_user(e2e_client, bob_user)
    yield bob_user
    e2e_client.delete(
        f"/api/roles/{builder_role['id']}/users/{bob_user.user_id}",
        headers=platform_admin.headers,
    )
    _login_user(e2e_client, bob_user)


pytestmark = pytest.mark.e2e


class TestBuilderCapabilityGate:
    def test_user_without_permission_cannot_create(self, e2e_client, bob_user):
        resp = _create(e2e_client, bob_user.headers, _slug("nogate"))
        assert resp.status_code == 403, resp.text

    def test_user_without_permission_cannot_list(self, e2e_client, bob_user):
        resp = e2e_client.get(BUILDER_URL, headers=bob_user.headers)
        assert resp.status_code == 403, resp.text

    def test_granted_user_can_create(self, e2e_client, builder_alice):
        slug = _slug("granted")
        resp = _create(e2e_client, builder_alice.headers, slug)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        try:
            assert body["slug"] == slug
            assert body["visibility"] == "private"
            assert body["owner_user_id"] == str(builder_alice.user_id)
            assert body["organization_id"] == str(builder_alice.organization_id)
            assert body["status"] == "active"
            assert body["promotion_status"] == "none"
        finally:
            e2e_client.delete(f"{BUILDER_URL}/{body['id']}", headers=builder_alice.headers)

    def test_platform_operator_can_support_but_cannot_start_builds(
        self,
        e2e_client,
        provider_org_user,
        alice_solution,
    ):
        create_response = _create(
            e2e_client,
            provider_org_user.headers,
            _slug("operator-denied"),
        )
        assert create_response.status_code == 403, create_response.text

        support_response = e2e_client.get(
            BUILDER_URL,
            headers={
                **provider_org_user.headers,
                "X-Bifrost-Boundary": "managed_organizations",
            },
            params={
                "view": "all",
                "organization_id": alice_solution["organization_id"],
                "search": alice_solution["slug"],
            },
        )
        assert support_response.status_code == 200, support_response.text
        body = support_response.json()
        assert body["can_view_all"] is True
        row = next(
            solution
            for solution in body["solutions"]
            if solution["id"] == alice_solution["id"]
        )
        assert row["caller_access"] == "support"


class TestPrivateInvisibilityAndSupport:
    def test_owner_sees_own_solution(self, e2e_client, builder_alice, alice_solution):
        detail = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}", headers=builder_alice.headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == alice_solution["id"]
        assert detail.json()["caller_access"] == "owner"

        listing = e2e_client.get(BUILDER_URL, headers=builder_alice.headers)
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["view"] == "mine"
        assert body["is_platform_admin"] is False
        assert alice_solution["id"] in {s["id"] for s in body["solutions"]}

    def test_permitted_non_owner_gets_404_and_empty_mine_list(
        self, e2e_client, builder_bob, alice_solution
    ):
        detail = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}", headers=builder_bob.headers
        )
        assert detail.status_code == 404, detail.text

        listing = e2e_client.get(BUILDER_URL, headers=builder_bob.headers)
        assert listing.status_code == 200, listing.text
        assert alice_solution["id"] not in {
            s["id"] for s in listing.json()["solutions"]
        }

        deleted = e2e_client.delete(
            f"{BUILDER_URL}/{alice_solution['id']}", headers=builder_bob.headers
        )
        assert deleted.status_code == 404, deleted.text

    def test_platform_admin_default_list_stays_focused_and_detail_requires_org_context(
        self, e2e_client, platform_admin, alice_solution
    ):
        listing = e2e_client.get(BUILDER_URL, headers=platform_admin.headers)
        assert listing.status_code == 200, listing.text
        assert listing.json()["is_platform_admin"] is True
        assert alice_solution["id"] not in {s["id"] for s in listing.json()["solutions"]}

        detail = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}",
            headers={
                **platform_admin.headers,
                "X-Bifrost-Boundary": (
                    f"organization:{alice_solution['organization_id']}"
                ),
            },
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["caller_access"] == "support"

    def test_platform_admin_support_view_can_find_private_work(
        self, e2e_client, platform_admin, builder_alice, alice_solution
    ):
        listing = e2e_client.get(
            BUILDER_URL,
            headers={
                **platform_admin.headers,
                "X-Bifrost-Boundary": "managed_organizations",
            },
            params={
                "view": "all",
                "organization_id": str(builder_alice.organization_id),
                "owner_user_id": str(builder_alice.user_id),
                "search": alice_solution["slug"],
            },
        )
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["view"] == "all"
        assert body["can_view_all"] is True
        row = next(s for s in body["solutions"] if s["id"] == alice_solution["id"])
        assert row["caller_access"] == "support"
        assert row["owner_email"] == builder_alice.email

    def test_standard_solution_catalog_does_not_show_private_solution(
        self, e2e_client, platform_admin, alice_solution
    ):
        listing = e2e_client.get("/api/solutions", headers=platform_admin.headers)
        assert listing.status_code == 200, listing.text
        assert alice_solution["id"] not in {
            row["id"] for row in listing.json()["solutions"]
        }

        detail = e2e_client.get(
            f"/api/solutions/{alice_solution['id']}",
            headers=platform_admin.headers,
        )
        assert detail.status_code == 404, detail.text


class TestSlugIdentity:
    def test_two_owners_may_share_a_slug_in_one_org(
        self, e2e_client, builder_alice, builder_bob
    ):
        slug = _slug("shared-slug")
        alice_resp = _create(e2e_client, builder_alice.headers, slug)
        assert alice_resp.status_code == 201, alice_resp.text
        bob_resp = _create(e2e_client, builder_bob.headers, slug)
        try:
            assert bob_resp.status_code == 201, bob_resp.text
            assert bob_resp.json()["id"] != alice_resp.json()["id"]
        finally:
            e2e_client.delete(
                f"{BUILDER_URL}/{alice_resp.json()['id']}",
                headers=builder_alice.headers,
            )
            if bob_resp.status_code == 201:
                e2e_client.delete(
                    f"{BUILDER_URL}/{bob_resp.json()['id']}",
                    headers=builder_bob.headers,
                )

    def test_same_owner_duplicate_slug_conflicts(self, e2e_client, builder_alice):
        slug = _slug("dupe")
        first = _create(e2e_client, builder_alice.headers, slug)
        assert first.status_code == 201, first.text
        try:
            second = _create(e2e_client, builder_alice.headers, slug)
            assert second.status_code == 409, second.text
        finally:
            e2e_client.delete(
                f"{BUILDER_URL}/{first.json()['id']}", headers=builder_alice.headers
            )


class TestSessionsAndRevisions:
    def test_create_session_and_list_newest_first(
        self, e2e_client, builder_alice, alice_solution, alice_session
    ):
        assert alice_session["solution_id"] == alice_solution["id"]
        assert alice_session["user_id"] == str(builder_alice.user_id)
        assert alice_session["conversation_id"]

        second = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/sessions",
            headers=builder_alice.headers,
            json={"title": "Second chat"},
        )
        assert second.status_code == 201, second.text
        assert second.json()["conversation_id"] != alice_session["conversation_id"]

        listing = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/sessions",
            headers=builder_alice.headers,
        )
        assert listing.status_code == 200, listing.text
        ids = [s["id"] for s in listing.json()["sessions"]]
        assert ids[0] == second.json()["id"]
        assert alice_session["id"] in ids

    def test_scaffold_revision_is_current_and_downloadable(
        self, e2e_client, builder_alice, alice_solution
    ):
        resp = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions",
            headers=builder_alice.headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1, body
        revision = body["revisions"][0]
        assert revision["is_current"] is True
        assert revision["is_deployed"] is False
        assert revision["parent_revision_id"] is None
        assert len(revision["source_sha256"]) == 64

        download = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions/{revision['id']}/download",
            headers=builder_alice.headers,
        )
        assert download.status_code == 200, download.text
        assert download.headers["content-type"] == "application/zip"
        assert hashlib.sha256(download.content).hexdigest() == revision["source_sha256"]
        archive = zipfile.ZipFile(io.BytesIO(download.content))
        assert archive.testzip() is None
        assert "bifrost.solution.yaml" in archive.namelist()

    def test_owner_can_browse_source_and_diff(
        self, e2e_client, builder_alice, alice_solution
    ):
        revision = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions",
            headers=builder_alice.headers,
        ).json()["revisions"][0]
        revision_url = f"{BUILDER_URL}/{alice_solution['id']}/revisions/{revision['id']}"

        files = e2e_client.get(f"{revision_url}/files", headers=builder_alice.headers)
        assert files.status_code == 200, files.text
        paths = {item["path"] for item in files.json()["files"]}
        assert "bifrost.solution.yaml" in paths

        source = e2e_client.get(
            f"{revision_url}/file",
            params={"path": "bifrost.solution.yaml"},
            headers=builder_alice.headers,
        )
        assert source.status_code == 200, source.text
        assert source.json()["encoding"] == "utf-8"
        assert alice_solution["slug"] in source.json()["content"]

        diff = e2e_client.get(f"{revision_url}/diff", headers=builder_alice.headers)
        assert diff.status_code == 200, diff.text
        assert diff.json()["against_revision_id"] is None
        assert {item["path"] for item in diff.json()["files"]} == paths


class TestPromotionGate:
    def test_unbuilt_revision_cannot_request_promotion(
        self, e2e_client, builder_alice, alice_solution
    ):
        resp = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/promotion-request",
            headers=builder_alice.headers,
        )
        assert resp.status_code == 409, resp.text

    def test_non_owner_cannot_request_promotion(
        self, e2e_client, builder_bob, alice_solution
    ):
        resp = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/promotion-request",
            headers=builder_bob.headers,
        )
        assert resp.status_code == 404, resp.text

    async def test_admin_promotes_pinned_green_revision_to_separate_release(
        self, e2e_client, db_session, builder_alice, platform_admin
    ):
        from src.models.orm.solution_builder import (
            SolutionBuilderProject,
            SolutionBuilderTurn,
            SolutionSourceRevision,
        )
        from src.models.orm.solution_deploy_jobs import SolutionDeployJob

        slug = _slug("promote")
        created = _create(e2e_client, builder_alice.headers, slug)
        assert created.status_code == 201, created.text
        source_solution = created.json()
        source_solution_id = UUID(source_solution["id"])
        published_id: str | None = None
        try:
            session_response = e2e_client.post(
                f"{BUILDER_URL}/{source_solution_id}/sessions",
                headers=builder_alice.headers,
                json={"title": "Promotion review"},
            )
            assert session_response.status_code == 201, session_response.text
            session_id = UUID(session_response.json()["id"])

            project = await db_session.get(SolutionBuilderProject, source_solution_id)
            assert project is not None and project.current_revision_id is not None
            revision = await db_session.get(
                SolutionSourceRevision, project.current_revision_id
            )
            assert revision is not None
            deploy = SolutionDeployJob(
                install_id=source_solution_id,
                status="succeeded",
                result={"roles_unresolved": [], "build_job_ids": []},
            )
            db_session.add(deploy)
            await db_session.flush()
            db_session.add(
                SolutionBuilderTurn(
                    session_id=session_id,
                    requested_by=builder_alice.user_id,
                    base_revision_id=revision.id,
                    output_revision_id=revision.id,
                    deploy_job_id=deploy.id,
                    status="succeeded",
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                )
            )
            project.deployed_revision_id = revision.id
            await db_session.commit()

            requested = e2e_client.post(
                f"{BUILDER_URL}/{source_solution_id}/promotion-request",
                headers=builder_alice.headers,
            )
            assert requested.status_code == 200, requested.text
            assert requested.json()["promotion_revision_id"] == str(revision.id)

            review = e2e_client.get(
                f"/api/solution-promotions/{source_solution_id}",
                headers=platform_admin.headers,
            )
            assert review.status_code == 200, review.text
            assert review.json()["ready"] is True
            assert review.json()["pinned_revision_id"] == str(revision.id)
            assert review.json()["source_sha256"] == revision.source_sha256

            promoted = e2e_client.post(
                f"/api/solution-promotions/{source_solution_id}/promote",
                headers=platform_admin.headers,
                json={
                    "target": "company",
                    "runtime_mode": "isolated",
                    "approve_role_creation": True,
                    "approved_connection_names": review.json()["connection_names"],
                },
                timeout=120,
            )
            assert promoted.status_code == 200, promoted.text
            result = promoted.json()
            published_id = result["published_solution_id"]
            assert result["solution_id"] == str(source_solution_id)
            assert result["visibility"] == "shared"
            assert result["promoted_revision_id"] == str(revision.id)
            assert published_id != str(source_solution_id)

            owner_private = e2e_client.get(
                f"{BUILDER_URL}/{source_solution_id}",
                headers=builder_alice.headers,
            )
            assert owner_private.status_code == 200, owner_private.text
            admin_shared = e2e_client.get(
                f"/api/solutions/{published_id}",
                headers=platform_admin.headers,
            )
            assert admin_shared.status_code == 200, admin_shared.text
            assert admin_shared.json()["id"] == published_id
        finally:
            e2e_client.delete(
                f"{BUILDER_URL}/{source_solution_id}",
                headers=builder_alice.headers,
            )
            if published_id is not None:
                e2e_client.delete(
                    f"/api/solutions/{published_id}",
                    headers=platform_admin.headers,
                    params={"confirm": slug},
                )


class TestNonOwnerCannotReachBuilderRoutes:
    def test_all_routes_404_for_another_builder(
        self,
        e2e_client,
        builder_bob,
        builder_alice,
        alice_solution,
        alice_session,
    ):
        revision_id = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions",
            headers=builder_alice.headers,
        ).json()["revisions"][0]["id"]
        base = f"{BUILDER_URL}/{alice_solution['id']}"
        probes = [
            e2e_client.post(
                f"{base}/sessions", headers=builder_bob.headers, json={"title": "nope"}
            ),
            e2e_client.get(f"{base}/sessions", headers=builder_bob.headers),
            e2e_client.get(f"{base}/revisions", headers=builder_bob.headers),
            e2e_client.get(
                f"{base}/revisions/{revision_id}/download",
                headers=builder_bob.headers,
            ),
            e2e_client.get(
                f"{base}/revisions/{revision_id}/files",
                headers=builder_bob.headers,
            ),
            e2e_client.get(f"{base}/turns", headers=builder_bob.headers),
            e2e_client.post(
                f"{base}/undo",
                headers=builder_bob.headers,
                json={"to_revision_id": revision_id, "session_id": alice_session["id"]},
            ),
            e2e_client.delete(f"{base}", headers=builder_bob.headers),
        ]
        assert [probe.status_code for probe in probes] == [404] * len(probes)
