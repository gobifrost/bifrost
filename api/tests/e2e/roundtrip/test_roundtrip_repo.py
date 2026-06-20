"""``_repo`` git-sync round-trip — the FIRST harness driver against REAL code.

Drives the real ``_repo`` export/import:
  export  = ``GitHubSyncService._regenerate_manifest_to_dir`` (DB -> .bifrost/*.yaml)
  import  = ``GitHubSyncService._import_all_entities``       (.bifrost/*.yaml -> DB)

The round trip seeds an entity in the DB with rich (non-default) values, exports
the split-file manifest, DELETES the entity to force a real incremental import
delta, then imports it back.  It asserts the import actually touched the entity
(``count > 0``) BEFORE comparing the re-serialized manifest entry field-by-field
against ``REPO_POLICY``.  A red here is a FINDING (a field the ``_repo`` path
drops), not a test bug — do NOT loosen the policy to make it green.

Lives under ``tests/e2e/`` so ``./test.sh e2e`` (path-based collection) picks it
up; the harness support modules live in ``tests/roundtrip/`` and are imported.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.field_classes import field_class_of
from src.models.orm.workflows import Workflow
from tests.roundtrip.assertions import assert_field_roundtrip, assert_no_secret_leak, pair_rows
from tests.roundtrip.paths import (
    DELETERS,
    REPO_POLICY,
    manifest_entry_for,
    manifest_text,
    repo_export,
    repo_import,
)

pytestmark = pytest.mark.e2e


SAMPLE_WORKFLOW_PY = """\
from bifrost import workflow


@workflow(name="RoundTrip WF")
def roundtrip_wf(message: str) -> dict:
    \"\"\"A workflow for round-trip testing.\"\"\"
    return {"result": message}
"""


# ---------------------------------------------------------------------------
# Dependency-closure fixture builders.  The REAL import SKIPS rows whose
# referenced files are missing (workflow file existence, github_sync.py:704),
# so we must write the .py file to work_dir and seed a closed dependency set.
# A skipped row is a FIXTURE GAP (noise red), not a real field drop.
# ---------------------------------------------------------------------------


async def seed_workflow(db: AsyncSession, work_dir: Path) -> str:
    """Seed a global (org=None, no roles) Workflow + write its .py file.

    Every content field is set to a NON-DEFAULT value so a dropped field shows
    up as a changed value, not a coincidental default match.  Global + no roles
    means no org/role closure is needed (env fields stay empty).
    """
    wf_path = "workflows/roundtrip_wf.py"
    (work_dir / "workflows").mkdir(parents=True, exist_ok=True)
    (work_dir / wf_path).write_text(SAMPLE_WORKFLOW_PY)

    wid = uuid4()
    wf = Workflow(
        id=wid,
        name="RoundTrip Display Name",
        function_name="roundtrip_wf",
        path=wf_path,
        type="workflow",
        description="seeded description for round trip",
        tool_description="LLM-facing tool description",
        access_level="authenticated",
        endpoint_enabled=True,
        timeout_seconds=999,
        public_endpoint=True,
        category="RoundTripCategory",
        tags=["alpha", "beta"],
        is_active=True,
    )
    db.add(wf)
    await db.commit()
    return str(wid)


async def seed_table(db: AsyncSession, work_dir: Path) -> str:
    """Seed a global Table — no closure needed (global, policies inline)."""
    from src.models.orm.tables import Table

    tid = uuid4()
    table = Table(
        id=tid,
        name=f"rt_table_{tid.hex[:6]}",
        description="seeded table description",
        organization_id=None,
        schema={"columns": [{"name": "title", "type": "string"}]},
        access={"policies": [
            {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": None},
        ]},
    )
    db.add(table)
    await db.commit()
    return str(tid)


async def seed_config(db: AsyncSession, work_dir: Path) -> str:
    """Seed a global (non-integration) string Config — no closure needed."""
    from src.models.enums import ConfigType
    from src.models.orm.config import Config

    cid = uuid4()
    cfg = Config(
        id=cid,
        key=f"RT_CONFIG_{cid.hex[:6]}",
        value={"value": "round-trip-value"},
        config_type=ConfigType.STRING,
        description="seeded config description",
        organization_id=None,
        integration_id=None,
        updated_by="roundtrip@test.local",
    )
    db.add(cfg)
    await db.commit()
    return str(cid)


async def seed_claim(db: AsyncSession, work_dir: Path) -> str:
    """Seed a CustomClaim + its org (organization_id is REQUIRED on claims).

    The org is part of the dependency closure — it round-trips alongside, and
    only the claim is deleted to force the import delta.
    """
    from src.models.orm.custom_claims import CustomClaim
    from src.models.orm.organizations import Organization

    org_id = uuid4()
    db.add(Organization(
        id=org_id,
        name=f"RT Claim Org {org_id.hex[:6]}",
        domain=f"rt-{org_id.hex[:8]}.test",
        created_by="roundtrip@test.local",
    ))
    cid = uuid4()
    db.add(CustomClaim(
        id=cid,
        name=f"rt_claim_{cid.hex[:6]}",
        description="seeded claim description",
        organization_id=org_id,
        type="list",
        query={"table": "rt_source", "where": None, "select": "value"},
    ))
    await db.commit()
    return str(cid)


async def seed_event_source(db: AsyncSession, work_dir: Path) -> str:
    """Seed a schedule EventSource + its ScheduleSource (no subscriptions).

    Schedule type avoids the webhook-integration dependency; no subscriptions
    avoids the workflow dependency.  generate_manifest only serializes
    is_active sources, so is_active=True is mandatory.
    """
    from src.models.enums import ScheduleOverlapPolicy
    from src.models.orm.events import EventSource, ScheduleSource

    esid = uuid4()
    db.add(EventSource(
        id=esid,
        name=f"rt_schedule_{esid.hex[:6]}",
        source_type="schedule",
        organization_id=None,
        is_active=True,
        created_by="roundtrip@test.local",
    ))
    db.add(ScheduleSource(
        id=uuid4(),
        event_source_id=esid,
        cron_expression="0 9 * * *",
        timezone="America/New_York",
        enabled=True,
        overlap_policy=ScheduleOverlapPolicy.SKIP,
    ))
    await db.commit()
    return str(esid)


async def seed_form(db: AsyncSession, work_dir: Path) -> str:
    """Seed a Form + its referenced Workflow (.py file) + one FormField.

    The workflow is the dependency closure (the form's ``workflow_id`` ref must
    resolve); both are written so the form indexer does not skip on re-import.
    Only the form is deleted to force the import delta.
    """
    from src.models.enums import FormAccessLevel
    from src.models.orm.forms import Form, FormField
    from src.models.orm.workflows import Workflow

    wf_path = "workflows/rt_form_wf.py"
    (work_dir / "workflows").mkdir(parents=True, exist_ok=True)
    (work_dir / wf_path).write_text(SAMPLE_WORKFLOW_PY)
    wid = uuid4()
    db.add(Workflow(
        id=wid, name="RT Form WF", function_name="roundtrip_wf", path=wf_path,
        type="workflow", access_level="authenticated", is_active=True,
    ))

    fid = uuid4()
    db.add(Form(
        id=fid,
        name="RoundTrip Form",
        description="seeded form description",
        workflow_id=str(wid),
        access_level=FormAccessLevel.AUTHENTICATED,
        organization_id=None,
        allowed_query_params=["foo"],
        is_active=True,
        created_by="roundtrip@test.local",
    ))
    db.add(FormField(
        id=uuid4(), form_id=fid, name="title", type="text", required=True,
        position=0, label="Title", placeholder="Enter title",
    ))
    await db.commit()
    return str(fid)


async def seed_agent(db: AsyncSession, work_dir: Path) -> str:
    """Seed a minimal global Agent (no tools/delegations/roles) — inline content
    only, so no dependency closure beyond the agent itself."""
    from src.models.enums import AgentAccessLevel
    from src.models.orm.agents import Agent

    aid = uuid4()
    db.add(Agent(
        id=aid,
        name="RoundTrip Agent",
        description="seeded agent description",
        system_prompt="You are a round-trip test agent.",
        channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
        knowledge_sources=["kb-alpha"],
        system_tools=["search_knowledge"],
        llm_model="claude-test",
        llm_max_tokens=2048,
        max_iterations=11,
        max_token_budget=22222,
        max_run_timeout=777,  # NOT a ManifestAgent field — does not travel via _repo
        organization_id=None,
        created_by="roundtrip@test.local",
    ))
    await db.commit()
    return str(aid)


async def seed_integration(db: AsyncSession, work_dir: Path) -> str:
    """Seed an Integration with a config-schema item + an OAuth provider.

    Standalone (no closure).  Exercises the nested CONTENT (config_schema,
    oauth_provider).  The OAuth ``encrypted_client_secret`` is intentionally
    NEVER serialized (a documented secret drop, not a Manifest field); a real
    ``client_id`` (not the ``__NEEDS_SETUP__`` sentinel) must round-trip.
    """
    from src.models.orm.integrations import Integration, IntegrationConfigSchema
    from src.models.orm.oauth import OAuthProvider

    iid = uuid4()
    db.add(Integration(
        id=iid,
        name=f"rt-integration-{iid.hex[:6]}",
        entity_id="tenant_id",
        entity_id_name="Tenant",
        default_entity_id="default-tenant",
    ))
    db.add(IntegrationConfigSchema(
        id=uuid4(), integration_id=iid, key="base_url",
        type="string", required=True, position=0, description="Base URL",
    ))
    db.add(OAuthProvider(
        id=uuid4(),
        provider_name=f"rtprov{iid.hex[:6]}",
        display_name="RT Provider",
        client_id="real-client-id-not-sentinel",
        encrypted_client_secret=b"SECRET_SENTINEL_DO_NOT_LEAK",
        integration_id=iid,
        authorization_url="https://example.test/authorize",
        token_url="https://example.test/token",
        scopes=["read", "write"],
    ))
    await db.commit()
    return str(iid)


# Registry: collection -> seeder.  Workflow is proven end-to-end; more entities
# get added here as their dependency closures are wired (Task 7).
SEEDERS = {
    "workflows": seed_workflow,
    "tables": seed_table,
    "configs": seed_config,
    "claims": seed_claim,
    "events": seed_event_source,
    "integrations": seed_integration,
    "forms": seed_form,
    "agents": seed_agent,
}


async def _run_repo_roundtrip(
    db: AsyncSession,
    work_dir: Path,
    collection: str,
) -> tuple[dict, dict]:
    """Drive one entity through the real ``_repo`` export -> delete -> import.

    Returns (before_entry, after_entry) — the serialized manifest dicts produced
    by ``generate_manifest`` before export and after re-import.
    """
    seeder = SEEDERS[collection]
    entity_id = await seeder(db, work_dir)

    # BEFORE: the manifest entry the seeded DB row produces (via real generator).
    before = await manifest_entry_for(db, collection, entity_id)
    assert before is not None, f"{collection} {entity_id} did not serialize into the manifest"

    # EXPORT (real split-file writer): DB -> .bifrost/*.yaml in work_dir.
    await repo_export(db, work_dir)

    # FORCE A REAL IMPORT DELTA: delete the entity so _diff_and_collect sees the
    # manifest entity as new on re-import (else the incremental import no-ops).
    await DELETERS[collection](db, entity_id)
    gone = await manifest_entry_for(db, collection, entity_id)
    assert gone is None, "deleter did not remove the entity — delta would be empty"

    # IMPORT (real wrapper, runs indexers): .bifrost/*.yaml -> DB.
    count, _changes = await repo_import(db, work_dir)
    assert count > 0, (
        "_import_all_entities was a no-op (count=0) — the incremental diff found "
        "nothing to import; the round trip never ran the resolver/indexers"
    )

    # AFTER: re-serialize the re-imported row.
    after = await manifest_entry_for(db, collection, entity_id)
    assert after is not None, (
        f"{collection} {entity_id} was reported imported (count={count}) but does "
        f"not appear in the regenerated manifest — the import skipped the row"
    )
    return before, after


# ---------------------------------------------------------------------------
# Workflow — proven end-to-end first.
# ---------------------------------------------------------------------------


async def test_repo_roundtrip_workflow(db_session: AsyncSession, tmp_path: Path):
    """Every ManifestWorkflow field obeys REPO_POLICY across a real _repo round trip."""
    import bifrost.manifest as m

    before, after = await _run_repo_roundtrip(db_session, tmp_path, "workflows")

    # Pair by id (same-env _repo keeps ids).
    (b, a), = pair_rows(m.ManifestWorkflow, [before], [after], "by_id", REPO_POLICY)

    reds: list[str] = []
    for field in m.ManifestWorkflow.model_fields:
        try:
            assert_field_roundtrip(m.ManifestWorkflow, field, b, a, REPO_POLICY, row=b)
        except AssertionError as e:
            cls = field_class_of(m.ManifestWorkflow, field, b)
            reds.append(f"{field} ({cls.value}): {e}")

    assert not reds, "ManifestWorkflow _repo round-trip drops:\n" + "\n".join(reds)


# ---------------------------------------------------------------------------
# Additional entities — each wired with a real dependency-closure seeder.
# The _repo path serializes via generate_manifest -> model_dump, so the emitted
# dict IS the Manifest* model (no transport extras); the completeness layer is
# satisfied trivially.  REPO_POLICY keeps everything except secrets.
# ---------------------------------------------------------------------------

import pytest as _pytest  # noqa: E402


@_pytest.mark.parametrize(
    "collection,model_name",
    [
        ("tables", "ManifestTable"),
        ("configs", "ManifestConfig"),
        ("claims", "ManifestCustomClaim"),
        ("events", "ManifestEventSource"),
        ("integrations", "ManifestIntegration"),
        ("forms", "ManifestForm"),
        ("agents", "ManifestAgent"),
    ],
)
async def test_repo_roundtrip_entity(
    db_session: AsyncSession, tmp_path: Path, collection: str, model_name: str
):
    """Every Manifest* field obeys REPO_POLICY across a real _repo round trip."""
    import bifrost.manifest as m

    model = getattr(m, model_name)
    before, after = await _run_repo_roundtrip(db_session, tmp_path, collection)

    (b, a), = pair_rows(model, [before], [after], "by_id", REPO_POLICY)

    reds: list[str] = []
    for field in model.model_fields:
        try:
            assert_field_roundtrip(model, field, b, a, REPO_POLICY, row=b)
        except AssertionError as e:
            cls = field_class_of(model, field, b)
            reds.append(f"{field} ({cls.value}): {e}")

    assert not reds, f"{model_name} _repo round-trip drops:\n" + "\n".join(reds)


async def test_form_field_auto_fill_is_dropped_below_manifest(db_session: AsyncSession):
    """DOCUMENTED PRODUCT-DECISION DROP: FormField.auto_fill never serializes.

    ``auto_fill`` is a ``FormField`` column (forms.py:71) that
    ``_form_field_to_schema_dict`` (manifest_generator.py:110-129) does NOT emit
    into ``form_schema.fields``.  It therefore travels through NEITHER the _repo
    NOR the solution path — the field-class harness is structurally blind to it
    because the loss happens BELOW the manifest serialization boundary (inside
    the opaque ``form_schema`` CONTENT blob), not at a ``Manifest*`` field.

    This test PINS the current (dropping) behavior so the finding is executable.
    Fixing it (adding ``auto_fill`` to ``_form_field_to_schema_dict`` + the form
    indexer's FormField parse) is a PRODUCT DECISION — auto_fill may be
    environment-specific prefill data that should NOT be shared — so it is left
    as a documented finding, not silently fixed.  Mirror of the Phase-1
    tool_description fix IF the product decision is "it should travel".
    """
    from src.services.manifest_generator import _form_field_to_schema_dict
    from src.models.orm.forms import FormField

    field = FormField(
        id=uuid4(), name="ticket", type="text", required=False, position=0,
        auto_fill={"sibling": "metadata.path"},
    )
    rendered = _form_field_to_schema_dict(field)
    assert "auto_fill" not in rendered, (
        "auto_fill is now serialized — if this is intentional, add it to the form "
        "indexer's FormField parse on import and update the product-decision finding."
    )


async def test_repo_roundtrip_mcp_server(db_session: AsyncSession, tmp_path: Path):
    """MCP server round-trips through the _repo manifest via the DIRECT resolver.

    MCP servers are _repo-only (NOT a Solution entity) AND take a DIFFERENT
    import path: _diff_and_collect omits mcp_servers (manifest_import.py:82) and
    has_entities omits them (github_sync.py:1151), so _import_all_entities never
    touches them.  The real import is ManifestResolver.plan_import driving
    _resolve_mcp_server, gated on the server id being in changed_ids
    (manifest_import.py:789).  This test drives that exact path.

    Known intentional drop (documented, NOT a Manifest field): the per-connection
    encrypted_client_secret is never serialized — secrets stay out of the
    manifest, like Config values (manifest.py ManifestMCPConnection docstring).
    """
    import bifrost.manifest as m
    from src.models.orm.external_mcp import MCPServer
    from tests.roundtrip.paths import (
        REPO_POLICY,
        make_repo_sync_service,
        manifest_entry_for,
        repo_export,
    )

    sid = uuid4()
    db_session.add(MCPServer(
        id=sid,
        name=f"rt-mcp-{sid.hex[:6]}",
        server_url="https://mcp.example.test/sse",
        redirect_url="https://app.test/oauth/callback",
        discovery_metadata={"version": "1.0"},
        organization_id=None,
        is_active=True,
    ))
    await db_session.commit()

    before = await manifest_entry_for(db_session, "mcp_servers", str(sid))
    assert before is not None, "MCP server did not serialize into the manifest"

    await repo_export(db_session, tmp_path)

    # Force a delta: delete the server, then drive the DIRECT resolver with the
    # server id explicitly in changed_ids (the path _import_all_entities skips).
    from sqlalchemy import delete

    await db_session.execute(delete(MCPServer).where(MCPServer.id == sid))
    await db_session.commit()

    from bifrost.manifest import read_manifest_from_dir

    service = make_repo_sync_service(db_session, tmp_path)
    manifest = read_manifest_from_dir(tmp_path / ".bifrost")
    await service._resolver.plan_import(manifest, tmp_path, changed_ids={str(sid)})
    await db_session.commit()

    after = await manifest_entry_for(db_session, "mcp_servers", str(sid))
    assert after is not None, "MCP server was not re-imported by the direct resolver"

    reds: list[str] = []
    for field in m.ManifestMCPServer.model_fields:
        try:
            assert_field_roundtrip(m.ManifestMCPServer, field, before, after, REPO_POLICY, row=before)
        except AssertionError as e:
            cls = field_class_of(m.ManifestMCPServer, field, before)
            reds.append(f"{field} ({cls.value}): {e}")
    assert not reds, "ManifestMCPServer _repo round-trip drops:\n" + "\n".join(reds)


async def test_repo_manifest_has_no_secret_leak(db_session: AsyncSession, tmp_path: Path):
    """The written _repo manifest text must not contain a secret sentinel.

    Workflow carries no secret fields, but this guards the on-disk envelope: if a
    future secret-class field leaks into the plaintext .bifrost/ files, this bites.
    """
    await seed_workflow(db_session, tmp_path)
    await repo_export(db_session, tmp_path)
    text = manifest_text(tmp_path)
    assert text, "export wrote no manifest text"
    # A sentinel that a SECRET-class value would carry if it leaked.
    assert_no_secret_leak(text, ["SECRET_SENTINEL_DO_NOT_LEAK"])
