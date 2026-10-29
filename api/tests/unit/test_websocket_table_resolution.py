"""Websocket table-name resolution must apply the canonical solution filters.

A `_repo/` table and a solution-deployed table may legally share (org, name) —
that's this branch's own design. The websocket name branch of
`_resolve_table_id` (and `_load_policies_for_table`) previously selected by
bare name, so the duplicate raised sqlalchemy MultipleResultsFound, which
propagated to the connection-level handler and killed the ENTIRE websocket.

Canonical semantics (mirror `OrgScopedRepository.get()`): by-name resolution is
the LIVE `_repo/` namespace — solution-managed rows resolve by id (or
?solution=).
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, update

from src.core.auth import UserPrincipal
from src.models.contracts.policies import TablePolicies

from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.routers import websocket as ws_mod

pytestmark = pytest.mark.e2e


@pytest.fixture
def patched_db(monkeypatch, db_session):
    """Route the websocket module's `get_db_context` to the test session.

    The helpers under test open their own session via `get_db_context`;
    pointing it at the (uncommitted, rolled-back) test session keeps seeded
    rows visible to them without committing anything.
    """

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(ws_mod, "get_db_context", _ctx)
    return db_session


async def _make_org(db) -> uuid.UUID:
    org = Organization(id=uuid.uuid4(), name=f"Org-{uuid.uuid4().hex[:8]}", created_by="dev@x")
    db.add(org)
    await db.flush()
    return org.id


async def _make_solution(db, org_id) -> uuid.UUID:
    solution = Solution(
        id=uuid.uuid4(),
        slug=f"sol-{uuid.uuid4().hex[:8]}",
        name="Test Solution",
        organization_id=org_id,
    )
    db.add(solution)
    await db.flush()
    return solution.id


def _table(name: str, *, org_id, solution_id=None, access=None) -> Table:
    return Table(
        id=uuid.uuid4(),
        name=name,
        organization_id=org_id,
        solution_id=solution_id,
        created_by="dev@x",
        access=access,
    )


def _principal(user: User) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        is_superuser=False,
    )


async def _org_user(db, org_id) -> UserPrincipal:
    user = User(
        email=f"ws-{uuid.uuid4().hex}@example.test",
        name="WebSocket user",
        organization_id=org_id,
    )
    db.add(user)
    await db.flush()
    return _principal(user)


async def _grant_at(db, user_id: uuid.UUID, org_id: uuid.UUID, permission: str) -> Role:
    role = Role(name=f"ws-role-{uuid.uuid4().hex[:8]}", created_by="websocket-test")
    db.add(role)
    await db.flush()
    db.add_all((
        RolePermission(role_id=role.id, permission=permission),
        UserRole(user_id=user_id, role_id=role.id, assigned_by="websocket-test"),
        UserRoleBoundary(
            user_id=user_id,
            role_id=role.id,
            kind="organization",
            organization_id=org_id,
        ),
    ))
    await db.flush()
    return role


class TestResolveTableIdByName:
    async def test_plain_repo_table_still_resolves(self, patched_db) -> None:
        """Happy-path regression guard: an ordinary `_repo/` table with no
        same-name siblings must keep resolving by name — an over-aggressive
        future filter change should fail here, not in production.
        """
        db = patched_db
        org = await _make_org(db)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        live = _table(name, org_id=org)
        db.add(live)
        await db.flush()

        assert await ws_mod._resolve_table_id(name, await _org_user(db, org)) == str(live.id)

    async def test_repo_row_wins_over_same_name_solution_row(self, patched_db) -> None:
        """A live `_repo/` table and a solution table share (org, name): the
        name lookup must return the `_repo/` row — not raise
        MultipleResultsFound (which killed the whole websocket).
        """
        db = patched_db
        org = await _make_org(db)
        solution_id = await _make_solution(db, org)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        live = _table(name, org_id=org)
        managed = _table(name, org_id=org, solution_id=solution_id)
        db.add_all([live, managed])
        await db.flush()

        resolved = await ws_mod._resolve_table_id(name, await _org_user(db, org))
        assert resolved == str(live.id)

class TestResolveTableIdById:
    """UUID subscribes follow the REST ``?scope=`` target-org rule."""

    async def _foreign_table(self, db) -> Table:
        table = _table(f"history_{uuid.uuid4().hex[:8]}", org_id=await _make_org(db))
        db.add(table)
        await db.flush()
        return table

    async def test_member_of_another_org_is_refused(self, patched_db) -> None:
        table = await self._foreign_table(patched_db)
        other_org = await _make_org(patched_db)

        assert await ws_mod._resolve_table_id(str(table.id), await _org_user(patched_db, other_org)) is None

    async def test_org_bounded_role_reaches_customer_table(self, patched_db) -> None:
        table = await self._foreign_table(patched_db)
        staff = await _org_user(patched_db, await _make_org(patched_db))
        await _grant_at(patched_db, staff.user_id, table.organization_id, "tables.read")

        assert await ws_mod._resolve_table_id(str(table.id), staff) == str(table.id)

    async def test_serialized_superuser_flag_cannot_widen_reach(self, patched_db) -> None:
        table = await self._foreign_table(patched_db)
        user = await _org_user(patched_db, await _make_org(patched_db))
        user.is_superuser = True

        assert await ws_mod._resolve_table_id(str(table.id), user) is None


class TestLoadPoliciesForTableByName:
    async def test_name_lookup_skips_solution_rows(self, patched_db) -> None:
        """Same-name `_repo/` + solution rows: the name branch must load the
        `_repo/` row's policies (empty here), not raise MultipleResultsFound
        and not read the solution row's policy document.
        """
        db = patched_db
        org = await _make_org(db)
        solution_id = await _make_solution(db, org)
        name = f"customers_{uuid.uuid4().hex[:8]}"

        live = _table(name, org_id=org, access=None)
        managed = _table(
            name,
            org_id=org,
            solution_id=solution_id,
            access={"policies": [{"name": "deny-all-marker", "actions": ["read"]}]},
        )
        db.add_all([live, managed])
        await db.flush()

        policies = await ws_mod._load_policies_for_table(name, await _org_user(db, org))
        assert policies == TablePolicies()


class TestFreshTablePolicyClaims:
    """Per-table evaluation must resolve custom claims afresh.

    Websocket principals live across tables and policy changes; custom claims
    are solution-scoped and mutable. Each evaluation therefore receives a
    shallow copy of the principal with an empty claims dict so that values
    resolved for one table never leak into another subscription's check.
    """

    def test_fresh_copy_drops_stale_claims_without_touching_principal(self) -> None:
        org = uuid.uuid4()
        user = _principal(User(id=uuid.uuid4(), email="claims@example.test", organization_id=org))
        user.claims = {"allowed_campus_ids": ["north"]}  # type: ignore[attr-defined]

        fresh = ws_mod._fresh_table_policy_user(user)

        assert fresh is not user
        assert fresh.claims == {}
        assert user.claims == {"allowed_campus_ids": ["north"]}

    async def test_handle_table_message_evaluates_with_fresh_claims(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mutating claims between two evaluations must not leak values."""
        org = uuid.uuid4()
        user = _principal(User(id=uuid.uuid4(), email="claims@example.test", organization_id=org))
        user.claims = {"allowed_campus_ids": ["north"]}  # type: ignore[attr-defined]
        seen: list = []

        async def fake_current(principal: UserPrincipal, table_id: str) -> UserPrincipal:
            policy_user = ws_mod._fresh_table_policy_user(principal)
            seen.append(policy_user)
            return policy_user

        async def fake_load(table_id: str, policy_user) -> TablePolicies:
            return TablePolicies.model_validate({
                "policies": [{"name": "open-read", "actions": ["read"]}],
            })

        monkeypatch.setattr(ws_mod, "_current_table_policy_user", fake_current)
        monkeypatch.setattr(ws_mod, "_load_policies_for_table", fake_load)
        websocket = SimpleNamespace(
            state=SimpleNamespace(table_subscriptions={"t1": {"filter": None}}),
            send_json=AsyncMock(),
        )

        await ws_mod._handle_table_message(
            websocket, user, "table:t1", {"type": "document_change"}
        )
        assert len(seen) == 1
        assert seen[0] is not user
        assert seen[0].claims == {}

        # Claims mutated after the first evaluation still start fresh.
        user.claims = {"allowed_campus_ids": ["south"]}
        await ws_mod._handle_table_message(
            websocket, user, "table:t1", {"type": "document_change"}
        )
        assert len(seen) == 2
        assert seen[1] is not user
        assert seen[1].claims == {}


class TestCurrentTableDeliveryAuthority:
    async def test_role_and_reach_revocation_unsubscribes_live_delivery(self, patched_db) -> None:
        """A connected socket must not retain a cached role or foreign-org reach."""
        db = patched_db
        home = await _make_org(db)
        target = await _make_org(db)
        user = await _org_user(db, home)
        role = await _grant_at(db, user.user_id, target, "tables.read")
        table = _table(
            f"restricted_{uuid.uuid4().hex[:8]}",
            org_id=target,
            access={
                "policies": [{
                    "name": "role-read",
                    "actions": ["read"],
                    "when": {"call": "has_role", "args": [str(role.id)]},
                }]
            },
        )
        db.add(table)
        await db.flush()

        websocket = SimpleNamespace(
            state=SimpleNamespace(table_subscriptions={str(table.id): {"filter": None}}),
            send_json=AsyncMock(),
        )
        payload = {"type": "document_change", "old_row": None, "new_row": {"id": "row-1"}}

        await ws_mod._handle_table_message(websocket, user, f"table:{table.id}", payload)
        assert websocket.send_json.await_args.args[0]["type"] == "document_change"

        await db.execute(
            delete(UserRole).where(
                UserRole.user_id == user.user_id,
                UserRole.role_id == role.id,
            )
        )
        await db.flush()
        websocket.send_json.reset_mock()

        await ws_mod._handle_table_message(websocket, user, f"table:{table.id}", payload)

        assert str(table.id) not in websocket.state.table_subscriptions
        websocket.send_json.assert_awaited_once_with({
            "type": "subscription_revoked",
            "channel": f"table:{table.id}",
        })


class TestClaimGatedSubscriptionVisibility:
    """A DB-backed custom claim must control subscription visibility end to end.

    Seeds a source table with a membership document, a list claim selecting
    the member's campus, and a policy table whose read rule references the
    claim. A document_change for a matching row must be delivered; after the
    claim source changes so the member no longer matches, the same message
    must be suppressed. This exercises the real loader, preresolution, and
    authorization — not mocks.
    """

    async def test_claim_source_change_flips_visibility(self, patched_db) -> None:
        from src.models.orm.custom_claims import CustomClaim as CustomClaimORM
        from src.models.orm.tables import Document

        db = patched_db
        org = await _make_org(db)
        suffix = uuid.uuid4().hex[:8]

        # Source table holding membership facts (open read so the claim
        # query itself is authorized; claims fail closed without it).
        source = _table(
            f"memberships_{suffix}",
            org_id=org,
            access={"policies": [{"name": "open_read", "actions": ["read"]}]},
        )
        db.add(source)
        await db.flush()
        member_email = f"member-{suffix}@x.test"
        user = await _org_user(db, org)
        await db.execute(
            update(User).where(User.id == user.user_id).values(email=member_email)
        )
        user.email = member_email
        db.add(
            Document(
                table_id=source.id,
                data={"email": member_email, "campus_id": "north"},
            )
        )

        # List claim: campuses for the calling user.
        db.add(
            CustomClaimORM(
                id=uuid.uuid4(),
                organization_id=org,
                name="allowed_campus_ids",
                type="list",
                query={
                    "table": source.name,
                    "where": {"eq": [{"row": "email"}, {"user": "email"}]},
                    "select": "campus_id",
                },
            )
        )

        # Policy table whose read rule references the claim.
        policy_table = _table(
            f"rosters_{suffix}",
            org_id=org,
            access={
                "policies": [
                    {
                        "name": "campus_read",
                        "actions": ["read"],
                        "when": {
                            "in": [
                                {"row": "campus_id"},
                                {"claims": "allowed_campus_ids"},
                            ]
                        },
                    }
                ]
            },
        )
        db.add(policy_table)
        await db.flush()

        websocket = SimpleNamespace(
            state=SimpleNamespace(
                table_subscriptions={str(policy_table.id): {"filter": None}}
            ),
            send_json=AsyncMock(),
        )
        payload = {
            "type": "document_change",
            "old_row": None,
            "new_row": {"id": "r1", "campus_id": "north"},
        }

        await ws_mod._handle_table_message(
            websocket, user, f"table:{policy_table.id}", payload
        )
        assert websocket.send_json.await_count == 1
        sent = websocket.send_json.await_args.args[0]
        assert sent["action"] == "insert"

        # The claim source changes: the member moves off campus "north".
        await db.execute(
            Document.__table__.update()
            .where(Document.table_id == source.id)
            .values(data={"email": member_email, "campus_id": "south"})
        )
        await db.flush()
        websocket.send_json.reset_mock()

        await ws_mod._handle_table_message(
            websocket, user, f"table:{policy_table.id}", payload
        )
        websocket.send_json.assert_not_awaited()

