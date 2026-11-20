"""Disconnect transaction boundaries against PostgreSQL; no GitHub credentials required."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from src.core.database import get_db_context
from src.routers import github
from src.services.github_config import delete_github_config, get_github_config, save_github_config

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.parametrize("scrub_fails", [False, True])
async def test_disconnect_releases_request_transaction_before_scrub(monkeypatch, scrub_fails):
    """Keep config until scrub succeeds, without holding the authorization transaction."""
    async with get_db_context() as setup_db:
        await save_github_config(
            setup_db, None, token="synthetic-disconnect-test-token",
            repo_url="https://github.com/example/disconnect-test.git",
            branch="main", updated_by="disconnect-regression",
        )
    try:
        async with get_db_context() as request_db:
            # Authentication/authorization normally checks out this connection.
            pid = await request_db.scalar(text("SELECT pg_backend_pid()"))
            assert request_db.in_transaction()

            async def scrub():
                assert not request_db.in_transaction()
                async with get_db_context() as observer:
                    state = await observer.scalar(
                        text("SELECT state FROM pg_stat_activity WHERE pid = :pid"),
                        {"pid": pid},
                    )
                    assert state != "idle in transaction"
                    assert await get_github_config(observer, None) is not None
                if scrub_fails:
                    raise RuntimeError("synthetic scrub failure")

            async def delete_in_fresh_session(db, org_id):
                assert db is not request_db
                await delete_github_config(db, org_id)

            monkeypatch.setattr(github.GitRepoManager, "remove_stored_credentials", AsyncMock(side_effect=scrub))
            monkeypatch.setattr(github, "delete_github_config", delete_in_fresh_session)
            if scrub_fails:
                with pytest.raises(HTTPException) as error:
                    await github.disconnect_github(SimpleNamespace(org_id=None), None, request_db)
                assert error.value.status_code == 500
                assert error.value.detail == "Failed to disconnect GitHub"
            else:
                result = await github.disconnect_github(SimpleNamespace(org_id=None), None, request_db)
                assert result == {"success": True, "message": "GitHub integration disconnected"}

        async with get_db_context() as verify_db:
            assert (await get_github_config(verify_db, None) is not None) == scrub_fails
    finally:
        async with get_db_context() as cleanup_db:
            await delete_github_config(cleanup_db, None)
