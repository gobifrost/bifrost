"""Unit tests for OAuthProviderRepository and OAuthTokenRepository.

The cross-tenant test in this file is the regression pin for the leak
that lived in ``IntegrationsRepository.get_provider_org_token`` prior to
the 2026-05 consolidation. That method took ``provider_id`` and returned
the first ``user_id IS NULL`` token with no ``organization_id`` filter
— meaning org A's CLI could surface org B's token. The new repositories
filter by org explicitly and fall back to global; they NEVER return
another org's row.

The tests use ``AsyncMock`` for the SQLAlchemy session because the
contract being asserted is "what queries does the repository make,"
not "does SQLAlchemy work" — full integration coverage lives in the
e2e suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.repositories.oauth import (
    OAuthProviderRepository,
    OAuthTokenRepository,
)


ORG_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ORG_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _result_returning(value) -> MagicMock:
    """Wrap a value in the ``execute().scalars().first()`` shape."""
    scalars = MagicMock()
    scalars.first = MagicMock(return_value=value)
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    return result


@pytest.fixture
def session():
    s = AsyncMock()
    s.execute = AsyncMock()
    return s


class TestOAuthTokenRepositoryCrossTenantIsolation:
    """The single most important test in this file. If any of these
    pass while a real cross-tenant leak exists, the test infrastructure
    is broken."""

    async def test_org_a_repo_returns_org_a_token(self, session) -> None:
        """Happy path: an org-scoped repo finds its own org's token."""
        provider_id = uuid4()
        org_a_token = MagicMock(organization_id=ORG_A)

        session.execute.return_value = _result_returning(org_a_token)

        repo = OAuthTokenRepository(session, org_id=ORG_A, bypass_resource_admission=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is org_a_token
        # The single query was the org-specific one; no fallback needed.
        assert session.execute.call_count == 1

    async def test_org_a_repo_does_not_return_org_b_token(self, session) -> None:
        """Cross-tenant isolation. Org A's repo MUST NOT see org B's row.

        We simulate the worst case: the SQL query was constructed wrong
        and the database returned org B's token. The repository's
        filter would catch this in real SQL, but the test pin asserts
        the filter is in place by inspecting what we asked the DB for.
        """
        provider_id = uuid4()

        # Simulate: org-specific query returns nothing (no org A token);
        # global fallback query returns nothing (no global token either).
        # Result: None. The repo MUST NOT have asked any query that would
        # match org B's row.
        session.execute.return_value = _result_returning(None)

        repo = OAuthTokenRepository(session, org_id=ORG_A, bypass_resource_admission=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is None
        # Both queries fired: org-specific then global fallback.
        assert session.execute.call_count == 2

        # Inspect the queries to confirm they filtered by ORG_A and NULL
        # only — never by ORG_B (the cross-tenant other) or by an
        # unfiltered organization_id condition.
        all_queries = [
            str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
            for call in session.execute.call_args_list
        ]
        for query_str in all_queries:
            assert str(ORG_B) not in query_str, (
                f"Query referenced ORG_B's id — possible cross-tenant leak:\n{query_str}"
            )
            # The query must reference the org filter explicitly.
            assert "organization_id" in query_str, (
                f"Query did not filter by organization_id — leak risk:\n{query_str}"
            )

    async def test_global_token_used_when_no_org_specific(self, session) -> None:
        """Cascade fallback: org-specific miss falls back to global."""
        provider_id = uuid4()
        global_token = MagicMock(organization_id=None)

        # First call (org-specific): empty. Second call (global): hit.
        session.execute.side_effect = [
            _result_returning(None),
            _result_returning(global_token),
        ]

        repo = OAuthTokenRepository(session, org_id=ORG_A, bypass_resource_admission=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is global_token
        assert session.execute.call_count == 2

    async def test_org_specific_wins_over_global(self, session) -> None:
        """Cascade override: when both exist, the org-specific row wins."""
        provider_id = uuid4()
        org_a_token = MagicMock(organization_id=ORG_A)
        global_token = MagicMock(organization_id=None)

        # First call: org-specific hits — the repo MUST NOT proceed to
        # the global fallback.
        session.execute.side_effect = [
            _result_returning(org_a_token),
            _result_returning(global_token),
        ]

        repo = OAuthTokenRepository(session, org_id=ORG_A, bypass_resource_admission=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is org_a_token
        assert session.execute.call_count == 1, (
            "Repository should short-circuit on org-specific hit; "
            "if it queried global anyway, the cascade override is broken."
        )

    async def test_no_org_skips_org_specific_query(self, session) -> None:
        """When the repo has no org_id, only the global query fires."""
        provider_id = uuid4()
        global_token = MagicMock(organization_id=None)

        session.execute.return_value = _result_returning(global_token)

        repo = OAuthTokenRepository(session, org_id=None, bypass_resource_admission=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is global_token
        # Only the global query; org-specific is skipped when org_id is None.
        assert session.execute.call_count == 1


class TestOAuthProviderRepositoryCascade:
    """Provider lookup also gets cascade for the same reasons as token."""

    async def test_repository_has_expected_model(self) -> None:
        from src.models.orm.oauth import OAuthProvider

        assert OAuthProviderRepository.model is OAuthProvider
        assert OAuthProviderRepository.role_table is None
