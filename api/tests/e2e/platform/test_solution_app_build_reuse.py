"""One direct deploy/redeploy journey plus the authenticated CLI preflight.

Zero queued jobs and one real Vite build: the second deploy proves reuse.
Real DB, S3 and HTTP lookup also prove the separate API-process artifact check.
"""

from uuid import uuid4
from time import perf_counter
import base64

import pytest
from sqlalchemy import delete

from src.models.orm.applications import Application
from src.models.orm.solutions import Solution
from src.services.solutions.app_build import SolutionAppBuilder
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployer,
    solution_entity_id,
)

pytestmark = pytest.mark.e2e


async def test_redeploy_preserves_build_and_preflight_verifies_all_artifacts(
    db_session,
    e2e_client,
    platform_admin,
    non_admin_user,
    monkeypatch,
    record_property,
):
    sol = Solution(
        id=uuid4(), slug=f"build-reuse-{uuid4().hex[:8]}", name="Build Reuse"
    )
    sol_id = sol.id
    manifest_id = uuid4()
    app_id = solution_entity_id(sol.id, manifest_id)
    db_session.add(sol)
    await db_session.commit()
    builds = []

    original_compile = SolutionAppBuilder.compile_dist

    def compile_dist(self, app_id, files, deps, prebuilt=None):
        builds.append(app_id)
        return original_compile(self, app_id, files, deps, prebuilt)

    monkeypatch.setattr(SolutionAppBuilder, "compile_dist", compile_dist)
    app = {
        "id": str(manifest_id),
        "slug": "dashboard",
        "name": "Dashboard",
        "app_model": "standalone_v2",
        "src_files": {"index.html": "<html>source</html>"},
        "bin_files": {"public/logo.bin": base64.b64encode(b"binary").decode()},
        "dependencies": {"vite": "^5.2.0"},
    }
    builder = SolutionAppBuilder()
    try:
        started = perf_counter()
        result = await SolutionDeployer(db_session).deploy(
            SolutionBundle(solution=sol, apps=[app])
        )
        await db_session.commit()
        await result.finalize_s3()
        record_property("initial_deploy_seconds", perf_counter() - started)
        row = await db_session.get(Application, app_id)
        pointer = row.active_deployment_id
        stamp = (row.deployed_at, row.sdk_built_at, row.sdk_fingerprint)
        identity = builder.source_identity(
            app_id,
            {"index.html": b"<html>source</html>", "public/logo.bin": b"binary"},
            app["dependencies"],
        )
        request = {"apps": [{"app_id": str(manifest_id), "fingerprint": identity}]}
        path = f"/api/solutions/{sol.id}/app-builds/lookup"
        response = e2e_client.post(path, json=request, headers=platform_admin.headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"matches": {str(manifest_id): str(pointer)}}
        assert e2e_client.post(path, json=request).status_code == 401
        assert (
            e2e_client.post(
                path, json=request, headers=non_admin_user.headers
            ).status_code
            == 403
        )

        app["name"] = "Updated Metadata"
        started = perf_counter()
        result = await SolutionDeployer(db_session).deploy(
            SolutionBundle(solution=sol, apps=[app])
        )
        await db_session.commit()
        await result.finalize_s3()
        await db_session.refresh(row)
        record_property("reused_deploy_seconds", perf_counter() - started)
        assert builds == [app_id]
        assert row.active_deployment_id == pointer
        assert (row.deployed_at, row.sdk_built_at, row.sdk_fingerprint) == stamp
        assert row.name == "Updated Metadata"
        async with builder._get_store() as store:
            await store.delete(builder._dist_key(app_id, "logo.bin", deployment_id=pointer))
        response = e2e_client.post(path, json=request, headers=platform_admin.headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"matches": {}}
    finally:
        await db_session.rollback()
        await builder.delete_all_app_artifacts(app_id)
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.commit()
