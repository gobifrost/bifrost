"""E2E tests for the private builder Solution surface (/api/builder/solutions).

This is the first non-superuser Solution surface, so the tests are written
against the two invariants that make it safe: the ``solutions.build`` capability
gate (403 when absent) and the private-access invariant — a private Solution is
**invisible**, so a non-owner, including a platform admin, gets 404 rather than
403 and never sees the row in a list.
"""

import hashlib
import io
import uuid
import zipfile
from datetime import datetime, timezone

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
    """A role carrying the solutions.build permission."""
    resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={
            "name": f"E2E Builder {uuid.uuid4().hex[:8]}",
            "description": "private solution builder e2e",
            "scopes": ["solutions.build"],
        },
    )
    assert resp.status_code == 201, resp.text
    role = resp.json()
    yield role
    e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def builder_alice(e2e_client, platform_admin, builder_role, alice_user):
    """Alice, granted solutions.build for the duration of this module."""
    resp = e2e_client.post(
        f"/api/roles/{builder_role['id']}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(alice_user.user_id)]},
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
    """One private Solution owned by Alice, cleaned up afterwards."""
    resp = _create(e2e_client, builder_alice.headers, _slug())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    yield body
    e2e_client.delete(f"{BUILDER_URL}/{body['id']}", headers=builder_alice.headers)


@pytest.mark.e2e
class TestBuilderCapabilityGate:

    def test_user_without_permission_cannot_create(self, e2e_client, bob_user):
        """Bob holds no solutions.build role → 403, not 404."""
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
        assert body["slug"] == slug
        assert body["visibility"] == "private"
        assert body["owner_user_id"] == str(builder_alice.user_id)
        assert body["organization_id"] == str(builder_alice.organization_id)
        assert body["status"] == "active"
        assert body["promotion_status"] == "none"
        e2e_client.delete(f"{BUILDER_URL}/{body['id']}", headers=builder_alice.headers)


@pytest.mark.e2e
class TestPrivateInvisibility:

    def test_owner_sees_own_solution(self, e2e_client, builder_alice, alice_solution):
        detail = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}", headers=builder_alice.headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == alice_solution["id"]

        listing = e2e_client.get(BUILDER_URL, headers=builder_alice.headers)
        assert listing.status_code == 200, listing.text
        assert listing.json()["is_platform_admin"] is False
        assert alice_solution["id"] in {s["id"] for s in listing.json()["solutions"]}

    def test_another_builder_gets_404_and_empty_list(
        self, e2e_client, platform_admin, builder_role, bob_user, alice_solution
    ):
        """A second permitted user cannot see, get, or delete Alice's Solution."""
        grant = e2e_client.post(
            f"/api/roles/{builder_role['id']}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(bob_user.user_id)]},
        )
        assert grant.status_code == 204, grant.text
        _login_user(e2e_client, bob_user)
        try:
            detail = e2e_client.get(
                f"{BUILDER_URL}/{alice_solution['id']}", headers=bob_user.headers
            )
            assert detail.status_code == 404, detail.text

            listing = e2e_client.get(BUILDER_URL, headers=bob_user.headers)
            assert listing.status_code == 200, listing.text
            assert alice_solution["id"] not in {
                s["id"] for s in listing.json()["solutions"]
            }

            deleted = e2e_client.delete(
                f"{BUILDER_URL}/{alice_solution['id']}", headers=bob_user.headers
            )
            assert deleted.status_code == 404, deleted.text
        finally:
            e2e_client.delete(
                f"/api/roles/{builder_role['id']}/users/{bob_user.user_id}",
                headers=platform_admin.headers,
            )
            _login_user(e2e_client, bob_user)

    def test_platform_admin_does_not_see_it_here(
        self, e2e_client, platform_admin, alice_solution
    ):
        """Private installs stay out of the admin catalog on this surface."""
        listing = e2e_client.get(BUILDER_URL, headers=platform_admin.headers)
        assert listing.status_code == 200, listing.text
        assert listing.json()["is_platform_admin"] is True
        assert alice_solution["id"] not in {s["id"] for s in listing.json()["solutions"]}

        detail = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}", headers=platform_admin.headers
        )
        assert detail.status_code == 404, detail.text

    def test_platform_admin_standard_catalog_does_not_see_private_solution(
        self, e2e_client, platform_admin, alice_solution
    ):
        """The legacy admin surface is not an implicit private-content bypass."""
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

        entities = e2e_client.get(
            f"/api/solutions/{alice_solution['id']}/entities",
            headers=platform_admin.headers,
        )
        assert entities.status_code == 404, entities.text


@pytest.mark.e2e
class TestSlugIdentity:

    def test_two_owners_may_share_a_slug_in_one_org(
        self, e2e_client, platform_admin, builder_role, builder_alice, bob_user
    ):
        """Private uniqueness is (owner, slug), so Bob may reuse Alice's slug."""
        grant = e2e_client.post(
            f"/api/roles/{builder_role['id']}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(bob_user.user_id)]},
        )
        assert grant.status_code == 204, grant.text
        _login_user(e2e_client, bob_user)
        slug = _slug("shared-slug")
        alice_resp = _create(e2e_client, builder_alice.headers, slug)
        assert alice_resp.status_code == 201, alice_resp.text
        bob_resp = _create(e2e_client, bob_user.headers, slug)
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
                    f"{BUILDER_URL}/{bob_resp.json()['id']}", headers=bob_user.headers
                )
            e2e_client.delete(
                f"/api/roles/{builder_role['id']}/users/{bob_user.user_id}",
                headers=platform_admin.headers,
            )
            _login_user(e2e_client, bob_user)

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


@pytest.mark.e2e
class TestPromotionAndDelete:

    async def test_owner_can_request_promotion(
        self, e2e_client, db_session, builder_alice, alice_solution
    ):
        from src.models.orm.solution_builder import SolutionBuilderProject

        project = await db_session.get(
            SolutionBuilderProject,
            uuid.UUID(alice_solution["id"]),
        )
        assert project is not None
        project.deployed_revision_id = project.current_revision_id
        await db_session.commit()

        resp = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/promotion-request",
            headers=builder_alice.headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["promotion_status"] == "requested"

        detail = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}", headers=builder_alice.headers
        )
        assert detail.json()["promotion_status"] == "requested"

    def test_unbuilt_revision_cannot_request_promotion(
        self, e2e_client, builder_alice, alice_solution
    ):
        resp = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/promotion-request",
            headers=builder_alice.headers,
        )
        assert resp.status_code == 409, resp.text

    def test_non_owner_cannot_request_promotion(
        self, e2e_client, bob_user, alice_solution
    ):
        resp = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/promotion-request",
            headers=bob_user.headers,
        )
        # Bob has no builder permission here, so the capability gate answers first.
        assert resp.status_code == 403, resp.text

    def test_owner_can_delete(self, e2e_client, builder_alice):
        created = _create(e2e_client, builder_alice.headers, _slug("delete"))
        assert created.status_code == 201, created.text
        solution_id = created.json()["id"]

        deleted = e2e_client.delete(
            f"{BUILDER_URL}/{solution_id}", headers=builder_alice.headers
        )
        assert deleted.status_code == 204, deleted.text

        gone = e2e_client.get(
            f"{BUILDER_URL}/{solution_id}", headers=builder_alice.headers
        )
        assert gone.status_code == 404, gone.text

    async def test_admin_promotes_the_exact_green_revision_to_company(
        self,
        e2e_client,
        db_session,
        builder_alice,
        platform_admin,
    ):
        from src.models.orm.solution_build_jobs import SolutionBuildJob
        from src.models.orm.solution_builder import (
            SolutionBuilderProject,
            SolutionBuilderTurn,
            SolutionSourceRevision,
        )
        from src.models.orm.solution_deploy_jobs import SolutionDeployJob

        slug = _slug("promote")
        created = _create(e2e_client, builder_alice.headers, slug)
        assert created.status_code == 201, created.text
        solution = created.json()
        solution_id = uuid.UUID(solution["id"])
        try:
            session_response = e2e_client.post(
                f"{BUILDER_URL}/{solution_id}/sessions",
                headers=builder_alice.headers,
                json={"title": "Promotion review"},
            )
            assert session_response.status_code == 201, session_response.text
            session_id = uuid.UUID(session_response.json()["id"])

            project = await db_session.get(SolutionBuilderProject, solution_id)
            assert project is not None and project.current_revision_id is not None
            revision = await db_session.get(
                SolutionSourceRevision,
                project.current_revision_id,
            )
            assert revision is not None
            build = SolutionBuildJob(
                solution_id=solution_id,
                source_revision_id=revision.id,
                requested_by=builder_alice.user_id,
                source_sha256=revision.source_sha256,
                toolchain_version="e2e-reviewed",
                status="succeeded",
                completed_at=datetime.now(timezone.utc),
            )
            deploy = SolutionDeployJob(
                install_id=solution_id,
                status="succeeded",
                kind="deploy",
                result={"roles_unresolved": [], "build_job_ids": []},
            )
            db_session.add_all([build, deploy])
            await db_session.flush()
            db_session.add(
                SolutionBuilderTurn(
                    session_id=session_id,
                    requested_by=builder_alice.user_id,
                    base_revision_id=revision.id,
                    output_revision_id=revision.id,
                    build_job_id=build.id,
                    deploy_job_id=deploy.id,
                    status="succeeded",
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                )
            )
            project.deployed_revision_id = revision.id
            await db_session.commit()

            requested = e2e_client.post(
                f"{BUILDER_URL}/{solution_id}/promotion-request",
                headers=builder_alice.headers,
            )
            assert requested.status_code == 200, requested.text
            assert requested.json()["promotion_revision_id"] == str(revision.id)

            review = e2e_client.get(
                f"/api/solution-promotions/{solution_id}",
                headers=platform_admin.headers,
            )
            assert review.status_code == 200, review.text
            assert review.json()["ready"] is True
            assert review.json()["pinned_revision_id"] == str(revision.id)
            assert review.json()["source_sha256"] == revision.source_sha256

            promoted = e2e_client.post(
                f"/api/solution-promotions/{solution_id}/promote",
                headers=platform_admin.headers,
                json={
                    "target": "company",
                    "approve_role_creation": True,
                    "approved_connection_names": review.json()[
                        "connection_names"
                    ],
                },
                timeout=120,
            )
            assert promoted.status_code == 200, promoted.text
            assert promoted.json()["visibility"] == "shared"
            assert promoted.json()["promoted_revision_id"] == str(revision.id)

            owner_private = e2e_client.get(
                f"{BUILDER_URL}/{solution_id}",
                headers=builder_alice.headers,
            )
            assert owner_private.status_code == 404
            admin_shared = e2e_client.get(
                f"/api/solutions/{solution_id}",
                headers=platform_admin.headers,
            )
            assert admin_shared.status_code == 200, admin_shared.text
        finally:
            e2e_client.delete(
                f"/api/solutions/{solution_id}",
                headers=platform_admin.headers,
                params={"confirm": slug},
            )


@pytest.fixture
def alice_session(e2e_client, builder_alice, alice_solution):
    """One builder chat session on Alice's Solution."""
    resp = e2e_client.post(
        f"{BUILDER_URL}/{alice_solution['id']}/sessions",
        headers=builder_alice.headers,
        json={"title": "Build me a thing"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.e2e
class TestBuilderSessions:

    def test_create_returns_a_session_bound_to_owner_and_solution(
        self, builder_alice, alice_solution, alice_session
    ):
        assert alice_session["solution_id"] == alice_solution["id"]
        assert alice_session["user_id"] == str(builder_alice.user_id)
        assert alice_session["conversation_id"]

    def test_list_returns_sessions_newest_first(
        self, e2e_client, builder_alice, alice_solution, alice_session
    ):
        second = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/sessions",
            headers=builder_alice.headers,
            json={"title": "Second chat"},
        )
        assert second.status_code == 201, second.text

        listing = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/sessions",
            headers=builder_alice.headers,
        )
        assert listing.status_code == 200, listing.text
        body = listing.json()
        ids = [s["id"] for s in body["sessions"]]
        assert body["total"] == len(ids)
        assert alice_session["id"] in ids
        assert ids[0] == second.json()["id"], "newest session should sort first"

    def test_each_session_gets_its_own_conversation(
        self, e2e_client, builder_alice, alice_solution, alice_session
    ):
        """Multiple chats per Solution (spec, "Multiple chats")."""
        second = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/sessions",
            headers=builder_alice.headers,
            json={"title": "Another angle"},
        )
        assert second.status_code == 201, second.text
        assert (
            second.json()["conversation_id"] != alice_session["conversation_id"]
        )


@pytest.mark.e2e
class TestSourceRevisions:

    def test_scaffold_revision_is_current(
        self, e2e_client, builder_alice, alice_solution
    ):
        """Creating a Solution scaffolds revision 0 and points current at it.

        This is the end-to-end proof that ``POST /api/builder/solutions`` wires
        the builder project through ``BuilderTurnService.create_project``: a
        brand-new Solution has exactly one revision, it is current, nothing is
        deployed yet, and the bytes behind it hash to the recorded digest.
        """
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
        assert revision["restored_from_revision_id"] is None
        assert revision["size_bytes"] > 0
        assert len(revision["source_sha256"]) == 64

        # The stored zip is the content the row is addressed by.
        download = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions/{revision['id']}/download",
            headers=builder_alice.headers,
        )
        assert download.status_code == 200, download.text
        assert hashlib.sha256(download.content).hexdigest() == revision["source_sha256"]
        assert len(download.content) == revision["size_bytes"]

    def test_download_returns_the_revision_zip(
        self, e2e_client, builder_alice, alice_solution
    ):
        listing = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions",
            headers=builder_alice.headers,
        )
        revision = listing.json()["revisions"][0]

        resp = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions/{revision['id']}/download",
            headers=builder_alice.headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/zip"
        assert "attachment" in resp.headers["content-disposition"]

        # The bytes served must be exactly the content the row is addressed by.
        assert hashlib.sha256(resp.content).hexdigest() == revision["source_sha256"]

        archive = zipfile.ZipFile(io.BytesIO(resp.content))
        assert archive.testzip() is None
        assert "bifrost.solution.yaml" in archive.namelist()

    def test_owner_can_browse_source_and_review_the_revision_diff(
        self, e2e_client, builder_alice, alice_solution
    ):
        listing = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions",
            headers=builder_alice.headers,
        )
        revision = listing.json()["revisions"][0]
        revision_url = (
            f"{BUILDER_URL}/{alice_solution['id']}/revisions/{revision['id']}"
        )

        files = e2e_client.get(
            f"{revision_url}/files",
            headers=builder_alice.headers,
        )
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

        diff = e2e_client.get(
            f"{revision_url}/diff",
            headers=builder_alice.headers,
        )
        assert diff.status_code == 200, diff.text
        body = diff.json()
        assert body["against_revision_id"] is None
        assert body["total"] == len(paths)
        assert {
            item["path"] for item in body["files"] if item["status"] == "added"
        } == paths

    def test_download_of_another_solutions_revision_is_404(
        self, e2e_client, builder_alice, alice_solution
    ):
        """A revision id is never honored outside the Solution that owns it."""
        other = _create(e2e_client, builder_alice.headers, _slug("other"))
        assert other.status_code == 201, other.text
        other_id = other.json()["id"]
        try:
            other_revisions = e2e_client.get(
                f"{BUILDER_URL}/{other_id}/revisions", headers=builder_alice.headers
            )
            foreign_revision_id = other_revisions.json()["revisions"][0]["id"]

            resp = e2e_client.get(
                f"{BUILDER_URL}/{alice_solution['id']}"
                f"/revisions/{foreign_revision_id}/download",
                headers=builder_alice.headers,
            )
            assert resp.status_code == 404, resp.text
        finally:
            e2e_client.delete(
                f"{BUILDER_URL}/{other_id}", headers=builder_alice.headers
            )


@pytest.mark.e2e
class TestUndoAndTurns:

    def test_undo_to_the_current_revision_succeeds_without_new_history(
        self, e2e_client, builder_alice, alice_solution, alice_session
    ):
        """Undo runs the full turn machinery over HTTP.

        Restoring an *older* revision is what stamps
        ``restored_from_revision_id``; that lineage is covered in
        ``tests/unit/test_builder_turns.py`` because reaching a second revision
        needs a mutating turn, which has no REST route yet. What this asserts is
        the part only the route can prove: the request authorizes, runs the turn,
        and serializes the result.
        """
        before = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions",
            headers=builder_alice.headers,
        ).json()
        scaffold = before["revisions"][0]

        resp = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/undo",
            headers=builder_alice.headers,
            json={
                "to_revision_id": scaffold["id"],
                "session_id": alice_session["id"],
            },
        )
        assert resp.status_code == 200, resp.text
        turn = resp.json()
        assert turn["status"] == "succeeded"
        assert turn["session_id"] == alice_session["id"]
        assert turn["base_revision_id"] == scaffold["id"]
        assert turn["requested_by"] == str(builder_alice.user_id)

        # Restoring the revision that is already current reproduces identical
        # content, so the turn publishes no new revision and history is unchanged.
        assert turn["output_revision_id"] == scaffold["id"]
        after = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions",
            headers=builder_alice.headers,
        ).json()
        assert after["total"] == before["total"]

    def test_turns_list_reflects_the_undo(
        self, e2e_client, builder_alice, alice_solution, alice_session
    ):
        revisions = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/revisions",
            headers=builder_alice.headers,
        ).json()
        scaffold_id = revisions["revisions"][0]["id"]

        undo = e2e_client.post(
            f"{BUILDER_URL}/{alice_solution['id']}/undo",
            headers=builder_alice.headers,
            json={"to_revision_id": scaffold_id, "session_id": alice_session["id"]},
        )
        assert undo.status_code == 200, undo.text

        listing = e2e_client.get(
            f"{BUILDER_URL}/{alice_solution['id']}/turns",
            headers=builder_alice.headers,
        )
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["total"] >= 1
        assert undo.json()["id"] in {t["id"] for t in body["turns"]}

    def test_undo_to_a_foreign_revision_is_404(
        self, e2e_client, builder_alice, alice_solution, alice_session
    ):
        other = _create(e2e_client, builder_alice.headers, _slug("foreign"))
        assert other.status_code == 201, other.text
        other_id = other.json()["id"]
        try:
            foreign_revision_id = (
                e2e_client.get(
                    f"{BUILDER_URL}/{other_id}/revisions",
                    headers=builder_alice.headers,
                )
                .json()["revisions"][0]["id"]
            )
            resp = e2e_client.post(
                f"{BUILDER_URL}/{alice_solution['id']}/undo",
                headers=builder_alice.headers,
                json={
                    "to_revision_id": foreign_revision_id,
                    "session_id": alice_session["id"],
                },
            )
            assert resp.status_code == 404, resp.text
        finally:
            e2e_client.delete(
                f"{BUILDER_URL}/{other_id}", headers=builder_alice.headers
            )


@pytest.mark.e2e
class TestNonOwnerCannotReachBuilderRoutes:
    """Every new route answers 404 for a permitted non-owner.

    Bob is granted solutions.build here on purpose: without the permission the
    capability gate would answer 403 first and the test would prove nothing
    about the private-access invariant.
    """

    def test_all_routes_404_for_another_builder(
        self,
        e2e_client,
        platform_admin,
        builder_role,
        bob_user,
        builder_alice,
        alice_solution,
        alice_session,
    ):
        revision_id = (
            e2e_client.get(
                f"{BUILDER_URL}/{alice_solution['id']}/revisions",
                headers=builder_alice.headers,
            )
            .json()["revisions"][0]["id"]
        )
        grant = e2e_client.post(
            f"/api/roles/{builder_role['id']}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(bob_user.user_id)]},
        )
        assert grant.status_code == 204, grant.text
        _login_user(e2e_client, bob_user)
        base = f"{BUILDER_URL}/{alice_solution['id']}"
        try:
            probes = [
                e2e_client.post(
                    f"{base}/sessions", headers=bob_user.headers, json={"title": "nope"}
                ),
                e2e_client.get(f"{base}/sessions", headers=bob_user.headers),
                e2e_client.get(f"{base}/revisions", headers=bob_user.headers),
                e2e_client.get(
                    f"{base}/revisions/{revision_id}/download",
                    headers=bob_user.headers,
                ),
                e2e_client.post(
                    f"{base}/undo",
                    headers=bob_user.headers,
                    json={
                        "to_revision_id": revision_id,
                        "session_id": alice_session["id"],
                    },
                ),
                e2e_client.get(f"{base}/turns", headers=bob_user.headers),
            ]
            for probe in probes:
                assert probe.status_code == 404, (
                    f"{probe.request.method} {probe.request.url} "
                    f"→ {probe.status_code}: {probe.text}"
                )
        finally:
            e2e_client.delete(
                f"/api/roles/{builder_role['id']}/users/{bob_user.user_id}",
                headers=platform_admin.headers,
            )
            _login_user(e2e_client, bob_user)
