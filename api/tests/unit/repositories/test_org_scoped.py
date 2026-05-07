"""
Unit tests for OrgScopedRepository.

Covers the access-control gate shared by every org-scoped repository
(workflows, forms, agents, knowledge, tables). Regressions here affect
every entity type that goes through this repository, so the contract is
worth pinning down with direct tests.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.models import Workflow
from src.repositories.org_scoped import OrgScopedRepository


class _FakeRepo(OrgScopedRepository[Workflow]):
    """Repository stand-in that exercises only the access gate on the
    base class. Bound to `Workflow` to satisfy the `Base` type bound on
    `OrgScopedRepository[ModelT]`; tests inject MagicMock entities and
    never touch the model itself."""

    model = Workflow


@pytest.fixture
def session():
    s = AsyncMock()
    s.execute = AsyncMock()
    return s


def _entity(org_id: UUID | None, access_level: str = "authenticated"):
    """Build a fake ORM entity with the attributes the repo inspects."""
    e = MagicMock(spec=["id", "organization_id", "access_level"])
    e.id = uuid4()
    e.organization_id = org_id
    e.access_level = access_level
    return e


def _result_for(entity):
    """Wrap an entity in the AsyncMock execute().scalar_one_or_none() shape."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=entity)
    return result


class TestOrgScopedRepositoryInputCoercion:
    """JWT claims arrive as strings. `UUID == str` is False in Python, so
    a string `org_id` here silently fails the in-scope check on ID lookups.
    The repository must coerce string inputs at the boundary."""

    def test_string_org_id_coerced_to_uuid(self, session):
        org_uuid = uuid4()
        repo = _FakeRepo(session, org_id=str(org_uuid))
        assert repo.org_id == org_uuid
        assert isinstance(repo.org_id, UUID)

    def test_string_user_id_coerced_to_uuid(self, session):
        user_uuid = uuid4()
        repo = _FakeRepo(session, org_id=None, user_id=str(user_uuid))
        assert repo.user_id == user_uuid
        assert isinstance(repo.user_id, UUID)

    def test_uuid_inputs_passed_through(self, session):
        org_uuid, user_uuid = uuid4(), uuid4()
        repo = _FakeRepo(session, org_id=org_uuid, user_id=user_uuid)
        assert repo.org_id is org_uuid
        assert repo.user_id is user_uuid

    def test_none_inputs_remain_none(self, session):
        repo = _FakeRepo(session, org_id=None, user_id=None)
        assert repo.org_id is None
        assert repo.user_id is None

    async def test_id_lookup_with_string_org_id_finds_in_scope_entity(self, session):
        """Regression: MCPContext used to pass `org_id` as a string from
        JWT claims, causing every non-admin tool execution to return
        `None` ('Workflow not found') even when the workflow's org and
        the user's org matched. Coercion at __init__ fixes the gate."""
        org_uuid = uuid4()
        entity = _entity(org_id=org_uuid)
        session.execute.return_value = _result_for(entity)

        repo = _FakeRepo(session, org_id=str(org_uuid), user_id=uuid4())

        found = await repo.get(id=entity.id)

        assert found is entity, (
            "Org-scoped repo should resolve entity when string org_id "
            "matches entity's org UUID after coercion"
        )
