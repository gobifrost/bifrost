import json
import os
from pathlib import Path

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


# --- Golden-file characterization oracle -----------------------------------
# A frozen, machine-captured snapshot of each entity's produced git_sync/install
# dict. Used INSTEAD of comparing against the live writer once Phase B has swapped
# the writer to delegate to the model — at that point `serialize_X()`/`_X_entries()`
# call the SAME model code, so comparing against them is circular (tautological).
# The golden file is captured ONCE while the round-trip detector is green (so the
# bytes are detector-proven byte-identical to the original writers), then committed.
# Re-capture deliberately with UPDATE_GOLDEN=1 (review the git diff of the fixture).
# Committed fixtures live in the repo (read at assert time — the container mounts
# /app READ-ONLY, so the test can only READ here). Captures in UPDATE_GOLDEN mode
# are written to the WRITABLE LOG_DIR mount (/tmp/bifrost in-container, host
# /tmp/bifrost-<project>) for the developer to harvest and commit into GOLDEN_DIR.
GOLDEN_DIR = Path(__file__).parent / "golden" / "manifest_codec"
GOLDEN_CAPTURE_DIR = Path("/tmp/bifrost/golden/manifest_codec")


# Keys whose VALUES are per-run-random (seeded uuid4 PKs / FK id lists). The
# golden locks their PRESENCE and shape, not the volatile value: each is replaced
# with a stable sentinel before capture AND before compare. The seed varies the
# uuid per run (no fixed-id collision across leaked sessions), the golden stays
# byte-stable. Nested volatile ids (e.g. inside a policies/subscriptions list) are
# masked by dotted path handled in _mask.
VOLATILE_SENTINEL = "<volatile>"


def _mask(value, volatile_keys: set[str]):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in volatile_keys:
                # mask scalars and every element of a list (id-list FKs)
                out[k] = (
                    [VOLATILE_SENTINEL for _ in v] if isinstance(v, list) else VOLATILE_SENTINEL
                )
            else:
                out[k] = _mask(v, volatile_keys)
        return out
    if isinstance(value, list):
        return [_mask(v, volatile_keys) for v in value]
    return value


def _normalize(produced: dict, volatile_keys: set[str]) -> dict:
    # json round-trip normalizes tuples→lists etc. so the comparison matches the
    # on-disk form exactly (the produced dict is already JSON-mode from view()),
    # then volatile per-run ids are masked to a stable sentinel.
    norm = json.loads(json.dumps(produced, sort_keys=True))
    masked = _mask(norm, volatile_keys)
    assert isinstance(masked, dict)  # top-level produced is always a dict
    return masked


def assert_golden(produced: dict, name: str, *, volatile_keys: set[str] | None = None) -> None:
    """Assert *produced* equals the committed golden snapshot ``<name>.json``.

    Non-circular characterization oracle: the golden is captured from a
    detector-verified run (not from the live, now-delegating writer) and
    committed. To (re)capture, run the suite with ``UPDATE_GOLDEN=1`` — the new
    snapshot is written under ``/tmp/bifrost/golden/manifest_codec`` (the
    writable LOG_DIR mount; ``/app`` is read-only in the test container). Harvest
    it into ``tests/unit/golden/manifest_codec`` and commit only after confirming
    the round-trip detector is green and reviewing the fixture diff.
    """
    produced_norm = _normalize(produced, volatile_keys or set())
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN_CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
        (GOLDEN_CAPTURE_DIR / f"{name}.json").write_text(
            json.dumps(produced_norm, indent=2, sort_keys=True) + "\n"
        )
        return  # capture run: don't assert against a possibly-stale committed fixture
    path = GOLDEN_DIR / f"{name}.json"
    assert path.exists(), (
        f"golden {name}.json is missing. Capture it with UPDATE_GOLDEN=1, then copy "
        f"from /tmp/bifrost-<project>/golden/manifest_codec into "
        f"api/tests/unit/golden/manifest_codec and commit."
    )
    golden = json.loads(path.read_text())
    assert produced_norm == golden, (
        f"{name}: produced diverges from golden {name}.json. If this change is "
        f"intentional and the round-trip detector is green, re-capture with "
        f"UPDATE_GOLDEN=1 and review the fixture diff.\n produced={produced_norm}\n golden={golden}"
    )


def test_assert_golden_compares_against_committed_fixture(tmp_path, monkeypatch):
    # Point GOLDEN_DIR at a tmp dir holding a known fixture; prove compare passes
    # on match (order-independent) and raises on divergence. Capture-mode is a
    # no-assert side effect, so it's exercised separately via UPDATE_GOLDEN.
    monkeypatch.setattr("tests.unit.test_manifest_codec.GOLDEN_DIR", tmp_path)
    monkeypatch.delenv("UPDATE_GOLDEN", raising=False)
    (tmp_path / "selfcheck.json").write_text(json.dumps({"a": 1, "b": 2}, sort_keys=True))
    assert_golden({"b": 2, "a": 1}, "selfcheck")  # equal, order-independent
    with pytest.raises(AssertionError):
        assert_golden({"a": 1, "b": 999}, "selfcheck")  # diverges
    with pytest.raises(AssertionError):
        assert_golden({"a": 1}, "missing_fixture")  # absent fixture fails loudly
    # volatile masking: differing id values compare equal once masked
    (tmp_path / "vol.json").write_text(
        json.dumps({"id": VOLATILE_SENTINEL, "roles": [VOLATILE_SENTINEL]}, sort_keys=True)
    )
    assert_golden({"id": "abc", "roles": ["xyz"]}, "vol", volatile_keys={"id", "roles"})


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
    """GIT_SYNC view of a seeded Workflow matches the committed golden snapshot."""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.workflows import Workflow
    from bifrost.manifest import ManifestWorkflow
    from bifrost.manifest_codec import Destination

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
        produced = ManifestWorkflow.from_row(wf, roles=roles).view(Destination.GIT_SYNC)
        assert_golden(produced, "workflow_git_sync", volatile_keys={"id"})
    finally:
        await db_session.execute(delete(Workflow).where(Workflow.id == wf_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_workflow_install_parity(db_session):
    """INSTALL view of a seeded solution-owned Workflow matches the committed golden snapshot."""
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
        # Codec-produced install view. role_ids/role_names are the extras the
        # capture orchestrator computes and passes in.
        capture = SolutionCaptureService(db_session)
        role_ids = [str(role_id)]
        role_names = await capture._role_names(role_ids)
        produced = ManifestWorkflow.from_row(wf, roles=role_ids).view(
            Destination.INSTALL, extras={"roles": role_ids, "role_names": role_names}
        )
        assert_golden(produced, "workflow_install", volatile_keys={"id", "roles"})
    finally:
        await db_session.execute(delete(WorkflowRole).where(WorkflowRole.workflow_id == wf_id))
        await db_session.execute(delete(Workflow).where(Workflow.id == wf_id))
        await db_session.execute(delete(Role).where(Role.id == role_id))
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_table_git_sync_parity(db_session):
    """GIT_SYNC view of a seeded Table matches the committed golden snapshot."""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.tables import Table
    from bifrost.manifest import ManifestTable
    from bifrost.manifest_codec import Destination

    tid = uuid.uuid4()
    table = Table(
        id=tid,
        name="rt_table_golden",
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
        produced = ManifestTable.from_row(table).view(Destination.GIT_SYNC)
        assert_golden(produced, "table_git_sync", volatile_keys={"id"})
    finally:
        await db_session.execute(delete(Table).where(Table.id == tid))
        await db_session.commit()


@pytest.mark.e2e
async def test_table_install_parity(db_session):
    """INSTALL view of a seeded solution-owned Table matches the committed golden snapshot."""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.solutions import Solution
    from src.models.orm.tables import Table
    from bifrost.manifest import ManifestTable
    from bifrost.manifest_codec import Destination

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
        name="rt_table_install_golden",
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
        produced = ManifestTable.from_row(table).view(Destination.INSTALL)
        assert_golden(produced, "table_install", volatile_keys={"id"})
    finally:
        await db_session.execute(delete(Table).where(Table.id == tid))
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_claim_git_sync_parity(db_session):
    """GIT_SYNC view of a seeded CustomClaim matches the committed golden snapshot."""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.custom_claims import CustomClaim
    from src.models.orm.organizations import Organization
    from bifrost.manifest import ManifestCustomClaim
    from bifrost.manifest_codec import Destination

    org_id = uuid.uuid4()
    claim_id = uuid.uuid4()

    org = Organization(id=org_id, name=f"RT Claim Org golden {org_id.hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()

    claim = CustomClaim(
        id=claim_id,
        name="rt_claim_golden",
        organization_id=org_id,
        type="list",
        query={"table": "users", "select": "id"},
        description="golden claim",
    )
    db_session.add(claim)
    await db_session.flush()

    try:
        produced = ManifestCustomClaim.from_row(claim).view(Destination.GIT_SYNC)
        assert_golden(produced, "claim_git_sync", volatile_keys={"id", "organization_id"})
    finally:
        await db_session.execute(delete(CustomClaim).where(CustomClaim.id == claim_id))
        await db_session.execute(delete(Organization).where(Organization.id == org_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_config_git_sync_parity(db_session):
    """GIT_SYNC view of a seeded Config matches the committed golden snapshot.

    Also asserts that the SECRET value-redaction path produces None.
    Config has no install path — to_orm_values(INSTALL) is explicitly unsupported.
    """
    import uuid
    from sqlalchemy import delete
    from src.models.orm.config import Config
    from src.models.enums import ConfigType
    from bifrost.manifest import ManifestConfig
    from bifrost.manifest_codec import Destination

    cfg_id = uuid.uuid4()
    secret_id = uuid.uuid4()

    cfg = Config(
        id=cfg_id,
        key="RT_CONFIG_GOLDEN",
        config_type=ConfigType.STRING,
        value="golden-value",
        description="parity test config",
        organization_id=None,
        integration_id=None,
        updated_by="test",
    )
    secret_cfg = Config(
        id=secret_id,
        key="RT_CONFIG_SECRET_GOLDEN",
        config_type=ConfigType.SECRET,
        value="supersecret",
        description="secret config",
        organization_id=None,
        integration_id=None,
        updated_by="test",
    )
    db_session.add(cfg)
    db_session.add(secret_cfg)
    await db_session.commit()

    try:
        produced = ManifestConfig.from_row(cfg).view(Destination.GIT_SYNC)
        assert_golden(produced, "config_git_sync", volatile_keys={"id", "organization_id"})

        # Secret value must be redacted to None regardless of stored value
        assert ManifestConfig.from_row(secret_cfg).value is None
    finally:
        await db_session.execute(delete(Config).where(Config.id == cfg_id))
        await db_session.execute(delete(Config).where(Config.id == secret_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_claim_install_parity(db_session):
    """INSTALL view of a seeded solution-owned CustomClaim matches the committed golden snapshot."""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.custom_claims import CustomClaim
    from src.models.orm.organizations import Organization
    from src.models.orm.solutions import Solution
    from bifrost.manifest import ManifestCustomClaim
    from bifrost.manifest_codec import Destination

    org_id = uuid.uuid4()
    sol_id = uuid.uuid4()
    claim_id = uuid.uuid4()

    org = Organization(id=org_id, name=f"RT Claim Install Org {org_id.hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()

    sol = Solution(
        id=sol_id,
        slug=f"rt-claim-sol-{sol_id.hex[:8]}",
        name="RT Claim Install Parity Sol",
    )
    db_session.add(sol)
    await db_session.flush()

    claim = CustomClaim(
        id=claim_id,
        name="rt_claim_install_golden",
        organization_id=org_id,
        solution_id=sol_id,
        type="list",
        query={"table": "assets", "select": "device_id", "where": None},
        description="install golden claim",
    )
    db_session.add(claim)
    await db_session.flush()

    try:
        produced = ManifestCustomClaim.from_row(claim).view(Destination.INSTALL)
        assert_golden(produced, "claim_install", volatile_keys={"id"})
    finally:
        await db_session.execute(delete(CustomClaim).where(CustomClaim.id == claim_id))
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.execute(delete(Organization).where(Organization.id == org_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_integration_git_sync_parity(db_session):
    """GIT_SYNC view of a seeded Integration (with config_schema, oauth_provider, mappings)
    matches the committed golden snapshot.

    Integration has no install path (install uses connection_schema templates).
    Child models (ConfigSchema, OAuthProvider, Mapping) have no standalone orm path.
    """
    import uuid
    from sqlalchemy import delete
    from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
    from src.models.orm.oauth import OAuthProvider
    from src.models.orm.organizations import Organization
    from bifrost.manifest import ManifestIntegration
    from bifrost.manifest_codec import Destination

    integ_id = uuid.uuid4()
    org_id = uuid.uuid4()

    org = Organization(id=org_id, name=f"rt-integration-org-{org_id.hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()

    integ = Integration(
        id=integ_id,
        name="rt-integration-golden",
        entity_id="tenant_id",
        entity_id_name="Tenant ID",
        default_entity_id=None,
        list_entities_data_provider_id=None,
        is_deleted=False,
    )
    db_session.add(integ)
    await db_session.flush()

    cs = IntegrationConfigSchema(
        integration_id=integ_id,
        key="api_key",
        type="secret",
        required=True,
        description="API key for auth",
        options=None,
        position=0,
    )
    db_session.add(cs)
    await db_session.flush()

    op = OAuthProvider(
        provider_name="rt-golden-oauth",
        display_name="RT Golden OAuth",
        oauth_flow_type="authorization_code",
        client_id="test-client-id",
        encrypted_client_secret=b"",
        authorization_url="https://auth.example.com/authorize",
        token_url="https://auth.example.com/token",
        token_url_defaults=None,
        scopes=["openid", "email"],
        redirect_uri="https://app.example.com/callback",
        integration_id=integ_id,
    )
    db_session.add(op)
    await db_session.flush()

    mapping = IntegrationMapping(
        integration_id=integ_id,
        organization_id=org_id,
        entity_id="tenant-abc-123",
        entity_name="Tenant ABC",
        oauth_token_id=None,
    )
    db_session.add(mapping)
    await db_session.commit()

    try:
        produced = ManifestIntegration.from_row(
            integ,
            config_schema=[cs],
            oauth_provider=op,
            mappings=[mapping],
        ).view(Destination.GIT_SYNC)
        assert_golden(
            produced,
            "integration_git_sync",
            volatile_keys={"id", "organization_id"},
        )

        # Verify child-model to_orm_values raises (no standalone path)
        import pytest as _pytest
        with _pytest.raises(NotImplementedError):
            ManifestIntegration.from_row(integ).to_orm_values(Destination.INSTALL)
    finally:
        await db_session.execute(
            delete(IntegrationMapping).where(IntegrationMapping.integration_id == integ_id)
        )
        await db_session.execute(
            delete(OAuthProvider).where(OAuthProvider.integration_id == integ_id)
        )
        await db_session.execute(
            delete(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == integ_id
            )
        )
        await db_session.execute(
            delete(Integration).where(Integration.name.like("rt-integration%"))
        )
        await db_session.execute(
            delete(Organization).where(Organization.id == org_id)
        )
        await db_session.commit()


@pytest.mark.e2e
async def test_event_git_sync_parity(db_session):
    """GIT_SYNC view of a seeded EventSource (schedule + subscriptions) matches the committed golden snapshot."""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.events import EventSource, EventSubscription, ScheduleSource
    from src.models.enums import EventSourceType, ScheduleOverlapPolicy
    from bifrost.manifest import ManifestEventSource
    from bifrost.manifest_codec import Destination

    es_id = uuid.uuid4()
    sched_id = uuid.uuid4()
    sub_id = uuid.uuid4()

    es = EventSource(
        id=es_id,
        name="rt_sched_golden",
        source_type=EventSourceType.SCHEDULE,
        is_active=True,
        created_by="test",
    )
    db_session.add(es)
    await db_session.flush()

    sched = ScheduleSource(
        id=sched_id,
        event_source_id=es_id,
        cron_expression="0 9 * * 1-5",
        timezone="America/New_York",
        enabled=True,
        overlap_policy=ScheduleOverlapPolicy.SKIP,
    )
    db_session.add(sched)

    sub = EventSubscription(
        id=sub_id,
        event_source_id=es_id,
        target_type="workflow",
        workflow_id=None,
        agent_id=None,
        event_type=None,
        filter_expression=None,
        input_mapping={"foo": "bar"},
        is_active=True,
        created_by="test",
    )
    db_session.add(sub)
    await db_session.commit()

    try:
        produced = ManifestEventSource.from_row(
            es, schedule=sched, subscriptions=[sub]
        ).view(Destination.GIT_SYNC)
        assert_golden(
            produced,
            "event_git_sync",
            volatile_keys={"id"},
        )
    finally:
        await db_session.execute(delete(EventSubscription).where(EventSubscription.id == sub_id))
        await db_session.execute(delete(ScheduleSource).where(ScheduleSource.id == sched_id))
        await db_session.execute(delete(EventSource).where(EventSource.id == es_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_event_install_parity(db_session):
    """INSTALL view of an EventSource equals GIT_SYNC (Nones INCLUDED — override confirmed)."""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.events import EventSource, EventSubscription, ScheduleSource
    from src.models.enums import EventSourceType, ScheduleOverlapPolicy
    from bifrost.manifest import ManifestEventSource
    from bifrost.manifest_codec import Destination

    es_id = uuid.uuid4()
    sched_id = uuid.uuid4()
    sub_id = uuid.uuid4()

    es = EventSource(
        id=es_id,
        name="rt_sched_golden",
        source_type=EventSourceType.SCHEDULE,
        is_active=True,
        created_by="test",
    )
    db_session.add(es)
    await db_session.flush()

    sched = ScheduleSource(
        id=sched_id,
        event_source_id=es_id,
        cron_expression="0 9 * * 1-5",
        timezone="America/New_York",
        enabled=True,
        overlap_policy=ScheduleOverlapPolicy.SKIP,
    )
    db_session.add(sched)

    sub = EventSubscription(
        id=sub_id,
        event_source_id=es_id,
        target_type="workflow",
        workflow_id=None,
        agent_id=None,
        event_type=None,
        filter_expression=None,
        input_mapping={"foo": "bar"},
        is_active=True,
        created_by="test",
    )
    db_session.add(sub)
    await db_session.commit()

    try:
        model = ManifestEventSource.from_row(es, schedule=sched, subscriptions=[sub])
        git_sync_view = model.view(Destination.GIT_SYNC)
        install_view = model.view(Destination.INSTALL)

        # EventSource overrides _install_view to keep Nones — both views must be identical.
        assert install_view == git_sync_view, (
            "_install_view override missing: INSTALL dropped Nones but should keep them\n"
            f"  install={install_view}\n  git_sync={git_sync_view}"
        )
        # Confirm Nones ARE present (adapter_name, webhook_integration_id, etc.)
        assert install_view.get("adapter_name") is None, "adapter_name should be None (not absent)"
        assert install_view.get("webhook_integration_id") is None

        assert_golden(install_view, "event_install", volatile_keys={"id"})
    finally:
        await db_session.execute(delete(EventSubscription).where(EventSubscription.id == sub_id))
        await db_session.execute(delete(ScheduleSource).where(ScheduleSource.id == sched_id))
        await db_session.execute(delete(EventSource).where(EventSource.id == es_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_mcp_server_git_sync_parity(db_session):
    """GIT_SYNC view of a seeded MCPServer (with connection + tools) matches the committed golden snapshot.

    MCPServer has no install path — to_orm_values(INSTALL) raises NotImplementedError.
    Child models (Connection, ConnectionTool) have no standalone orm path.
    """
    import uuid
    from sqlalchemy import delete
    from src.models.orm.external_mcp import MCPConnection, MCPConnectionTool, MCPServer
    from src.models.orm.organizations import Organization
    from bifrost.manifest import ManifestMCPServer
    from bifrost.manifest_codec import Destination

    server_id = uuid.uuid4()
    org_id = uuid.uuid4()
    # Fixed connection UUID so dict key is stable for volatile_keys path
    conn_id = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    org = Organization(id=org_id, name=f"rt-mcp-org-{org_id.hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()

    server = MCPServer(
        id=server_id,
        name="rt-mcp-golden",
        server_url="https://mcp.example.com/sse",
        oauth_provider_id=None,
        redirect_url="https://app.example.com/mcp/callback",
        discovery_metadata={"issuer": "https://mcp.example.com"},
        organization_id=None,
        is_active=True,
    )
    db_session.add(server)
    await db_session.flush()

    conn = MCPConnection(
        id=conn_id,
        server_id=server_id,
        organization_id=org_id,
        client_id="test-client-id",
        encrypted_client_secret="placeholder",
        server_url_override=None,
        available_in_chat=True,
        available_to_autonomous=False,
        service_oauth_token_id=None,
    )
    db_session.add(conn)
    await db_session.flush()

    tool = MCPConnectionTool(
        connection_id=conn_id,
        tool_name="list_tickets",
        tool_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
        enabled=True,
        disabled_reason=None,
    )
    db_session.add(tool)
    await db_session.commit()

    try:
        conn_id_str = str(conn_id)
        connections_by_id = {conn_id_str: conn}
        tools_by_connection = {conn_id_str: [tool]}

        produced = ManifestMCPServer.from_row(
            server,
            connections_by_id=connections_by_id,
            tools_by_connection=tools_by_connection,
        ).view(Destination.GIT_SYNC)

        # encrypted_client_secret must NEVER appear in any serialized output
        assert "encrypted_client_secret" not in produced
        for conn_val in produced.get("connections", {}).values():
            assert "encrypted_client_secret" not in conn_val

        # organization_id is masked everywhere (server-level and connection-level).
        # service_oauth_token_id is None in the fixture (not volatile).
        assert_golden(
            produced,
            "mcp_server_git_sync",
            volatile_keys={"id", "organization_id"},
        )

        # Child models have no standalone orm path
        import pytest as _pytest
        with _pytest.raises(NotImplementedError):
            ManifestMCPServer.from_row(server).to_orm_values(Destination.INSTALL)
    finally:
        await db_session.execute(
            delete(MCPConnectionTool).where(MCPConnectionTool.connection_id == conn_id)
        )
        await db_session.execute(
            delete(MCPConnection).where(MCPConnection.id == conn_id)
        )
        await db_session.execute(
            delete(MCPServer).where(MCPServer.name.like("rt-mcp-%"))
        )
        await db_session.execute(
            delete(Organization).where(Organization.id == org_id)
        )


@pytest.mark.e2e
async def test_solution_config_schema_install_parity(db_session):
    """INSTALL view of a seeded SolutionConfigSchema matches the committed golden snapshot.

    SolutionConfigSchema is install-only: to_orm_values(GIT_SYNC) raises
    NotImplementedError. solution_id and organization_id are absent from the view
    (stamped at deploy time).
    """
    import uuid
    from sqlalchemy import delete
    from src.models.orm.solutions import Solution
    from src.models.orm.solution_config_schema import SolutionConfigSchema
    from bifrost.manifest import ManifestSolutionConfigSchema
    from bifrost.manifest_codec import Destination

    sol_id = uuid.uuid4()

    sol = Solution(
        id=sol_id,
        slug=f"rt-sol-cfgschema-{sol_id.hex[:8]}",
        name="RT CfgSchema Golden Sol",
    )
    db_session.add(sol)
    await db_session.flush()

    cs = SolutionConfigSchema(
        solution_id=sol_id,
        key="rt_cfgschema_golden",
        type="string",
        required=True,
        description="Golden test schema",
        default="default_val",
        position=0,
    )
    db_session.add(cs)
    await db_session.commit()

    try:
        produced = ManifestSolutionConfigSchema.from_row(cs).view(Destination.INSTALL)
        assert_golden(produced, "solution_config_schema_install", volatile_keys={"id"})

        # solution_id and org ABSENT from install view
        assert "solution_id" not in produced
        assert "organization_id" not in produced
        assert produced["default"] == "default_val"

        # GIT_SYNC raises NotImplementedError (install-only entity)
        with pytest.raises(NotImplementedError):
            ManifestSolutionConfigSchema.from_row(cs).to_orm_values(Destination.GIT_SYNC)
    finally:
        await db_session.execute(
            delete(SolutionConfigSchema).where(SolutionConfigSchema.solution_id == sol_id)
        )
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.commit()



@pytest.mark.e2e
async def test_app_git_sync_parity(db_session):
    """GIT_SYNC view of a seeded Application matches the committed golden snapshot."""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.applications import Application
    from bifrost.manifest import ManifestApp
    from bifrost.manifest_codec import Destination

    app_id = uuid.uuid4()
    app = Application(
        id=app_id,
        name="RT App Golden",
        slug="rt-app-golden",
        repo_path="apps/rt-app-golden",
        description="parity test app",
        dependencies={"react": "^18.0.0"},
        app_model="standalone_v2",
    )
    db_session.add(app)
    await db_session.commit()

    try:
        roles: list[str] = []
        produced = ManifestApp.from_row(app, roles=roles).view(Destination.GIT_SYNC)
        assert_golden(produced, "app_git_sync", volatile_keys={"id", "organization_id"})
    finally:
        await db_session.execute(delete(Application).where(Application.id == app_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_app_install_parity(db_session):
    """INSTALL view of a seeded solution-owned Application matches the committed golden snapshot.

    Exercises the transport extras path: repo_path via extras, logo_b64, src_files, etc.
    organization_id must be ABSENT from the install view; ``path`` key must NOT appear;
    ``repo_path`` key must be present.
    """
    import base64
    import uuid
    from sqlalchemy import delete
    from src.models.orm.applications import Application
    from src.models.orm.solutions import Solution
    from src.models.orm.users import Role
    from src.models.orm.app_roles import AppRole
    from bifrost.manifest import ManifestApp
    from bifrost.manifest_codec import Destination
    from src.services.solutions.capture import SolutionCaptureService

    sol_id = uuid.uuid4()
    app_id = uuid.uuid4()
    role_id = uuid.uuid4()

    sol = Solution(
        id=sol_id,
        slug=f"rt-app-sol-{sol_id.hex[:8]}",
        name="RT App Install Parity Sol",
    )
    db_session.add(sol)
    await db_session.commit()

    logo_bytes = b"\x89PNG\r\n\x1a\n"  # minimal PNG header bytes
    app = Application(
        id=app_id,
        name="RT App Install",
        slug=f"rt-app-install-{app_id.hex[:8]}",
        repo_path="apps/rt-app-install",
        description="install parity app",
        dependencies={"react": "^18.0.0"},
        app_model="standalone_v2",
        logo_data=logo_bytes,
        logo_content_type="image/png",
        solution_id=sol_id,
    )
    db_session.add(app)

    role = Role(id=role_id, name=f"rt_app_install_role_{role_id.hex[:8]}", created_by="test")
    db_session.add(role)
    await db_session.flush()

    app_role = AppRole(app_id=app_id, role_id=role_id)
    db_session.add(app_role)
    await db_session.commit()

    try:
        capture = SolutionCaptureService(db_session)
        role_ids = [str(role_id)]
        role_names = await capture._role_names(role_ids)
        src_files, bin_files = await capture._app_source_files(app)
        logo_b64 = base64.b64encode(app.logo_data).decode("ascii") if app.logo_data else None

        produced = ManifestApp.from_row(app, roles=role_ids).view(
            Destination.INSTALL,
            extras={
                "repo_path": app.repo_path,
                "logo_b64": logo_b64,
                "logo_content_type": app.logo_content_type,
                "src_files": src_files if src_files else None,
                "bin_files": bin_files if bin_files else None,
                "role_names": role_names,
            },
        )

        # Structural invariants
        assert "path" not in produced, "install view must NOT contain 'path' key"
        assert "repo_path" in produced, "install view must contain 'repo_path' key"
        assert "organization_id" not in produced, "install view must NOT contain 'organization_id'"
        assert "logo_b64" in produced, "install view must contain 'logo_b64' (transport extra)"

        assert_golden(produced, "app_install", volatile_keys={"id", "roles", "slug", "role_names"})
    finally:
        await db_session.execute(delete(AppRole).where(AppRole.app_id == app_id))
        await db_session.execute(delete(Application).where(Application.id == app_id))
        await db_session.execute(delete(Role).where(Role.id == role_id))
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_form_git_sync_parity(db_session):
    """GIT_SYNC view of a seeded Form (with fields + workflow binding + role) matches the committed golden snapshot."""
    import uuid
    from sqlalchemy import delete
    from src.models.orm.forms import Form, FormField, FormRole
    from src.models.orm.users import Role
    from src.models.orm.workflows import Workflow
    from bifrost.manifest import ManifestForm
    from bifrost.manifest_codec import Destination
    from src.models.enums import FormAccessLevel

    form_id = uuid.uuid4()
    role_id = uuid.uuid4()
    wf_id = uuid.uuid4()

    wf = Workflow(
        id=wf_id,
        name="rt_form_wf",
        path="workflows/rt_form_wf.py",
        function_name="rt_form_wf",
        type="workflow",
        is_active=True,
    )
    db_session.add(wf)

    form = Form(
        id=form_id,
        name="rt_form_golden",
        description="parity test form",
        workflow_id=str(wf_id),
        access_level=FormAccessLevel.ROLE_BASED,
        created_by="test",
    )
    db_session.add(form)

    role = Role(id=role_id, name=f"rt_form_git_role_{role_id.hex[:8]}", created_by="test")
    db_session.add(role)
    await db_session.flush()

    ff1 = FormField(form_id=form_id, name="email", type="email", required=True, position=0, label="Email Address")
    ff2 = FormField(form_id=form_id, name="notes", type="textarea", required=False, position=1, placeholder="Optional notes")
    db_session.add(ff1)
    db_session.add(ff2)

    form_role = FormRole(form_id=form_id, role_id=role_id, assigned_by="test")
    db_session.add(form_role)
    await db_session.commit()

    try:
        roles = [str(role_id)]
        fields = [ff1, ff2]
        produced = ManifestForm.from_row(form, roles=roles, fields=fields).view(Destination.GIT_SYNC)
        assert_golden(produced, "form_git_sync", volatile_keys={"id", "organization_id", "workflow_id", "launch_workflow_id", "roles"})
    finally:
        await db_session.execute(delete(FormRole).where(FormRole.form_id == form_id))
        await db_session.execute(delete(FormField).where(FormField.form_id == form_id))
        await db_session.execute(delete(Form).where(Form.id == form_id))
        await db_session.execute(delete(Role).where(Role.id == role_id))
        await db_session.execute(delete(Workflow).where(Workflow.id == wf_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_form_install_parity(db_session):
    """INSTALL view of a seeded solution-owned Form matches the committed golden snapshot.

    organization_id must be ABSENT. workflow_path/workflow_function_name extras present.
    form_schema.fields[] include position (via _form_field_entry, not _form_field_to_schema_dict).
    """
    import uuid
    from sqlalchemy import delete
    from src.models.orm.forms import Form, FormField, FormRole
    from src.models.orm.solutions import Solution
    from src.models.orm.users import Role
    from src.models.orm.workflows import Workflow
    from src.models.orm.organizations import Organization
    from bifrost.manifest import ManifestForm
    from bifrost.manifest_codec import Destination
    from src.models.enums import FormAccessLevel
    from src.services.solutions.capture import SolutionCaptureService

    sol_id = uuid.uuid4()
    form_id = uuid.uuid4()
    role_id = uuid.uuid4()
    wf_id = uuid.uuid4()
    org_id = uuid.uuid4()

    org = Organization(id=org_id, name=f"rt-form-install-org-{org_id.hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()

    sol = Solution(
        id=sol_id,
        slug=f"rt-form-sol-{sol_id.hex[:8]}",
        name="RT Form Install Parity Sol",
    )
    db_session.add(sol)

    wf = Workflow(
        id=wf_id,
        name="rt_form_install_wf",
        path="workflows/rt_form_install.py",
        function_name="rt_form_install",
        type="workflow",
        is_active=True,
    )
    db_session.add(wf)
    await db_session.flush()

    form = Form(
        id=form_id,
        name="rt_form_install_golden",
        description="install parity form",
        workflow_id=str(wf_id),
        workflow_path="workflows/rt_form_install.py",
        workflow_function_name="rt_form_install",
        access_level=FormAccessLevel.ROLE_BASED,
        organization_id=org_id,
        solution_id=sol_id,
        created_by="test",
    )
    db_session.add(form)

    role = Role(id=role_id, name=f"rt_form_install_role_{role_id.hex[:8]}", created_by="test")
    db_session.add(role)
    await db_session.flush()

    ff1 = FormField(form_id=form_id, name="name", type="text", required=True, position=0, label="Full Name")
    ff2 = FormField(form_id=form_id, name="phone", type="tel", required=False, position=1, placeholder="Phone number")
    db_session.add(ff1)
    db_session.add(ff2)

    form_role = FormRole(form_id=form_id, role_id=role_id, assigned_by="test")
    db_session.add(form_role)
    await db_session.commit()

    try:
        capture = SolutionCaptureService(db_session)
        roles = [str(role_id)]
        role_names = await capture._role_names(roles)
        # form_schema for install uses _form_field_entry (includes position)
        form_schema = {"fields": [capture._form_field_entry(f) for f in [ff1, ff2]]}

        produced = ManifestForm.from_row(form, roles=roles).view(
            Destination.INSTALL,
            extras={
                "workflow_path": form.workflow_path,
                "workflow_function_name": form.workflow_function_name,
                "role_names": role_names,
                "form_schema": form_schema,
            },
        )

        assert "organization_id" not in produced, "install view must NOT contain 'organization_id'"
        assert "path" not in produced, "install view must NOT contain deprecated 'path'"
        assert "workflow_path" in produced, "install view must contain 'workflow_path' extra"
        assert "workflow_function_name" in produced, "install view must contain 'workflow_function_name' extra"
        assert "form_schema" in produced, "install view must contain 'form_schema'"
        # position is present in install (from _form_field_entry) but NOT in git_sync
        assert "position" in produced["form_schema"]["fields"][0], "install form_schema fields must include position"

        assert_golden(produced, "form_install", volatile_keys={"id", "roles", "workflow_id", "role_names"})
    finally:
        await db_session.execute(delete(FormRole).where(FormRole.form_id == form_id))
        await db_session.execute(delete(FormField).where(FormField.form_id == form_id))
        await db_session.execute(delete(Form).where(Form.id == form_id))
        await db_session.execute(delete(Role).where(Role.id == role_id))
        await db_session.execute(delete(Workflow).where(Workflow.id == wf_id))
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.execute(delete(Organization).where(Organization.id == org_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_form_to_orm_values_partition(db_session):
    """Assert the three-way partition of ManifestForm.to_orm_values.

    - indexer_content has id+name+description+workflow_id+form_schema
    - direct == {}
    - restamp == {organization_id: UUID, access_level: str}
    - indexer_content shape locked to a committed golden (non-circular)
    """
    import uuid
    from sqlalchemy import delete
    from src.models.orm.forms import Form, FormField
    from src.models.orm.organizations import Organization
    from bifrost.manifest import ManifestForm
    from bifrost.manifest_codec import Destination
    from src.models.enums import FormAccessLevel

    form_id = uuid.uuid4()
    org_id = uuid.uuid4()

    org = Organization(id=org_id, name=f"rt-form-partition-org-{org_id.hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()

    form = Form(
        id=form_id,
        name="rt_form_partition",
        description="partition test form",
        workflow_id="some-workflow-uuid",
        access_level=FormAccessLevel.AUTHENTICATED,
        organization_id=org_id,
        created_by="test",
    )
    db_session.add(form)
    await db_session.flush()

    ff1 = FormField(form_id=form_id, name="field1", type="text", required=True, position=0)
    ff2 = FormField(form_id=form_id, name="field2", type="select", required=False, position=1, options={"choices": ["a", "b"]})
    db_session.add(ff1)
    db_session.add(ff2)
    await db_session.commit()

    try:
        mform = ManifestForm.from_row(form, roles=[], fields=[ff1, ff2])
        parts = mform.to_orm_values(Destination.GIT_SYNC)

        # Form import is INDEXER-ONLY: only indexer_content is emitted. direct and
        # restamp are empty — organization_id/access_level are re-stamped by the
        # importer orchestration (_index_forms_from_manifest / _upsert_forms) AFTER
        # the indexer, NOT sourced from this method.
        assert parts.direct == {}, f"direct must be empty, got {parts.direct!r}"
        assert parts.restamp == {}, f"restamp must be empty, got {parts.restamp!r}"

        # indexer_content has all expected keys (id + name always present)
        ic = parts.indexer_content
        assert ic["id"] == str(form_id)
        assert ic["name"] == "rt_form_partition"
        assert ic["description"] == "partition test form"
        assert ic["workflow_id"] == "some-workflow-uuid"
        assert "form_schema" in ic

        # Lock the indexer_content shape to a committed golden. Comparing against
        # _form_content_from_manifest is now CIRCULAR (it delegates to
        # to_orm_values post-swap), so a golden is the non-circular oracle; the
        # round-trip detector covers the indexer→DB end-to-end path.
        assert_golden(ic, "form_indexer_content", volatile_keys={"id"})
    finally:
        await db_session.execute(delete(FormField).where(FormField.form_id == form_id))
        await db_session.execute(delete(Form).where(Form.id == form_id))
        await db_session.execute(delete(Organization).where(Organization.id == org_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_agent_git_sync_parity(db_session):
    """GIT_SYNC view of a seeded Agent matches the committed golden snapshot.

    Git sync carries mcp_connection_ids — confirm they appear in the view.
    """
    import uuid
    from sqlalchemy import delete
    from src.models.orm.agents import Agent
    from src.models.orm.users import Role
    from src.models.orm.agents import AgentRole
    from bifrost.manifest import ManifestAgent
    from bifrost.manifest_codec import Destination
    from src.models.enums import AgentAccessLevel

    agent_id = uuid.uuid4()
    role_id = uuid.uuid4()
    mcp_conn_id = uuid.uuid4()

    agent = Agent(
        id=agent_id,
        name="rt_agent_golden",
        description="parity test agent",
        system_prompt="You are a helpful assistant.",
        channels=["chat"],
        knowledge_sources=["kb1"],
        system_tools=["execute_workflow"],
        llm_model="claude-sonnet-4-5",
        llm_max_tokens=4096,
        max_iterations=10,
        max_token_budget=50000,
        max_run_timeout=300,
        access_level=AgentAccessLevel.ROLE_BASED,
        is_active=True,
        created_by="test",
    )
    db_session.add(agent)

    role = Role(id=role_id, name=f"rt_agent_git_role_{role_id.hex[:8]}", created_by="test")
    db_session.add(role)
    await db_session.flush()

    agent_role = AgentRole(agent_id=agent_id, role_id=role_id, assigned_by="test")
    db_session.add(agent_role)
    await db_session.commit()

    try:
        roles = [str(role_id)]
        tool_ids: list[str] = []
        delegated_agent_ids: list[str] = []
        mcp_connection_ids = [str(mcp_conn_id)]
        produced = ManifestAgent.from_row(
            agent,
            roles=roles,
            tool_ids=tool_ids,
            delegated_agent_ids=delegated_agent_ids,
            mcp_connection_ids=mcp_connection_ids,
        ).view(Destination.GIT_SYNC)

        # git_sync MUST carry mcp_connection_ids
        assert "mcp_connection_ids" in produced, "git_sync view must contain mcp_connection_ids"
        assert produced["mcp_connection_ids"] == mcp_connection_ids

        assert_golden(
            produced,
            "agent_git_sync",
            volatile_keys={"id", "organization_id", "roles", "mcp_connection_ids"},
        )
    finally:
        await db_session.execute(delete(AgentRole).where(AgentRole.agent_id == agent_id))
        await db_session.execute(delete(Agent).where(Agent.id == agent_id))
        await db_session.execute(delete(Role).where(Role.id == role_id))
        await db_session.commit()


def test_agent_from_row_coerces_uuid_junction_ids():
    """from_row must accept raw UUID junction ids and coerce them to strings.

    Solution capture (capture._junction_ids) returns list[UUID] for
    tool_ids / delegated_agent_ids / mcp_connection_ids, whereas the git-sync
    generator pre-stringifies them. Both feed ManifestAgent.from_row, whose
    fields are list[str]. Without coercion, capturing any agent that has tools
    or delegated agents raises a Pydantic ValidationError (regression vs the
    pre-unification capture path, which put raw UUIDs straight into the bundle
    dict and never validated). Pin the coercion here.
    """
    import uuid
    from bifrost.manifest import ManifestAgent

    tool_uuid = uuid.uuid4()
    deleg_uuid = uuid.uuid4()
    mcp_uuid = uuid.uuid4()

    class _AgentRow:
        id = uuid.uuid4()
        name = "a"
        description = None
        system_prompt = "x"
        channels = []
        access_level = None
        knowledge_sources = []
        system_tools = []
        llm_model = None
        llm_max_tokens = None
        max_iterations = None
        max_token_budget = None
        organization_id = None

    m = ManifestAgent.from_row(
        _AgentRow(),
        roles=[],
        tool_ids=[tool_uuid],
        delegated_agent_ids=[deleg_uuid],
        mcp_connection_ids=[mcp_uuid],
    )
    assert m.tool_ids == [str(tool_uuid)]
    assert m.delegated_agent_ids == [str(deleg_uuid)]
    assert m.mcp_connection_ids == [str(mcp_uuid)]
    assert all(isinstance(x, str) for x in m.tool_ids)


@pytest.mark.e2e
async def test_agent_install_parity(db_session):
    """INSTALL view of a seeded solution-owned Agent matches the committed golden snapshot.

    Install has max_run_timeout (transport extra) but NOT mcp_connection_ids.
    organization_id must be ABSENT.
    """
    import uuid
    from sqlalchemy import delete
    from src.models.orm.agents import Agent
    from src.models.orm.solutions import Solution
    from src.models.orm.users import Role
    from src.models.orm.agents import AgentRole
    from bifrost.manifest import ManifestAgent
    from bifrost.manifest_codec import Destination
    from src.models.enums import AgentAccessLevel
    from src.services.solutions.capture import SolutionCaptureService

    sol_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    role_id = uuid.uuid4()

    sol = Solution(
        id=sol_id,
        slug=f"rt-sol-{sol_id.hex[:8]}",
        name="RT Agent Install Parity Sol",
    )
    db_session.add(sol)

    agent = Agent(
        id=agent_id,
        name="rt_agent_install_golden",
        description="install parity agent",
        system_prompt="Install test system prompt.",
        channels=["chat", "email"],
        knowledge_sources=[],
        system_tools=["execute_workflow"],
        llm_model=None,
        llm_max_tokens=None,
        max_iterations=20,
        max_token_budget=None,
        max_run_timeout=600,
        access_level=AgentAccessLevel.AUTHENTICATED,
        is_active=True,
        solution_id=sol_id,
        created_by="test",
    )
    db_session.add(agent)

    role = Role(id=role_id, name=f"rt_agent_install_role_{role_id.hex[:8]}", created_by="test")
    db_session.add(role)
    await db_session.flush()

    agent_role = AgentRole(agent_id=agent_id, role_id=role_id, assigned_by="test")
    db_session.add(agent_role)
    await db_session.commit()

    try:
        capture = SolutionCaptureService(db_session)
        roles = [str(role_id)]
        role_names = await capture._role_names(roles)

        produced = ManifestAgent.from_row(
            agent,
            roles=roles,
            tool_ids=[],
            delegated_agent_ids=[],
        ).view(
            Destination.INSTALL,
            extras={
                "max_run_timeout": agent.max_run_timeout,
                "role_names": role_names,
            },
        )

        # Structural invariants
        assert "organization_id" not in produced, "install view must NOT contain 'organization_id'"
        assert "mcp_connection_ids" not in produced, "install view must NOT contain 'mcp_connection_ids'"
        assert "max_run_timeout" in produced, "install view must contain 'max_run_timeout' (transport extra)"
        assert produced["max_run_timeout"] == 600

        assert_golden(
            produced,
            "agent_install",
            volatile_keys={"id", "roles", "role_names"},
        )
    finally:
        await db_session.execute(delete(AgentRole).where(AgentRole.agent_id == agent_id))
        await db_session.execute(delete(Agent).where(Agent.id == agent_id))
        await db_session.execute(delete(Role).where(Role.id == role_id))
        await db_session.execute(delete(Solution).where(Solution.id == sol_id))
        await db_session.commit()


@pytest.mark.e2e
async def test_agent_to_orm_values_partition(db_session):
    """Assert the three-way partition of ManifestAgent.to_orm_values.

    - indexer_content has id+name+description+system_prompt+non-empty lists+mcp_connection_ids
    - direct == {id, name, system_prompt}
    - restamp == {access_level, max_iterations, max_token_budget}
    - max_run_timeout NOT in indexer_content (it's a transport extra, not a model field)
    - indexer_content shape locked to committed golden (non-circular oracle)
    """
    import uuid
    from sqlalchemy import delete
    from src.models.orm.agents import Agent
    from src.models.orm.organizations import Organization
    from bifrost.manifest import ManifestAgent
    from bifrost.manifest_codec import Destination
    from src.models.enums import AgentAccessLevel

    agent_id = uuid.uuid4()
    org_id = uuid.uuid4()
    mcp_id = uuid.uuid4()

    org = Organization(id=org_id, name=f"rt-agent-partition-org-{org_id.hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()

    agent = Agent(
        id=agent_id,
        name="rt_agent_partition",
        description="partition test agent",
        system_prompt="Partition test prompt.",
        channels=["chat"],
        knowledge_sources=["kb_partition"],
        system_tools=["execute_workflow"],
        llm_model="claude-haiku-4-5",
        llm_max_tokens=1024,
        max_iterations=5,
        max_token_budget=10000,
        max_run_timeout=120,
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=org_id,
        is_active=True,
        created_by="test",
    )
    db_session.add(agent)
    await db_session.commit()

    try:
        magent = ManifestAgent.from_row(
            agent,
            roles=[],
            tool_ids=["tool-uuid-1"],
            delegated_agent_ids=[],
            mcp_connection_ids=[str(mcp_id)],
        )
        parts = magent.to_orm_values(Destination.GIT_SYNC)

        # Agent import is INDEXER-ONLY: only indexer_content is emitted. direct and
        # restamp are empty — id/name/system_prompt are resolved on the metadata row
        # and access_level/max_iterations/max_token_budget (+ max_run_timeout extra)
        # are re-stamped by the importer orchestration (_resolve_agent /
        # _index_agents_from_manifest / _upsert_agents) AFTER the indexer, NOT
        # sourced from this method.
        assert parts.direct == {}, f"direct must be empty, got {parts.direct!r}"
        assert parts.restamp == {}, f"restamp must be empty, got {parts.restamp!r}"

        # max_run_timeout NOT in indexer_content (transport extra, not a model field)
        assert "max_run_timeout" not in parts.indexer_content, (
            "max_run_timeout must NOT appear in indexer_content"
        )

        # Lock the indexer_content shape to a committed golden.
        assert_golden(parts.indexer_content, "agent_indexer_content", volatile_keys={"id", "mcp_connection_ids", "tool_ids"})
    finally:
        await db_session.execute(delete(Agent).where(Agent.id == agent_id))
        await db_session.execute(delete(Organization).where(Organization.id == org_id))
        await db_session.commit()


# P4: child models that are reconciled by their parent resolver have no standalone
# orm path — to_orm_values raises NotImplementedError for either destination. The
# parent cases (Integration/MCPServer/Config/SolutionConfigSchema) are covered
# above; these lock the leaf children that were previously only covered indirectly.
@pytest.mark.parametrize("dest", [Destination.GIT_SYNC, Destination.INSTALL])
def test_child_models_have_no_standalone_orm_path(dest):
    from bifrost.manifest import (
        ManifestEventSubscription,
        ManifestIntegrationConfigSchema,
        ManifestIntegrationMapping,
        ManifestMCPConnection,
        ManifestMCPConnectionTool,
        ManifestOAuthProvider,
    )

    children = [
        ManifestIntegrationConfigSchema(key="k", type="string"),
        ManifestOAuthProvider(provider_name="acme"),
        ManifestIntegrationMapping(entity_id="ent-1"),
        ManifestEventSubscription(id="00000000-0000-0000-0000-000000000001"),
        ManifestMCPConnectionTool(tool_name="do_thing"),
        ManifestMCPConnection(organization_id="11111111-1111-1111-1111-111111111111", client_id="cid"),
    ]
    for child in children:
        with pytest.raises(NotImplementedError):
            child.to_orm_values(dest)


# ---------------------------------------------------------------------------
# Structural guard against "Leak A": a field that to_orm_values(INSTALL) writes
# from self.X but that view(INSTALL)'s allowlist drops will silently reconstitute
# to its model DEFAULT on a Solution redeploy (deploy does
# `Model(**view).to_orm_values(INSTALL)`), clearing the real DB value. The
# allowlist and to_orm_values are two hand-maintained lists; nothing fails when
# they disagree. This test makes that disagreement un-mergeable: for each
# allowlist entity, every install-imported model field must SURVIVE the
# capture->view->reconstruct->import round-trip (or be explicitly env-stripped).
#
# B2 (Workflow.tool_description dropped from the install allowlist) is exactly
# this class; this guard fails until B2 is fixed.
# ---------------------------------------------------------------------------

# Per-entity: a factory that builds a model with a NON-DEFAULT sentinel in every
# install-imported field, plus the set of fields that install INTENTIONALLY strips
# (env-specific; deploy re-stamps them — organization_id, access_level, etc.).
def _install_roundtrip_specs():
    from bifrost.manifest import ManifestWorkflow, ManifestApp

    workflow = ManifestWorkflow(
        id="11111111-1111-1111-1111-111111111111",
        name="wf_sentinel",
        function_name="fn_sentinel",
        path="workflows/sentinel.py",
        type="tool",
        description="DESC_SENTINEL",
        tool_description="TOOLDESC_SENTINEL",
        endpoint_enabled=True,
        public_endpoint=True,
        timeout_seconds=4242,
        category="CAT_SENTINEL",
        tags=["t_sentinel"],
        access_level="role_based",
        organization_id="22222222-2222-2222-2222-222222222222",
    )
    app = ManifestApp(
        id="33333333-3333-3333-3333-333333333333",
        name="app_sentinel",
        path="apps/app-sentinel",
        slug="app-sentinel",
        description="APP_DESC_SENTINEL",
        dependencies={"left-pad": "1.0.0"},
        app_model="standalone_v2",
        access_level="role_based",
        organization_id="44444444-4444-4444-4444-444444444444",
    )
    def reconstruct_default(cls, bundle):
        # What deploy._upsert_workflows does: Model(**view-fields).
        return cls(**{k: v for k, v in bundle.items() if k in cls.model_fields})

    def reconstruct_app(cls, bundle):
        # What deploy._upsert_apps does: path is NOT in the view (App emits the
        # transport extra repo_path instead); deploy re-derives path from
        # repo_path or falls back to apps/{slug} before rebuilding the model.
        fields = {k: v for k, v in bundle.items() if k in cls.model_fields}
        if "path" not in fields:
            fields["path"] = bundle.get("repo_path") or f"apps/{bundle['slug']}"
        return cls(**fields)

    return [
        # (model, reconstruct_fn, install_stripped fields deploy re-stamps and may not survive)
        (workflow, reconstruct_default, {"organization_id", "access_level", "is_active"}),
        (app, reconstruct_app, {"organization_id", "access_level", "is_active", "repo_path"}),
    ]


@pytest.mark.parametrize("model,reconstruct,install_stripped", _install_roundtrip_specs())
def test_install_view_preserves_every_imported_field(model, reconstruct, install_stripped):
    """view(INSTALL) must carry every field to_orm_values(INSTALL) reads, so a
    redeploy reconstructs the real value, not the model default."""
    cls = type(model)
    before = model.to_orm_values(Destination.INSTALL).direct

    # Capture -> bundle dict -> reconstruct (exactly as the deployer does) -> import again.
    bundle = model.view(Destination.INSTALL)
    reconstructed = reconstruct(cls, bundle)
    after = reconstructed.to_orm_values(Destination.INSTALL).direct

    dropped = {
        k for k in before
        if k not in install_stripped and before.get(k) != after.get(k)
    }
    assert not dropped, (
        f"{cls.__name__}: install round-trip silently changed field(s) {sorted(dropped)} "
        f"(view(INSTALL) drops them, so redeploy reconstitutes the model default). "
        f"Add them to the install allowlist, or to install_stripped if deploy re-stamps them.\n"
        f"  before={ {k: before.get(k) for k in dropped} }\n"
        f"  after = { {k: after.get(k) for k in dropped} }"
    )
