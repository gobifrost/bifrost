"""Solution shareable + full-backup round-trip — the harness over REAL solution
export/install code.

Drives the real solution distribution pipeline in-process:
  export  = ``SolutionCaptureService.bundle_for`` -> ``build_workspace_zip``
            (the pair the ``GET /api/solutions/{id}/export`` router calls)
  install = ``zip_install.install_zip`` -> ``SolutionDeployer.deploy``
            (per-install id remap via ``solution_entity_id``)

The round trip seeds a SOURCE solution that owns rich (non-default) entities,
exports the workspace zip, installs it into a FRESH TARGET org, then reads the
INSTALLED entities back through the SAME ``bundle_for`` serializer and pairs them
``by_remap`` (``expected_id(before) = solution_entity_id(installed.id, src_id)``).
Each field is then asserted against ``SOLUTION_SHAREABLE`` / ``SOLUTION_FULL``.

A red here is a FINDING (a field the solution path drops / mis-transforms), NOT a
test bug — do NOT loosen the policy to make it green.  A red that is a KNOWN
transform gets a code-cited ``FIELD_OVERRIDES`` entry; a real drop is recorded
for Task 7.

Plus the three envelope checks (separate from the manifest field round trip):
  - table_data: full export ``include_data=True`` carries rows; shareable carries none.
  - secret envelope: a secret config value survives full encrypt->decrypt; absent
    from the shareable/_repo plaintext (leak scan).
  - connection declaration: ``build_integration_template`` emits a scrubbed skeleton
    (no client_id/secret/token/org), schema shape present.

Lives under ``tests/e2e/`` so ``./test.sh e2e`` (path collection) picks it up; the
harness support modules live in ``tests/roundtrip/`` and are imported.
"""
from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import bifrost.manifest as m
from bifrost.field_classes import field_class_of
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from tests.roundtrip.assertions import (
    assert_dict_keys_accounted,
    assert_field_roundtrip,
    assert_no_secret_leak,
    pair_rows,
)
from tests.roundtrip.paths import (
    EXTRA_FIELD_POLICY,
    FIELD_OVERRIDES,
    SOLUTION_FULL_POLICY,
    SOLUTION_SHAREABLE_POLICY,
    expected_solution_id,
    remap_ref_for,
    solution_bundle_entries,
    solution_export_zip,
    solution_install_zip,
)


def _extra_keys_for(model: type) -> set[str]:
    """The EXTRA_FIELD_POLICY keys declared for *model* (the transport extras)."""
    return {k[1] for k in EXTRA_FIELD_POLICY if k[0] == model.__name__}


def _assert_complete(model: type, before: dict, after: dict) -> None:
    """Completeness oracle: every emitted key (source + installed) is either a
    classified Manifest field or a declared EXTRA_FIELD_POLICY extra."""
    extras = _extra_keys_for(model)
    assert_dict_keys_accounted(model, before, extras)
    assert_dict_keys_accounted(model, after, extras)

pytestmark = pytest.mark.e2e


SECRET_SENTINEL = "SECRET_SENTINEL_DO_NOT_LEAK"


@pytest.fixture(autouse=True)
def _solution_write_guard():
    """Prod-faithful: the always-on read-only ``before_flush`` guard fires on
    every solution-managed ORM write in prod (core/database.py:136). Install it
    so a deploy that mutates an ORM object would 500 here exactly as in prod."""
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    yield


# ---------------------------------------------------------------------------
# Seeders: build a SOURCE solution owning one rich entity (all content fields
# set to NON-DEFAULT values so a dropped field shows as a changed value).
# ---------------------------------------------------------------------------


async def _make_solution(db: AsyncSession, *, org_id: UUID | None = None) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"rt-sol-{uuid.uuid4().hex[:8]}",
        name="RoundTrip Solution",
        organization_id=org_id,
    )
    db.add(sol)
    await db.flush()
    return sol


async def seed_solution_workflow(db: AsyncSession, sol: Solution) -> str:
    """Seed a solution-managed Workflow with every content field non-default."""
    wid = uuid.uuid4()
    wf = Workflow(
        id=wid,
        name="RoundTrip WF Display",
        function_name="roundtrip_wf",
        path="workflows/roundtrip_wf.py",
        type="workflow",
        description="seeded description for solution round trip",
        tool_description="LLM-facing tool description",
        access_level="authenticated",
        endpoint_enabled=True,
        timeout_seconds=999,
        public_endpoint=True,
        category="RoundTripCategory",
        tags=["alpha", "beta"],
        is_active=True,
        organization_id=sol.organization_id,
        solution_id=sol.id,
    )
    db.add(wf)
    await db.flush()
    return str(wid)


async def _fresh_org(db: AsyncSession) -> UUID:
    from src.models.orm.organizations import Organization

    org = Organization(
        id=uuid.uuid4(),
        name=f"RT Target {uuid.uuid4().hex[:8]}",
        domain=f"rt-{uuid.uuid4().hex[:8]}.test",
        created_by="roundtrip@test.local",
    )
    db.add(org)
    await db.flush()
    return org.id


# ---------------------------------------------------------------------------
# The field-by-field oracle (shared by shareable + full).
# ---------------------------------------------------------------------------


def _assert_entity_fields(
    model: type,
    before: dict,
    after: dict,
    policy: dict,
    *,
    installed_solution_id: UUID,
    in_bundle_ids: set[str],
) -> list[str]:
    """Assert every model field obeys *policy*, honoring FIELD_OVERRIDES.

    Returns the list of red strings (empty == clean). Reference fields get the
    exact in-bundle remap check via ``remap=``.
    """
    remap = remap_ref_for(installed_solution_id, in_bundle_ids)
    reds: list[str] = []
    for field in model.model_fields:
        override = FIELD_OVERRIDES.get((model.__name__, field))
        try:
            if override == "absent":
                # Scope-inherited field: never serialized into the bundle entry.
                assert before.get(field) in (None, [], {}, ""), (
                    f"{model.__name__}.{field} (override=absent) was present in the "
                    f"source bundle entry: {before.get(field)!r}"
                )
                assert after.get(field) in (None, [], {}, ""), (
                    f"{model.__name__}.{field} (override=absent) appeared in the "
                    f"installed bundle entry: {after.get(field)!r}"
                )
            elif override == "scrub":
                aval = after.get(field)
                assert aval in (None, [], {}, ""), (
                    f"{model.__name__}.{field} (override=scrub) leaked: {aval!r}"
                )
            elif override == "keep_env_ref":
                # Env-scoped grant: value preserved as-is (NOT solution-remapped).
                assert after.get(field) == before.get(field), (
                    f"{model.__name__}.{field} (override=keep_env_ref) changed "
                    f"{before.get(field)!r} -> {after.get(field)!r}"
                )
            else:
                assert_field_roundtrip(
                    model, field, before, after, policy, row=before, remap=remap
                )
        except AssertionError as e:
            cls = field_class_of(model, field, before)
            tag = f"override={override}" if override else cls.value
            reds.append(f"{model.__name__}.{field} ({tag}): {e}")
    return reds


async def _assert_env_stamped(db: AsyncSession, installed_wf_id: str, target_org: UUID) -> None:
    """The installed Workflow DB row must bind to the TARGET org (ENVIRONMENT:stamp).

    The bundle ENTRY never carries ``organization_id`` (scope is install-inherited —
    the ``"absent"`` override), so the stamp can only be verified on the persisted row.
    A vacuous ``entry.get("organization_id")`` check would always pass; this does not.
    """
    from sqlalchemy import select

    row = await db.scalar(select(Workflow).where(Workflow.id == UUID(str(installed_wf_id))))
    assert row is not None, f"installed workflow {installed_wf_id} not found in DB"
    assert row.organization_id == target_org, (
        f"env-stamp failed: installed workflow org {row.organization_id!r} != target {target_org!r}"
    )


# ---------------------------------------------------------------------------
# Workflow — proven end-to-end through BOTH shareable and full.
# Task 7 will factor the seed→export→install dance into a shared helper once the
# per-entity parametrization shape (which intermediate rows each entity needs) is
# settled across all 8 solution entities; today the two Workflow tests inline it.
# ---------------------------------------------------------------------------


async def test_solution_shareable_roundtrip_workflow(db_session: AsyncSession):
    """Every ManifestWorkflow field obeys SOLUTION_SHAREABLE across a real
    shareable export -> install -> read-back round trip, paired by_remap."""
    db = db_session
    # BEFORE: capture the source bundle's workflow entry through the real serializer.
    src_sol = await _make_solution(db)
    src_wf_id = await seed_solution_workflow(db, src_sol)
    before_rows = await solution_bundle_entries(db, src_sol, "workflows")
    assert len(before_rows) == 1, f"expected 1 source workflow, got {before_rows}"

    # EXPORT (shareable, no password) -> INSTALL into a fresh org.
    zip_bytes = await solution_export_zip(db, src_sol)
    target_org = await _fresh_org(db)
    installed = await solution_install_zip(db, zip_bytes, organization_id=target_org)

    # AFTER: read the INSTALLED workflow back through the SAME serializer.
    after_rows = await solution_bundle_entries(db, installed, "workflows")
    assert len(after_rows) == 1, f"expected 1 installed workflow, got {after_rows}"

    (b, a), = pair_rows(
        m.ManifestWorkflow,
        before_rows,
        after_rows,
        "by_remap",
        SOLUTION_SHAREABLE_POLICY,
        expected_id=expected_solution_id(installed.id),
    )
    # Env-stamp: assert on the installed DB ROW (the bundle entry never carries
    # organization_id — see the "absent" override; the real stamp lives on the row).
    await _assert_env_stamped(db, a["id"], target_org)

    in_bundle = {str(src_wf_id)}
    _assert_complete(m.ManifestWorkflow, b, a)
    reds = _assert_entity_fields(
        m.ManifestWorkflow, b, a, SOLUTION_SHAREABLE_POLICY,
        installed_solution_id=installed.id, in_bundle_ids=in_bundle,
    )
    assert not reds, "ManifestWorkflow SOLUTION_SHAREABLE round-trip drops:\n" + "\n".join(reds)


async def test_solution_full_roundtrip_workflow(db_session: AsyncSession):
    """ManifestWorkflow obeys SOLUTION_FULL (manifest env stamped, secret scrubbed
    from the manifest) across a full-backup export -> install round trip."""
    db = db_session
    src_sol = await _make_solution(db)
    src_wf_id = await seed_solution_workflow(db, src_sol)
    before_rows = await solution_bundle_entries(db, src_sol, "workflows")

    zip_bytes = await solution_export_zip(db, src_sol, password="pw", include_values=True)
    target_org = await _fresh_org(db)
    installed = await solution_install_zip(
        db, zip_bytes, organization_id=target_org, password="pw"
    )

    after_rows = await solution_bundle_entries(db, installed, "workflows")
    (b, a), = pair_rows(
        m.ManifestWorkflow, before_rows, after_rows, "by_remap",
        SOLUTION_FULL_POLICY, expected_id=expected_solution_id(installed.id),
    )
    # Env-stamp on the installed DB row (see shareable test for why the bundle entry can't be used).
    await _assert_env_stamped(db, a["id"], target_org)

    reds = _assert_entity_fields(
        m.ManifestWorkflow, b, a, SOLUTION_FULL_POLICY,
        installed_solution_id=installed.id, in_bundle_ids={str(src_wf_id)},
    )
    assert not reds, "ManifestWorkflow SOLUTION_FULL round-trip drops:\n" + "\n".join(reds)


# ---------------------------------------------------------------------------
# Agent — surfaces the EXTRA_FIELD_POLICY completeness layer (max_run_timeout is
# a captured Agent column ManifestAgent does not name) AND proves the deploy
# writer fix (deploy now stamps max_run_timeout so it round-trips).
# ---------------------------------------------------------------------------


async def seed_solution_agent(db: AsyncSession, sol: Solution) -> tuple[str, int]:
    """Seed a solution-managed Agent with every content field non-default,
    including the deploy-owned ``max_run_timeout`` (the headline drop)."""
    from src.models.enums import AgentAccessLevel
    from src.models.orm.agents import Agent

    aid = uuid.uuid4()
    run_timeout = 777
    agent = Agent(
        id=aid,
        name="RoundTrip Agent",
        description="seeded agent for solution round trip",
        system_prompt="You are a round-trip test agent.",
        channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
        knowledge_sources=["kb-alpha"],
        system_tools=["search_knowledge"],
        llm_model="claude-test",
        llm_max_tokens=2048,
        max_iterations=11,
        max_token_budget=22222,
        max_run_timeout=run_timeout,
        organization_id=sol.organization_id,
        solution_id=sol.id,
        created_by="roundtrip@test.local",
    )
    db.add(agent)
    await db.flush()
    return str(aid), run_timeout


async def test_solution_shareable_roundtrip_agent(db_session: AsyncSession):
    """ManifestAgent obeys SOLUTION_SHAREABLE; the completeness layer accounts
    for max_run_timeout (extra); and the deploy fix round-trips its value."""
    db = db_session
    src_sol = await _make_solution(db)
    src_agent_id, src_timeout = await seed_solution_agent(db, src_sol)
    before_rows = await solution_bundle_entries(db, src_sol, "agents")
    assert len(before_rows) == 1, f"expected 1 source agent, got {before_rows}"

    zip_bytes = await solution_export_zip(db, src_sol)
    target_org = await _fresh_org(db)
    installed = await solution_install_zip(db, zip_bytes, organization_id=target_org)

    after_rows = await solution_bundle_entries(db, installed, "agents")
    assert len(after_rows) == 1, f"expected 1 installed agent, got {after_rows}"

    (b, a), = pair_rows(
        m.ManifestAgent, before_rows, after_rows, "by_remap",
        SOLUTION_SHAREABLE_POLICY, expected_id=expected_solution_id(installed.id),
    )

    # Completeness: max_run_timeout is an EXTRA (not a ManifestAgent field).
    # Without the EXTRA_FIELD_POLICY entry this is an UNACCOUNTED-key failure.
    _assert_complete(m.ManifestAgent, b, a)

    # The deploy writer fix: max_run_timeout must survive the round trip (before
    # the fix, deploy never stamped it → re-capture saw the column default None).
    assert b["max_run_timeout"] == src_timeout
    assert a["max_run_timeout"] == src_timeout, (
        f"max_run_timeout dropped on solution deploy: {a.get('max_run_timeout')!r} "
        f"!= {src_timeout!r} — the deploy._upsert_agents stamp is missing"
    )

    reds = _assert_entity_fields(
        m.ManifestAgent, b, a, SOLUTION_SHAREABLE_POLICY,
        installed_solution_id=installed.id, in_bundle_ids={str(src_agent_id)},
    )
    assert not reds, "ManifestAgent SOLUTION_SHAREABLE round-trip drops:\n" + "\n".join(reds)


# ---------------------------------------------------------------------------
# Table — solution path (minimal closure: a solution-owned table, no rows).
# ---------------------------------------------------------------------------


async def seed_solution_table(db: AsyncSession, sol: Solution) -> str:
    from src.models.orm.tables import Table

    tid = uuid.uuid4()
    table = Table(
        id=tid,
        name=f"rt_table_{uuid.uuid4().hex[:6]}",
        description="seeded table for solution round trip",
        organization_id=sol.organization_id,
        solution_id=sol.id,
        schema={"columns": [{"name": "title", "type": "string"}]},
        access={"policies": [
            {"name": "admin_bypass", "actions": ["read", "create", "update", "delete"], "when": None},
        ]},
    )
    db.add(table)
    await db.flush()
    return str(tid)


async def test_solution_shareable_roundtrip_table(db_session: AsyncSession):
    """ManifestTable obeys SOLUTION_SHAREABLE across a real export -> install."""
    db = db_session
    src_sol = await _make_solution(db)
    src_table_id = await seed_solution_table(db, src_sol)
    before_rows = await solution_bundle_entries(db, src_sol, "tables")
    assert len(before_rows) == 1, f"expected 1 source table, got {before_rows}"

    zip_bytes = await solution_export_zip(db, src_sol)
    target_org = await _fresh_org(db)
    installed = await solution_install_zip(db, zip_bytes, organization_id=target_org)

    after_rows = await solution_bundle_entries(db, installed, "tables")
    assert len(after_rows) == 1, f"expected 1 installed table, got {after_rows}"

    (b, a), = pair_rows(
        m.ManifestTable, before_rows, after_rows, "by_remap",
        SOLUTION_SHAREABLE_POLICY, expected_id=expected_solution_id(installed.id),
    )
    _assert_complete(m.ManifestTable, b, a)
    reds = _assert_entity_fields(
        m.ManifestTable, b, a, SOLUTION_SHAREABLE_POLICY,
        installed_solution_id=installed.id, in_bundle_ids={str(src_table_id)},
    )
    assert not reds, "ManifestTable SOLUTION_SHAREABLE round-trip drops:\n" + "\n".join(reds)


# ---------------------------------------------------------------------------
# Config schema (SolutionConfigSchema) — solution-only entity, minimal closure.
# ---------------------------------------------------------------------------


async def seed_solution_config_schema(db: AsyncSession, sol: Solution) -> str:
    from src.models.enums import ConfigType
    from src.models.orm.solution_config_schema import SolutionConfigSchema

    cid = uuid.uuid4()
    db.add(SolutionConfigSchema(
        id=cid, solution_id=sol.id, key="RT_SETTING",
        type=ConfigType.STRING.value, required=True, position=2,
        description="seeded config schema for round trip", default="dflt",
    ))
    await db.flush()
    return str(cid)


async def test_solution_shareable_roundtrip_config_schema(db_session: AsyncSession):
    """ManifestSolutionConfigSchema obeys SOLUTION_SHAREABLE across export->install."""
    db = db_session
    src_sol = await _make_solution(db)
    src_id = await seed_solution_config_schema(db, src_sol)
    before_rows = await solution_bundle_entries(db, src_sol, "config_schemas")
    assert len(before_rows) == 1, f"expected 1 source config schema, got {before_rows}"

    zip_bytes = await solution_export_zip(db, src_sol)
    target_org = await _fresh_org(db)
    installed = await solution_install_zip(db, zip_bytes, organization_id=target_org)

    after_rows = await solution_bundle_entries(db, installed, "config_schemas")
    assert len(after_rows) == 1, f"expected 1 installed config schema, got {after_rows}"

    (b, a), = pair_rows(
        m.ManifestSolutionConfigSchema, before_rows, after_rows, "by_remap",
        SOLUTION_SHAREABLE_POLICY, expected_id=expected_solution_id(installed.id),
    )
    _assert_complete(m.ManifestSolutionConfigSchema, b, a)
    reds = _assert_entity_fields(
        m.ManifestSolutionConfigSchema, b, a, SOLUTION_SHAREABLE_POLICY,
        installed_solution_id=installed.id, in_bundle_ids={str(src_id)},
    )
    assert not reds, (
        "ManifestSolutionConfigSchema SOLUTION_SHAREABLE round-trip drops:\n" + "\n".join(reds)
    )


# ---------------------------------------------------------------------------
# Claim — solution path (closure: a solution-owned table the claim queries).
# ---------------------------------------------------------------------------


async def seed_solution_claim(db: AsyncSession, sol: Solution) -> str:
    from src.models.orm.custom_claims import CustomClaim

    # The claim's query references a table by NAME (org-scoped, resolved at
    # query time — deploy does not validate the table exists), so a standalone
    # claim is a valid minimal closure.
    cid = uuid.uuid4()
    db.add(CustomClaim(
        id=cid,
        name=f"rt_claim_{uuid.uuid4().hex[:6]}",
        description="seeded claim for solution round trip",
        organization_id=sol.organization_id,
        solution_id=sol.id,
        type="list",
        query={"table": "rt_source", "where": None, "select": "value"},
    ))
    await db.flush()
    return str(cid)


async def test_solution_shareable_roundtrip_claim(db_session: AsyncSession):
    """ManifestCustomClaim obeys SOLUTION_SHAREABLE across export->install."""
    db = db_session
    src_sol = await _make_solution(db)
    src_id = await seed_solution_claim(db, src_sol)
    before_rows = await solution_bundle_entries(db, src_sol, "claims")
    assert len(before_rows) == 1, f"expected 1 source claim, got {before_rows}"

    zip_bytes = await solution_export_zip(db, src_sol)
    target_org = await _fresh_org(db)
    installed = await solution_install_zip(db, zip_bytes, organization_id=target_org)

    after_rows = await solution_bundle_entries(db, installed, "claims")
    assert len(after_rows) == 1, f"expected 1 installed claim, got {after_rows}"

    (b, a), = pair_rows(
        m.ManifestCustomClaim, before_rows, after_rows, "by_remap",
        SOLUTION_SHAREABLE_POLICY, expected_id=expected_solution_id(installed.id),
    )
    _assert_complete(m.ManifestCustomClaim, b, a)
    reds = _assert_entity_fields(
        m.ManifestCustomClaim, b, a, SOLUTION_SHAREABLE_POLICY,
        installed_solution_id=installed.id, in_bundle_ids={str(src_id)},
    )
    assert not reds, (
        "ManifestCustomClaim SOLUTION_SHAREABLE round-trip drops:\n" + "\n".join(reds)
    )


# ---------------------------------------------------------------------------
# Envelope check 1: table_data — full carries rows, shareable carries none.
# ---------------------------------------------------------------------------


async def _seed_solution_table_with_rows(db: AsyncSession, sol: Solution, *, n: int) -> str:
    from src.models.orm.tables import Document, Table

    tid = uuid.uuid4()
    table = Table(
        id=tid,
        name=f"rt_table_{uuid.uuid4().hex[:6]}",
        organization_id=sol.organization_id,
        solution_id=sol.id,
        schema={"columns": [{"name": "title", "type": "string"}]},
        access={"policies": []},
    )
    db.add(table)
    for i in range(n):
        db.add(Document(table_id=tid, id=f"row-{i}", data={"title": f"value {i}"}))
    await db.flush()
    return table.name


async def test_table_data_envelope_full_carries_rows_shareable_does_not(db_session: AsyncSession):
    """Full export (include_data) carries table rows; shareable carries none."""
    db = db_session
    sol = await _make_solution(db)
    tname = await _seed_solution_table_with_rows(db, sol, n=3)

    from src.services.solutions.capture import SolutionCaptureService

    full_bundle = await SolutionCaptureService(db).bundle_for(sol, include_data=True)
    assert tname in full_bundle.table_data, (
        f"full bundle did not carry rows for {tname}: {full_bundle.table_data.keys()}"
    )
    assert len(full_bundle.table_data[tname]) == 3

    shareable_bundle = await SolutionCaptureService(db).bundle_for(sol)
    assert shareable_bundle.table_data == {}, (
        f"shareable bundle leaked table rows: {shareable_bundle.table_data!r}"
    )


# ---------------------------------------------------------------------------
# Envelope check 2: secret envelope — full encrypt->decrypt survives; the
# secret is ABSENT from the shareable manifest plaintext (leak scan).
# ---------------------------------------------------------------------------


async def test_secret_envelope_survives_full_and_absent_from_shareable(db_session: AsyncSession):
    """A SECRET-class config value survives full encrypt->decrypt and never
    appears in the shareable plaintext zip."""
    import zipfile
    from io import BytesIO

    from src.services.solutions.secrets_blob import (
        SolutionContent,
        decode_secrets_blob,
        encode_secrets_blob,
    )

    # Round-trip the secret value through the real Fernet envelope.
    content = SolutionContent(config_values={"RTM_API_KEY": SECRET_SENTINEL})
    blob = encode_secrets_blob(content, password="pw")
    decoded = decode_secrets_blob(blob, password="pw")
    assert decoded.config_values["RTM_API_KEY"] == SECRET_SENTINEL

    # The encrypted blob string itself must NOT contain the plaintext sentinel.
    assert_no_secret_leak(blob, [SECRET_SENTINEL])

    # And a shareable export of a solution carrying that config value must NOT
    # leak the plaintext anywhere in the zip (no secrets.enc in shareable mode).
    db = db_session
    sol = await _make_solution(db)
    from src.models.enums import ConfigType
    from src.models.orm.config import Config
    from src.models.orm.solution_config_schema import SolutionConfigSchema

    db.add(SolutionConfigSchema(
        id=uuid.uuid4(), solution_id=sol.id, key="RTM_API_KEY",
        type=ConfigType.SECRET.value, required=False, position=0,
    ))
    from src.core.security import encrypt_secret

    db.add(Config(
        id=uuid.uuid4(), key="RTM_API_KEY",
        value={"value": encrypt_secret(SECRET_SENTINEL)},
        config_type=ConfigType.SECRET, organization_id=sol.organization_id,
        updated_by="test",
    ))
    await db.flush()

    shareable_zip = await solution_export_zip(db, sol)  # no password -> no blob
    parts: list[str] = []
    with zipfile.ZipFile(BytesIO(shareable_zip)) as zf:
        names = zf.namelist()
        assert ".bifrost/secrets.enc" not in names, "shareable zip leaked secrets.enc"
        for name in names:
            try:
                parts.append(zf.read(name).decode("utf-8", errors="ignore"))
            except Exception:  # binary member — irrelevant to a text-secret scan
                continue
    assert_no_secret_leak("\n".join(parts), [SECRET_SENTINEL])


# ---------------------------------------------------------------------------
# Envelope check 3: connection declaration — scrubbed skeleton, no secrets.
# ---------------------------------------------------------------------------


async def test_connection_declaration_scrubs_secrets(db_session: AsyncSession):
    """A declared integration exports a skeleton via build_integration_template
    with NO client_id/secret/token/org — schema shape present, secrets absent."""
    db = db_session
    from src.models.orm.integrations import Integration, IntegrationConfigSchema
    from src.models.orm.oauth import OAuthProvider
    from src.services.solutions.integration_template import build_integration_template

    integ = Integration(
        id=uuid.uuid4(),
        name="rt-integration",
    )
    db.add(integ)
    db.add(OAuthProvider(
        id=uuid.uuid4(),
        provider_name="rtprov",
        display_name="RT Provider",
        client_id="CLIENT_ID_SECRET_DO_NOT_LEAK",
        encrypted_client_secret=b"CLIENT_SECRET_DO_NOT_LEAK",
        integration_id=integ.id,
    ))
    db.add(IntegrationConfigSchema(
        id=uuid.uuid4(), integration_id=integ.id, key="base_url",
        type="string", required=True, position=0, description="Base URL",
    ))
    await db.flush()
    await db.refresh(integ)

    template = build_integration_template(integ)

    # Schema shape present.
    assert template["name"] == "rt-integration"
    keys = [c["key"] for c in template["config_schema"]]
    assert "base_url" in keys, f"config schema shape missing: {template['config_schema']}"
    assert template["oauth"] is not None
    assert template["oauth"]["provider_name"] == "rtprov"

    # Secrets ABSENT: no client_id/secret/token/org anywhere in the skeleton.
    flat = repr(template)
    assert "CLIENT_ID_SECRET_DO_NOT_LEAK" not in flat, "client_id leaked into template"
    assert "CLIENT_SECRET_DO_NOT_LEAK" not in flat, "client_secret leaked into template"
    assert "client_id" not in template["oauth"], "client_id key present in oauth skeleton"
    assert "encrypted_client_secret" not in template["oauth"], "client_secret key present"
    assert "organization_id" not in flat, "organization_id leaked into template"
