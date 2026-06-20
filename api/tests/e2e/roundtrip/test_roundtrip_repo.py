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
