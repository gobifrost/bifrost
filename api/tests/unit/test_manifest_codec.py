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
