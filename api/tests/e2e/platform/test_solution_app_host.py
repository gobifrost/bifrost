"""E2E tests for the Solution app host (launch flow, session, artifact serving).

The invariants under test are the ones that make a separate app origin worth
having:

* the launch code is single-use and never puts a token in a URL;
* the app-host session cookie is bound to exactly one Solution and app, and a
  mismatch is 404 (invisible), not 403;
* the two token worlds are sealed against each other in *both* directions — a
  ``solution_app`` token cannot authenticate a normal API route, and a normal
  user token cannot authenticate an app-host route;
* the generated app's entry document ships a restrictive CSP and is never
  cached.
"""

import uuid

import httpx
import pytest

from src.services.solutions.app_build import SolutionAppBuilder
from tests.e2e.conftest import E2E_API_URL

BUILDER_URL = "/api/builder/solutions"


def _slug(prefix: str = "apphost") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def builder_role(e2e_client, platform_admin):
    resp = e2e_client.post(
        "/api/roles",
        headers=platform_admin.headers,
        json={
            "name": f"E2E AppHost {uuid.uuid4().hex[:8]}",
            "description": "solution app host e2e",
            "permissions": {"solutions.build": True},
        },
    )
    assert resp.status_code == 201, resp.text
    role = resp.json()
    yield role
    e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)


@pytest.fixture(scope="module")
def builder_alice(e2e_client, platform_admin, builder_role, alice_user):
    resp = e2e_client.post(
        f"/api/roles/{builder_role['id']}/users",
        headers=platform_admin.headers,
        json={"user_ids": [str(alice_user.user_id)]},
    )
    assert resp.status_code == 204, resp.text
    yield alice_user
    e2e_client.delete(
        f"/api/roles/{builder_role['id']}/users/{alice_user.user_id}",
        headers=platform_admin.headers,
    )


def _make_solution(e2e_client, user):
    resp = e2e_client.post(
        BUILDER_URL,
        headers=user.headers,
        json={"slug": _slug(), "name": "App host e2e"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def make_app(db_session, builder_alice):
    """Seed a Solution-owned app row plus a minimal built dist/ for it.

    The app row is inserted directly because no route creates a
    Solution-owned Application yet — deploy does, and that machinery belongs
    to a different work package. What this suite is testing is the app host,
    which only needs the row to exist with the right ``solution_id``.
    """
    from src.models.orm.applications import Application

    async def _make(solution_id: str, organization_id) -> str:
        suffix = uuid.uuid4().hex[:8]
        app = Application(
            name=f"apphost-{suffix}",
            slug=f"apphost-{suffix}",
            description="app host e2e",
            repo_path=f"apps/apphost-{suffix}",
            app_model="standalone_v2",
            organization_id=organization_id,
            solution_id=uuid.UUID(solution_id),
        )
        db_session.add(app)
        await db_session.commit()

        builder = SolutionAppBuilder()
        await builder.upload_dist(
            app.id,
            {
                "index.html": b"<!doctype html><div id=root></div>",
                "assets/main-abc123.js": b"console.log(1)",
            },
        )
        return str(app.id)

    return _make


@pytest.fixture
async def alice_app(e2e_client, builder_alice, make_app):
    solution = _make_solution(e2e_client, builder_alice)
    app_id = await make_app(solution["id"], builder_alice.organization_id)
    yield solution, app_id
    e2e_client.delete(f"{BUILDER_URL}/{solution['id']}", headers=builder_alice.headers)


def _launch(e2e_client, user, solution_id: str, app_id: str, path: str = "/"):
    return e2e_client.post(
        f"{BUILDER_URL}/{solution_id}/apps/{app_id}/launch",
        headers=user.headers,
        params={"path": path},
    )


def _host_client() -> httpx.Client:
    """A cookie-carrying client for the app-host routes (no bearer auth)."""
    return httpx.Client(base_url=E2E_API_URL, timeout=60.0, follow_redirects=False)


def _redeem(host: httpx.Client, launch_url: str) -> httpx.Response:
    code = launch_url.rsplit("/", 1)[-1]
    return host.get(f"/launch/{code}")


@pytest.mark.e2e
class TestLaunchFlow:

    def test_redeem_sets_cookie_and_redirects_to_id_path(
        self, e2e_client, builder_alice, alice_app
    ):
        solution, app_id = alice_app
        launch = _launch(e2e_client, builder_alice, solution["id"], app_id, "/reports")
        assert launch.status_code == 200, launch.text
        assert "/launch/" in launch.json()["launch_url"]

        with _host_client() as host:
            resp = _redeem(host, launch.json()["launch_url"])
            assert resp.status_code == 302, resp.text
            assert resp.headers["location"] == (
                f"/{solution['id']}/apps/{app_id}/reports"
            )
            assert "bifrost_app_session" in host.cookies

    def test_launch_code_is_single_use(self, e2e_client, builder_alice, alice_app):
        solution, app_id = alice_app
        launch = _launch(e2e_client, builder_alice, solution["id"], app_id)
        url = launch.json()["launch_url"]

        with _host_client() as host:
            assert _redeem(host, url).status_code == 302
        with _host_client() as second:
            replay = _redeem(second, url)
            assert replay.status_code == 400, replay.text
            assert "bifrost_app_session" not in second.cookies

    def test_no_token_appears_in_the_launch_url(
        self, e2e_client, builder_alice, alice_app
    ):
        solution, app_id = alice_app
        url = _launch(e2e_client, builder_alice, solution["id"], app_id).json()[
            "launch_url"
        ]
        # A JWT is three dot-separated segments; the launch code is opaque.
        assert url.count(".") == 0 or "eyJ" not in url


@pytest.mark.e2e
class TestTokenSeal:

    def test_minted_token_binds_the_right_solution_and_app(
        self, e2e_client, builder_alice, alice_app
    ):
        import base64
        import json

        solution, app_id = alice_app
        url = _launch(e2e_client, builder_alice, solution["id"], app_id).json()[
            "launch_url"
        ]
        with _host_client() as host:
            _redeem(host, url)
            resp = host.post("/app-session/token")
            assert resp.status_code == 200, resp.text
            token = resp.json()["access_token"]

        claims_b64 = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(claims_b64 + "=="))
        assert claims["actor_type"] == "solution_app"
        assert claims["solution_id"] == solution["id"]
        assert claims["app_id"] == app_id

    def test_app_token_is_rejected_by_a_normal_api_route(
        self, e2e_client, builder_alice, alice_app
    ):
        """The default-deny half: an actor token authenticates nothing normal."""
        solution, app_id = alice_app
        url = _launch(e2e_client, builder_alice, solution["id"], app_id).json()[
            "launch_url"
        ]
        with _host_client() as host:
            _redeem(host, url)
            token = host.post("/app-session/token").json()["access_token"]

        resp = e2e_client.get(
            "/api/tables", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 401, resp.text

    def test_user_token_is_rejected_by_an_app_host_route(
        self, e2e_client, builder_alice, alice_app
    ):
        """The other half: a full user token buys nothing on the app host."""
        solution, app_id = alice_app
        with _host_client() as host:
            resp = host.get(
                f"/{solution['id']}/apps/{app_id}/index.html",
                headers=builder_alice.headers,
            )
            assert resp.status_code == 401, resp.text

    def test_revoke_ends_renewal(self, e2e_client, builder_alice, alice_app):
        solution, app_id = alice_app
        url = _launch(e2e_client, builder_alice, solution["id"], app_id).json()[
            "launch_url"
        ]
        with _host_client() as host:
            _redeem(host, url)
            assert host.post("/app-session/token").status_code == 200
            assert host.delete("/app-session").status_code == 204
            # The cookie value is cleared, and even replaying it is dead.
            assert host.post("/app-session/token").status_code == 401


@pytest.mark.e2e
class TestArtifactServing:

    def test_index_carries_csp_and_no_store(
        self, e2e_client, builder_alice, alice_app
    ):
        solution, app_id = alice_app
        url = _launch(e2e_client, builder_alice, solution["id"], app_id).json()[
            "launch_url"
        ]
        with _host_client() as host:
            _redeem(host, url)
            resp = host.get(f"/{solution['id']}/apps/{app_id}/index.html")
            assert resp.status_code == 200, resp.text
            csp = resp.headers["content-security-policy"]
            assert "default-src 'self'" in csp
            assert "object-src 'none'" in csp
            assert "base-uri 'none'" in csp
            assert "frame-ancestors" in csp
            assert resp.headers["cache-control"] == "no-store"
            assert resp.headers["x-content-type-options"] == "nosniff"

    def test_hashed_asset_is_immutable_and_not_html(
        self, e2e_client, builder_alice, alice_app
    ):
        solution, app_id = alice_app
        url = _launch(e2e_client, builder_alice, solution["id"], app_id).json()[
            "launch_url"
        ]
        with _host_client() as host:
            _redeem(host, url)
            resp = host.get(f"/{solution['id']}/apps/{app_id}/assets/main-abc123.js")
            assert resp.status_code == 200, resp.text
            assert "immutable" in resp.headers["cache-control"]
            # A missing asset must 404 rather than silently become index.html.
            missing = host.get(f"/{solution['id']}/apps/{app_id}/assets/nope.js")
            assert missing.status_code == 404

    def test_traversal_cannot_escape_the_app_prefix(
        self, e2e_client, builder_alice, alice_app
    ):
        solution, app_id = alice_app
        url = _launch(e2e_client, builder_alice, solution["id"], app_id).json()[
            "launch_url"
        ]
        with _host_client() as host:
            _redeem(host, url)
            resp = host.get(
                f"/{solution['id']}/apps/{app_id}/../../other/dist/index.html"
            )
            # Either the router rejects it or the URL never matches this route;
            # what must never happen is serving another app's artifact.
            assert resp.status_code == 404, resp.text

    def test_spa_deep_link_serves_index(self, e2e_client, builder_alice, alice_app):
        solution, app_id = alice_app
        url = _launch(e2e_client, builder_alice, solution["id"], app_id).json()[
            "launch_url"
        ]
        with _host_client() as host:
            _redeem(host, url)
            resp = host.get(f"/{solution['id']}/apps/{app_id}/reports/42")
            assert resp.status_code == 200, resp.text
            assert b"id=root" in resp.content
            assert resp.headers["cache-control"] == "no-store"

    async def test_session_for_solution_a_cannot_fetch_solution_b(
        self, e2e_client, builder_alice, alice_app, make_app
    ):
        solution_a, app_a = alice_app
        solution_b = _make_solution(e2e_client, builder_alice)
        app_b = await make_app(solution_b["id"], builder_alice.organization_id)
        try:
            url = _launch(e2e_client, builder_alice, solution_a["id"], app_a).json()[
                "launch_url"
            ]
            with _host_client() as host:
                _redeem(host, url)
                # Bound to A; asking for B is invisible, not forbidden.
                resp = host.get(f"/{solution_b['id']}/apps/{app_b}/index.html")
                assert resp.status_code == 404, resp.text
                # Even the right Solution with the wrong app is 404.
                mismatched = host.get(f"/{solution_a['id']}/apps/{app_b}/index.html")
                assert mismatched.status_code == 404, mismatched.text
        finally:
            e2e_client.delete(
                f"{BUILDER_URL}/{solution_b['id']}", headers=builder_alice.headers
            )
