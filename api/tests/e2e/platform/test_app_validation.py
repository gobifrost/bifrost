"""E2E tests for app validation endpoint v1/v2 branching."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete


async def _make_app(db_session, org_id: str, app_model: str, files: dict[str, str]):
    """Create an Application ORM row + FileIndex entries, return the app id."""
    from src.models.orm.applications import Application
    from src.models.orm.file_index import FileIndex

    slug = f"validate-{app_model}-{uuid4().hex[:8]}"
    app = Application(
        id=uuid4(),
        name=slug,
        slug=slug,
        repo_path=f"apps/{slug}",
        organization_id=UUID(org_id),
        app_model=app_model,
    )
    db_session.add(app)
    await db_session.flush()

    prefix = app.repo_prefix
    for rel_path, content in files.items():
        db_session.add(
            FileIndex(
                path=f"{prefix}{rel_path}",
                content=content,
                updated_at=datetime.now(timezone.utc),
            )
        )
    await db_session.commit()
    return app.id


async def _delete_app(db_session, app_id):
    from src.models.orm.applications import Application
    from src.models.orm.file_index import FileIndex

    app = await db_session.get(Application, app_id)
    if app is not None:
        await db_session.execute(
            delete(FileIndex).where(FileIndex.path.startswith(app.repo_prefix))
        )
        await db_session.delete(app)
    await db_session.commit()


@pytest.mark.e2e
class TestAppValidationModelBranching:
    async def test_v2_app_skips_layout_check(
        self, db_session, e2e_client, platform_admin, org1
    ):
        """standalone_v2 apps must not be flagged for a missing _layout.tsx."""
        app_id = await _make_app(
            db_session,
            org1["id"],
            app_model="standalone_v2",
            files={"index.tsx": "export default () => null"},
        )
        try:
            resp = e2e_client.post(
                f"/api/applications/{app_id}/validate",
                headers=platform_admin.headers,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            all_issues = body["errors"] + body["warnings"]
            assert not any(
                i["file"] == "_layout.tsx" for i in all_issues
            ), f"v2 app should not require _layout.tsx, got: {all_issues}"
        finally:
            await _delete_app(db_session, app_id)

    async def test_v1_app_still_requires_layout(
        self, db_session, e2e_client, platform_admin, org1
    ):
        """inline_v1 apps still flag a missing _layout.tsx (regression guard)."""
        app_id = await _make_app(
            db_session,
            org1["id"],
            app_model="inline_v1",
            files={"pages/index.tsx": "export default () => null"},
        )
        try:
            resp = e2e_client.post(
                f"/api/applications/{app_id}/validate",
                headers=platform_admin.headers,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert any(
                i["file"] == "_layout.tsx" for i in body["errors"]
            ), "v1 app should still require _layout.tsx"
        finally:
            await _delete_app(db_session, app_id)
