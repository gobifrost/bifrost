import pytest
from bifrost.field_classes import classify, import_owner_of, FieldClass
from bifrost.manifest_codec import Destination, EntityCodec, ImportFields
from pydantic import BaseModel, Field


def test_classify_records_import_owner():
    class M(BaseModel):
        a: str = Field(**classify(FieldClass.CONTENT, import_owner="indexer"))
        b: str = Field(**classify(FieldClass.CONTENT))  # default
    assert import_owner_of(M, "a") == "indexer"
    assert import_owner_of(M, "b") == "direct"


def test_view_git_sync_dumps_whole_model_including_nones():
    class M(EntityCodec, BaseModel):
        id: str = Field(**classify(FieldClass.IDENTITY))
        path: str | None = Field(default=None, **classify(FieldClass.CONTENT))
    m = M(id="x")
    # GIT_SYNC == model_dump() verbatim: every field present, None included.
    assert m.view(Destination.GIT_SYNC) == {"id": "x", "path": None}


def test_import_fields_shape():
    f = ImportFields(indexer_content={}, direct={"a": 1}, restamp={})
    assert f.direct == {"a": 1} and f.indexer_content == {} and f.restamp == {}


def assert_parity(produced: dict, legacy: dict, *, label: str = "") -> None:
    """Byte-parity assertion for entity conversions: key-set first, then values."""
    only_new = set(produced) - set(legacy)
    only_old = set(legacy) - set(produced)
    assert not only_new and not only_old, (
        f"{label} field-set mismatch: only_new={only_new} only_old={only_old}"
    )
    assert produced == legacy, f"{label} values diverge:\n produced={produced}\n legacy={legacy}"


def test_assert_parity_passes_on_equal_and_fails_on_diff():
    assert_parity({"a": 1}, {"a": 1}, label="ok")
    with pytest.raises(AssertionError):
        assert_parity({"a": 1}, {"a": 2}, label="bad")
    with pytest.raises(AssertionError):
        assert_parity({"a": 1, "b": 2}, {"a": 1}, label="extra")


@pytest.mark.e2e
async def test_organization_git_sync_parity(db_session):
    import uuid
    from sqlalchemy import delete
    from src.models.orm.organizations import Organization
    from bifrost.manifest import ManifestOrganization
    from bifrost.manifest_codec import Destination

    org = Organization(id=uuid.uuid4(), name="RT Org Parity", is_active=True, created_by="test")
    db_session.add(org)
    await db_session.commit()

    try:
        expected = {"id": str(org.id), "name": "RT Org Parity", "is_active": True}
        produced = ManifestOrganization.from_row(org).view(Destination.GIT_SYNC)
        assert_parity(produced, expected, label="organization git_sync")
    finally:
        await db_session.execute(delete(Organization).where(Organization.id == org.id))
        await db_session.commit()


@pytest.mark.e2e
async def test_role_git_sync_parity(db_session):
    import uuid
    from sqlalchemy import delete
    from src.models.orm.users import Role
    from bifrost.manifest import ManifestRole
    from bifrost.manifest_codec import Destination

    role = Role(id=uuid.uuid4(), name="rt_role_parity", created_by="test")
    db_session.add(role)
    await db_session.commit()

    try:
        expected = {"id": str(role.id), "name": "rt_role_parity"}
        produced = ManifestRole.from_row(role).view(Destination.GIT_SYNC)
        assert_parity(produced, expected, label="role git_sync")
    finally:
        await db_session.execute(delete(Role).where(Role.id == role.id))
        await db_session.commit()


@pytest.mark.e2e
async def test_workflow_git_sync_parity(db_session):
    """from_row(wf, roles=[...]).view(GIT_SYNC) == serialize_workflow(wf, roles=[...]).model_dump()"""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.workflows import Workflow
    from bifrost.manifest import ManifestWorkflow
    from bifrost.manifest_codec import Destination
    from src.services.manifest_generator import serialize_workflow

    wf_id = uuid.uuid4()
    wf = Workflow(
        id=wf_id,
        name="rt_wf_parity",
        path="workflows/rt_parity.py",
        function_name="rt_parity",
        type="workflow",
        description="parity test desc",
        tool_description="parity tool desc",
        access_level="authenticated",
        endpoint_enabled=True,
        timeout_seconds=999,
        public_endpoint=True,
        category="TestCat",
        tags=["alpha", "beta"],
        is_active=True,
    )
    db_session.add(wf)
    await db_session.commit()

    try:
        roles: list[str] = []
        expected = serialize_workflow(wf, roles=roles).model_dump()
        produced = ManifestWorkflow.from_row(wf, roles=roles).view(Destination.GIT_SYNC)
        assert_parity(produced, expected, label="workflow git_sync")
    finally:
        await db_session.execute(delete(Workflow).where(Workflow.id == wf_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_workflow_install_parity(db_session):
    """from_row(...).view(INSTALL, extras=...) == _workflow_entries(solution_id)[0]"""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.solutions import Solution
    from src.models.orm.users import Role
    from src.models.orm.workflow_roles import WorkflowRole
    from src.models.orm.workflows import Workflow
    from bifrost.manifest import ManifestWorkflow
    from bifrost.manifest_codec import Destination
    from src.services.solutions.capture import SolutionCaptureService

    sol_id = uuid.uuid4()
    wf_id = uuid.uuid4()
    role_id = uuid.uuid4()

    sol = Solution(
        id=sol_id,
        slug=f"rt-sol-{sol_id.hex[:8]}",
        name="RT Install Parity Sol",
    )
    db_session.add(sol)

    wf = Workflow(
        id=wf_id,
        name="rt_wf_install_parity",
        path="workflows/rt_install_parity.py",
        function_name="rt_install_parity",
        type="workflow",
        description="install parity desc",
        tool_description=None,
        access_level="role_based",
        endpoint_enabled=True,
        timeout_seconds=300,
        public_endpoint=False,
        category="InstallCat",
        tags=["x"],
        is_active=True,
        solution_id=sol_id,
    )
    db_session.add(wf)

    role = Role(id=role_id, name="rt_install_parity_role", created_by="test")
    db_session.add(role)
    await db_session.flush()

    wf_role = WorkflowRole(workflow_id=wf_id, role_id=role_id)
    db_session.add(wf_role)
    await db_session.commit()

    try:
        capture = SolutionCaptureService(db_session)
        # Legacy: the hand-written dict producer.
        legacy_entries = await capture._workflow_entries(sol_id)
        assert len(legacy_entries) == 1, f"expected 1 entry, got {legacy_entries}"
        legacy = legacy_entries[0]

        # New: codec-produced view.
        role_ids = [str(role_id)]
        role_names = await capture._role_names(role_ids)
        produced = ManifestWorkflow.from_row(wf, roles=role_ids).view(
            Destination.INSTALL, extras={"roles": role_ids, "role_names": role_names}
        )
        assert_parity(produced, legacy, label="workflow install")
    finally:
        await db_session.execute(delete(WorkflowRole).where(WorkflowRole.workflow_id == wf_id))
        await db_session.execute(delete(Workflow).where(Workflow.id == wf_id))
        await db_session.execute(delete(Role).where(Role.id == role_id))
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_table_git_sync_parity(db_session):
    """from_row(table).view(GIT_SYNC) == serialize_table(table).model_dump(by_alias=True)"""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.tables import Table
    from bifrost.manifest import ManifestTable
    from bifrost.manifest_codec import Destination
    from src.services.manifest_generator import serialize_table

    tid = uuid.uuid4()
    table = Table(
        id=tid,
        name=f"rt_table_{tid.hex[:6]}",
        description="parity test table",
        organization_id=None,
        schema={"columns": [{"name": "col1", "type": "string"}]},
        access={"policies": [
            {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": None},
            {
                "name": "owner_can_edit",
                "description": "Row owner may update/delete",
                "actions": ["update", "delete"],
                "when": {"eq": [{"row": "owner_id"}, {"user": "user_id"}]},
            },
        ]},
    )
    db_session.add(table)
    await db_session.commit()

    try:
        # The roundtrip writer calls model_dump(by_alias=True) on the serialized
        # ManifestTable; use that same call on both sides so the parity oracle
        # is comparing apples to apples (both emit "schema", not "table_schema").
        expected = serialize_table(table).model_dump(by_alias=True)
        produced = ManifestTable.from_row(table).view(Destination.GIT_SYNC)
        assert_parity(produced, expected, label="table git_sync")
    finally:
        await db_session.execute(delete(Table).where(Table.id == tid))
        await db_session.commit()


@pytest.mark.e2e
async def test_table_install_parity(db_session):
    """from_row(t).view(INSTALL) == capture._table_entries(solution_id)[0]"""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.solutions import Solution
    from src.models.orm.tables import Table
    from bifrost.manifest import ManifestTable
    from bifrost.manifest_codec import Destination
    from src.services.solutions.capture import SolutionCaptureService

    sol_id = uuid.uuid4()
    tid = uuid.uuid4()

    sol = Solution(
        id=sol_id,
        slug=f"rt-sol-{sol_id.hex[:8]}",
        name="RT Table Install Parity Sol",
    )
    db_session.add(sol)

    table = Table(
        id=tid,
        name=f"rt_table_{tid.hex[:6]}",
        description="install parity table",
        organization_id=None,
        schema={"columns": [{"name": "item", "type": "string"}]},
        access={"policies": [
            {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": None},
        ]},
        solution_id=sol_id,
    )
    db_session.add(table)
    await db_session.commit()

    try:
        capture = SolutionCaptureService(db_session)
        legacy_entries = await capture._table_entries(sol_id)
        assert len(legacy_entries) == 1, f"expected 1 entry, got {legacy_entries}"
        legacy = legacy_entries[0]

        produced = ManifestTable.from_row(table).view(Destination.INSTALL)
        assert_parity(produced, legacy, label="table install")
    finally:
        await db_session.execute(delete(Table).where(Table.id == tid))
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.commit()
