"""Prospective conversion policies use current resource scope and rules."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from shared.workflow_conversion_resources import source_resource_requirements
from src.models.orm.tables import Table


def _workflow():
    return SimpleNamespace(id=uuid4(), organization_id=None, solution_id=None)


def _analysis(call="bifrost.tables.get", **selectors):
    return {
        "sdk_calls": [call],
        "resource_calls": [{
            "call": call, "path": "workflows/example.py", "line": 3,
            "selectors": selectors, "dynamic_selectors": [],
        }],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("when,expected", [
    ({"authority": "platform_admin"}, "denied"),
    ({"or": [True, {"call": "has_role", "args": ["Operators"]}]}, None),
    ({"eq": [{"row": "owner"}, {"user": "user_id"}]}, "conditional"),
])
async def test_conversion_checks_the_selected_table_without_full_authority(
    db_session, when, expected
):
    table = Table(
        name=f"conversion-{uuid4()}",
        access={"policies": [{"name": "read", "actions": ["read"], "when": when}]},
    )
    db_session.add(table)
    await db_session.flush()

    result = await source_resource_requirements(
        db_session, _workflow(), _analysis(table=table.name), [], []
    )

    if expected is None:
        assert result.unknowns == ()
    else:
        assert len(result.unknowns) == 1
        assert f"read is {expected}" in result.unknowns[0]


@pytest.mark.asyncio
async def test_a_static_foreign_table_does_not_invent_organization_reach(db_session):
    from src.models.orm.organizations import Organization

    organization = Organization(name="Contoso", created_by="test")
    db_session.add(organization)
    await db_session.flush()
    table = Table(
        name=f"foreign-{uuid4()}", organization_id=organization.id,
        access={"policies": [{"name": "read", "actions": ["read"]}]},
    )
    db_session.add(table)
    await db_session.flush()

    result = await source_resource_requirements(
        db_session, _workflow(), _analysis(table=str(table.id)), [], []
    )

    assert len(result.unknowns) == 1
    assert "cannot be resolved within the proposed identity's reach" in result.unknowns[0]


@pytest.mark.asyncio
async def test_unresolved_target_and_observed_only_policy_calls_stay_in_review(db_session):
    result = await source_resource_requirements(
        db_session, _workflow(), _analysis(), [],
        ["POST /api/files/read"],
    )

    assert len(result.unknowns) == 2
    assert any("table is determined at runtime" in reason for reason in result.unknowns)
    assert any("observed operation" in reason for reason in result.unknowns)


@pytest.mark.asyncio
async def test_inherited_resource_scope_is_not_assumed_after_a_scope_override(db_session):
    analysis = _analysis(table="reports")
    analysis["sdk_calls"].append("bifrost.context.set_scope")

    result = await source_resource_requirements(
        db_session, _workflow(), analysis, [], []
    )

    assert len(result.unknowns) == 1
    assert "scope can change within the workflow" in result.unknowns[0]


@pytest.mark.asyncio
async def test_file_policy_preview_uses_the_governing_prefix_without_reading_content(db_session):
    from src.models.orm.file_metadata import FilePolicy

    prefix = f"conversion-{uuid4()}"
    db_session.add_all([
        FilePolicy(
            location="uploads", path=prefix, organization_id=None,
            policies={"policies": [{"name": "deny", "actions": ["read"], "when": False}]},
        ),
        FilePolicy(
            location="uploads", path=f"{prefix}/reports", organization_id=None,
            policies={"policies": [{"name": "read", "actions": ["read"]}]},
        ),
    ])
    await db_session.flush()

    result = await source_resource_requirements(
        db_session, _workflow(),
        _analysis("bifrost.files.read", path=f"{prefix}/reports/example.txt", location="uploads"),
        [], [],
    )

    assert result.unknowns == ()
    assert result.permissions == ()


@pytest.mark.asyncio
async def test_workspace_files_propose_the_repository_permission_instead_of_a_file_policy(db_session):
    result = await source_resource_requirements(
        db_session, _workflow(),
        _analysis("bifrost.files.read", location="workspace"), [], [],
    )

    assert result.permissions == ("repository.read",)
    assert result.unknowns == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,permission", [("GET", "repository.read"), ("PUT", "repository.readwrite")])
async def test_workspace_signed_urls_propose_the_permission_for_the_signed_action(
    db_session, method, permission
):
    result = await source_resource_requirements(
        db_session, _workflow(),
        _analysis("bifrost.files.get_signed_url", location="workspace", method=method),
        [], [],
    )

    assert result.permissions == (permission,)
    assert result.unknowns == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["bifrost.events.emit", "bifrost.workflows.execute"])
@pytest.mark.parametrize("own_install,allow_inbound,allowed", [
    (True, False, True), (False, True, True), (False, False, False),
])
async def test_explicit_solution_targets_use_the_canonical_inbound_gate(
    db_session, call, own_install, allow_inbound, allowed
):
    from src.models.orm.solutions import Solution

    target = Solution(
        slug=f"contoso-{uuid4()}", name="Contoso", allow_inbound_access=allow_inbound,
    )
    db_session.add(target)
    await db_session.flush()
    workflow = _workflow()
    if own_install:
        workflow.solution_id = target.id

    result = await source_resource_requirements(
        db_session, workflow, _analysis(call, solution=target.slug), [], []
    )

    assert bool(result.unknowns) is not allowed
    if not allowed:
        assert "Solution target is unavailable" in result.unknowns[0]


@pytest.mark.asyncio
async def test_dynamic_solution_targets_do_not_become_ready_from_named_grants(db_session):
    analysis = _analysis("bifrost.events.emit")
    analysis["resource_calls"][0]["dynamic_selectors"] = ["solution"]

    result = await source_resource_requirements(
        db_session, _workflow(), analysis, ["events.readwrite"], []
    )

    assert len(result.unknowns) == 1
    assert "Solution target is determined at runtime" in result.unknowns[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,allowed", [(None, False), ("GLOBAL", True)])
async def test_solution_slug_uses_the_selected_organization_before_global(db_session, scope, allowed):
    from src.models.orm.organizations import Organization
    from src.models.orm.solutions import Solution

    organization = Organization(name="Contoso", created_by="test")
    db_session.add(organization)
    await db_session.flush()
    slug = f"contoso-{uuid4()}"
    db_session.add_all([
        Solution(slug=slug, name="Contoso", allow_inbound_access=True),
        Solution(
            slug=slug, name="Contoso", organization_id=organization.id,
            allow_inbound_access=False,
        ),
    ])
    await db_session.flush()
    workflow = _workflow()
    workflow.organization_id = organization.id

    result = await source_resource_requirements(
        db_session, workflow,
        _analysis("bifrost.events.emit", solution=slug, **({"scope": scope} if scope else {})),
        [], [],
    )

    assert bool(result.unknowns) is not allowed
    if not allowed:
        assert "Solution target is unavailable" in result.unknowns[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("call,allowed", [("bifrost.tables.get", True), ("bifrost.tables.insert", False)])
async def test_solution_shared_table_fallback_keeps_the_runtime_write_confinement(
    db_session, call, allowed
):
    from src.models.orm.solutions import Solution

    solution = Solution(
        slug=f"contoso-{uuid4()}", name="Contoso", allow_outbound_access=True,
    )
    table = Table(
        name=f"contoso-{uuid4()}",
        access={"policies": [{"name": "public", "actions": ["read", "create"]}]},
    )
    db_session.add_all([solution, table])
    await db_session.flush()
    workflow = _workflow()
    workflow.solution_id = solution.id

    result = await source_resource_requirements(
        db_session, workflow, _analysis(call, table=table.name), [], []
    )

    assert bool(result.unknowns) is not allowed
