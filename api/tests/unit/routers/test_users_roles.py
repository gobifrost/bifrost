from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import sys
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.orm.role_assignments import RoleAssignment, RoleAssignmentBoundary
from src.services.authorization import AuthorizationBoundary


_MODULE_PATH = Path(__file__).resolve().parents[3] / "src" / "routers" / "users.py"
_SPEC = spec_from_file_location("users_router_under_test", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
users = module_from_spec(_SPEC)
sys.modules[_SPEC.name] = users
_SPEC.loader.exec_module(users)
get_user_role_assignments = users.get_user_role_assignments


@dataclass
class _Auth:
    can_read_roles: bool
    is_platform_admin: bool = False

    def __post_init__(self) -> None:
        self.calls: list[str] = []
        self.selected_boundary = AuthorizationBoundary.platform()

    def require_operation(self, operation_id: str) -> None:
        self.calls.append(operation_id)
        if not self.can_read_roles:
            raise HTTPException(status_code=403, detail="Missing required capability: roles.read")

    def has_capability(self, capability: str) -> bool:
        return self.is_platform_admin and capability == "platform.superuser"


class _Result:
    def __init__(
        self,
        rows: list[object] | None = None,
        scalar: object | None = None,
        row: object | None = None,
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self._row = row

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[object]:
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar

    def one_or_none(self):
        return self._row


class _Db:
    def __init__(
        self,
        *,
        user_id: object,
        user_org_id: object,
        assignments: list[object],
    ) -> None:
        self.user_id = user_id
        self.user_org_id = user_org_id
        self.assignments = assignments
        self.execute_calls: list[object] = []

    async def execute(self, query):
        self.execute_calls.append(query)
        text = str(query)
        if "FROM users" in text:
            return _Result(row=(self.user_id, self.user_org_id))
        return _Result(rows=self.assignments)


def _assignment() -> RoleAssignment:
    assignment = RoleAssignment(
        id=uuid4(),
        user_id=uuid4(),
        role_id=uuid4(),
        assigned_by_user_id=uuid4(),
        assigned_at=datetime.now(timezone.utc),
        boundaries=[
            RoleAssignmentBoundary(
                id=uuid4(),
                boundary_kind="organization",
                organization_id=uuid4(),
            ),
            RoleAssignmentBoundary(
                id=uuid4(),
                boundary_kind="organization_group",
                organization_group_id=uuid4(),
            ),
            RoleAssignmentBoundary(
                id=uuid4(),
                boundary_kind="managed_organizations",
            ),
            RoleAssignmentBoundary(
                id=uuid4(),
                boundary_kind="platform",
            ),
        ],
    )
    return assignment


@pytest.mark.asyncio
async def test_get_user_role_assignments_returns_canonical_boundary_records() -> None:
    assignment = _assignment()
    auth = _Auth(can_read_roles=True)
    user_org_id = uuid4()
    auth.selected_boundary = AuthorizationBoundary.organization(user_org_id)
    db = _Db(
        user_id=assignment.user_id,
        user_org_id=user_org_id,
        assignments=[assignment],
    )

    result = await get_user_role_assignments(
        user_id=str(assignment.user_id), authorization=auth, db=db
    )

    assert auth.calls == ["users.roles.list"]
    assert len(result) == 1
    returned = result[0]
    assert returned.role_id == assignment.role_id
    assert returned.assigned_by_user_id == assignment.assigned_by_user_id
    assert [boundary.boundary_kind for boundary in returned.boundaries] == [
        "organization",
        "organization_group",
        "managed_organizations",
        "platform",
    ]
    assert returned.boundaries[0].organization_id == assignment.boundaries[0].organization_id
    assert returned.boundaries[1].organization_group_id == assignment.boundaries[1].organization_group_id
    assert returned.boundaries[2].organization_id is None
    assert returned.boundaries[3].organization_group_id is None


@pytest.mark.asyncio
async def test_get_user_role_assignments_denies_cross_boundary_user_lookup() -> None:
    assignment = _assignment()
    auth = _Auth(can_read_roles=True)
    auth.selected_boundary = AuthorizationBoundary.organization(uuid4())
    db = _Db(
        user_id=assignment.user_id,
        user_org_id=uuid4(),
        assignments=[assignment],
    )

    with pytest.raises(HTTPException, match="User not found"):
        await get_user_role_assignments(
            user_id=str(assignment.user_id), authorization=auth, db=db
        )

    assert auth.calls == ["users.roles.list"]


@pytest.mark.asyncio
async def test_platform_admin_can_read_customer_assignments_from_home_boundary() -> None:
    assignment = _assignment()
    auth = _Auth(can_read_roles=True, is_platform_admin=True)
    auth.selected_boundary = AuthorizationBoundary.organization(uuid4())
    db = _Db(
        user_id=assignment.user_id,
        user_org_id=uuid4(),
        assignments=[assignment],
    )

    result = await get_user_role_assignments(
        user_id=str(assignment.user_id),
        authorization=auth,
        db=db,
    )

    assert len(result) == 1
    assert result[0].role_id == assignment.role_id


@pytest.mark.asyncio
async def test_get_user_role_assignments_denies_without_roles_read() -> None:
    auth = _Auth(can_read_roles=False)
    db = SimpleNamespace(execute=pytest.fail)

    with pytest.raises(HTTPException, match="Missing required capability: roles.read"):
        await get_user_role_assignments(
            user_id=str(uuid4()), authorization=auth, db=db
        )

    assert auth.calls == ["users.roles.list"]
