# Manifest Field-Class Contract + Generated Round-Trip Test Harness — Design Spec

> **Status: DRAFT for review (2026-06-19).** This is the design Jack and Claude settled
> interactively. The per-entity classification table (§5) is the part most likely to need
> correction — **review every row**, especially the ⚠️-flagged ones in §6. Once approved, an
> implementation plan is written from this spec.

## 0. Why this exists

Bifrost has **multiple, divergent code paths** that turn manifest entity declarations into DB rows
(and back) for the same entities: `_repo` git-sync (`generate_manifest` ↔ `ManifestResolver`),
Solution export/import (`_collect_*` + deploy `_upsert_*`), and Solution full-backup/restore
(Fernet-encrypted). Phase 1 (the prior plan, now complete) fixed three reproduced field-drop bugs
one at a time. The **convergence** refactor (Phases 2–4) wants to put these paths on one shared
contract — but we have no safety net proving that a refactor preserves every field on every path.
A field silently dropped by the converged writer would only be caught if someone happened to have
written a checklist line for it (that is how Bug C — `tool_description` — was found, by luck).

This spec defines:
1. **A field-class contract** — machine-readable metadata on every manifest field declaring how it
   behaves on each round-trip path. This is *axis A* of the convergence design (spec
   `2026-06-18-...-contract-unification.md` §16–17) — building it here produces the contract the
   refactor is meant to be built on, not throwaway test scaffolding.
2. **A generated round-trip test harness** that drives each REAL path and asserts each field obeys
   its class's per-path policy. Built FIRST, against current code, so it is the regression oracle
   the refactor must keep green.

**The harness is the thing that makes us comfortable with a big refactor.** Build it before
touching the writers.

## 1. Terminology (settled)

This is **field-level metadata / annotations** — the Python analog of a **C# attribute**
(`[JsonProperty]`, `[Key]`) read via reflection. It is **NOT a decorator** (decorators wrap a whole
class/function, not a field). Carried in the Pydantic `Field()` descriptor via `json_schema_extra`,
introspected through `Model.model_fields[name]`. We deliberately chose `Field(**classify(...))` over
`typing.Annotated[T, ...]` to keep all field metadata (default, description, class) in one call site,
matching how these models already declare everything in `Field()`.

## 2. The mechanism (settled)

New module `api/bifrost/field_classes.py`:

```python
from enum import Enum
from typing import Callable, Any

class FieldClass(str, Enum):
    IDENTITY    = "identity"     # the row's own PK/UUID. REGENERATED (remapped) on a portable path —
                                 # does NOT round-trip as-is; never the cross-env match key.
    CONTENT     = "content"      # portable payload — MUST round-trip on every path.
    ENVIRONMENT = "environment"  # org/roles/access/state tied to THIS install — scrubbed on portable
                                 # export, kept on _repo sync and full backup.
    SECRET      = "secret"       # credential/sensitive — scrubbed on _repo (goes to git!) AND portable
                                 # share; kept (Fernet-encrypted) only in full backup.
    REFERENCE   = "reference"    # FK to another entity by id/name — round-trips, remapped per install.

def classify(
    field_class: FieldClass,
    *,
    match_key: bool = False,           # this field (alone or with other match_key fields) is the
                                       # NATURAL key used to re-pair "the same logical entity" across
                                       # a portable round trip, where IDENTITY was regenerated.
    when: Callable[[Any], FieldClass] | None = None,  # conditional class (e.g. value is SECRET only
                                       # when config_type=='secret', else CONTENT). Receives the row.
    keep_on_portable: bool = False,    # ENVIRONMENT field that is nonetheless KEPT on portable export
                                       # because it is the portable shadow of a scrubbed field
                                       # (role_names carries roles across the scrub).
) -> dict:
    extra = {"bifrost_field_class": field_class.value}
    if match_key: extra["bifrost_match_key"] = True
    if keep_on_portable: extra["bifrost_keep_on_portable"] = True
    if when is not None: extra["bifrost_class_predicate"] = when   # stored; harness calls it per row
    return {"json_schema_extra": extra}
```

Usage:
```python
class ManifestWorkflow(BaseModel):
    id:            str = Field(description="Agent UUID", **classify(FieldClass.IDENTITY))
    function_name: str = Field(..., **classify(FieldClass.CONTENT, match_key=True))
    organization_id: str | None = Field(default=None, **classify(FieldClass.ENVIRONMENT))
    role_names:    list[str] | None = Field(default=None,
                       **classify(FieldClass.ENVIRONMENT, keep_on_portable=True))
```

### 2.1 The tripwire (what keeps the contract honest)
A unit test introspects every `Manifest*` model and asserts **every field carries
`bifrost_field_class`**. An untagged field is a HARD failure. This is the mechanism that prevents
silent drift when Phase 3 adds fields — the same idea as the existing `test_dto_flags.py` /
`test_contract_version.py` tripwires.

## 3. Match keys answer "how do we re-pair entities when IDs are regenerated?" (settled)

**Solutions deliberately do not carry stable IDs across environments.** On install, an entity gets a
new `solution_entity_id(install_id, manifest_id)` (see Phase-1 Task 10's remap). So the raw `id`
cannot pair "the workflow I exported" with "the workflow I imported." The **match key** — one or more
fields flagged `match_key=True` — is the natural key that does. Per-entity match keys are drawn from
how deploy/the engine already match (code evidence in §5). A field can be BOTH `content` and a match
key (a table `name` is user-visible content AND its reattach key) — `match_key` is an orthogonal flag,
not a sixth mutually-exclusive class.

**This is the substrate the held Task 11 decision plugs into:** "what is a solution entity's stable
identity across reinstall" is the match-key question; `(origin_solution_slug, name)` is a composite
match key with a scope qualifier.

## 4. The contract (3 paths × 5 classes) — pinned by the harness, matching EXISTING code

| Path | identity | content | environment | secret | reference |
|---|---|---|---|---|---|
| **`_repo` git-sync** (same-env, **writes to a git repo**) | keep | keep | keep | **scrub** | keep |
| **Solution export `--mode shareable`** (portable) | regen (match by key) | keep | **scrub** (except `keep_on_portable`) | **scrub** | keep (remap) |
| **Solution export `--mode full` / backup** (Fernet, password) | regen (match by key) | keep | keep | **keep (encrypted)** | keep (remap) |

Code evidence this is the EXISTING (implicit) contract, not a new invention:
- `_repo` scrubs secret: `manifest_generator.py:279` (`value=None if config_type==SECRET`),
  `:390-391` (`encrypted_client_secret intentionally omitted — secrets are gitignored`).
- `_repo` keeps environment: it emits `organization_id`, `roles`, `access_level` (same-env sync).
- Portable scrubs environment: the inline-content serialization "intentionally excludes
  organization_id, roles, access_level" (CLAUDE.md portability design); `role_names` is the kept shadow.
- Full backup keeps secret encrypted: `secrets_blob.py` Fernet envelope; `capture.py:345 include_data`.

**`secret` on `_repo` was the one I (Claude) got wrong first** — git-sync must NOT write decrypted
secrets to git. The code already scrubs them; the harness pins that so convergence cannot regress it
into a leak.

## 5. PROPOSED per-entity field-class table (REVIEW EVERY ROW)

> Generated by classifier heuristic + code evidence. `MK` = match_key. `env+keep` =
> environment, keep_on_portable. `secret?` = conditional (see §6). Entities are nested where a field
> holds a list of other Manifest* models — the harness recurses.

**ManifestOrganization:** id=identity · name=content **MK** · is_active=content ⚠️
**ManifestRole:** id=identity · name=content **MK**
**ManifestWorkflow:** id=identity · name=content · path=content(deprecated→absent) · function_name=content **MK** · type=content · organization_id=environment · roles=environment · role_names=env+keep · access_level=environment · endpoint_enabled=content ⚠️ · timeout_seconds=content · public_endpoint=content · description=content · tool_description=content · category=content · tags=content
**ManifestForm:** id=identity · name=content **MK** · path=content(deprecated) · organization_id=environment · roles=environment · role_names=env+keep · access_level=environment · description=content · workflow_id=reference · launch_workflow_id=reference · default_launch_params=content · allowed_query_params=content · form_schema=content
**ManifestAgent:** id=identity · name=content **MK** · path=content(deprecated) · organization_id=environment · roles=environment · role_names=env+keep · access_level=environment · description=content · system_prompt=content · channels=content · tool_ids=reference · delegated_agent_ids=reference · knowledge_sources=content · system_tools=content · mcp_connection_ids=reference · llm_model=content · llm_max_tokens=content · max_iterations=content · max_token_budget=content
**ManifestApp:** id=identity · path=content · slug=content **MK** · name=content · description=content · dependencies=content · organization_id=environment · roles=environment · role_names=env+keep · access_level=environment · app_model=content · logo=content
**ManifestIntegrationConfigSchema:** key=content **MK** · type=content · required=content · description=content · options=content · position=content
**ManifestOAuthProvider:** provider_name=content **MK** · display_name=content · oauth_flow_type=content · client_id=reference ⚠️ · authorization_url=content · token_url=content · token_url_defaults=content · scopes=content · redirect_uri=content
**ManifestIntegrationMapping:** organization_id=environment · entity_id=reference · entity_name=content · oauth_token_id=secret ⚠️
**ManifestIntegration:** id=identity · name=content **MK** · entity_id=reference · entity_id_name=content · default_entity_id=reference · list_entities_data_provider_id=reference · config_schema=nested · oauth_provider=nested · mappings=nested
**ManifestConfig:** id=identity · integration_id=reference · key=content **MK** · config_type=content · description=content · organization_id=environment · value=**secret?** (when config_type==secret, else content) ⚠️
**ManifestSolutionConfigSchema:** id=identity · key=content **MK** · type=content · required=content · description=content · default=content ⚠️ · position=content
**ManifestCustomClaim:** id=identity · name=content **MK** · description=content · organization_id=environment · type=content · query=content
**ManifestPolicy:** name=content **MK** · description=content · actions=content · when=content
**ManifestTable:** id=identity · name=content **MK** · description=content · organization_id=environment · table_schema=content · policies=nested
**ManifestEventSubscription:** id=identity · target_type=content · workflow_id=reference · agent_id=reference · event_type=content · filter_expression=content · input_mapping=content · is_active=content ⚠️
**ManifestEventSource:** id=identity · name=content **MK** · source_type=content · organization_id=environment · is_active=content ⚠️ · cron_expression=content · timezone=content · schedule_enabled=content ⚠️ · overlap_policy=content · adapter_name=content · webhook_integration_id=reference · webhook_config=content ⚠️ · rate_limit_*=content · subscriptions=nested
**ManifestMCPConnectionTool:** tool_name=content **MK** · tool_schema=content · enabled=content · disabled_reason=content
**ManifestMCPConnection:** organization_id=environment · client_id=reference ⚠️ · server_url_override=content · available_in_chat=content · available_to_autonomous=content · service_oauth_token_id=secret ⚠️ · tools=nested
**ManifestMCPServer:** id=identity · name=content **MK** · server_url=content · oauth_provider_id=reference · redirect_url=content · discovery_metadata=content ⚠️ · organization_id=environment · is_active=content ⚠️ · connections=nested

## 6. ⚠️ Rows needing an explicit human decision (the targeted-review list)

1. **State flags: `is_active`, `enabled`, `endpoint_enabled`, `schedule_enabled`, `public_endpoint`.**
   Called `content`. Real question: is "is this active/enabled" part of the **portable definition**
   (travels) or **per-install state** (`environment`)? E.g. should a shareable bundle dictate that
   an event source is *active* in the target, or is active/paused a local decision? Default proposal:
   keep as `content` (the author's intent travels; the installer can toggle after). Confirm.
2. **`ManifestConfig.value` conditional secret** — `secret` when `config_type=='secret'`, else
   `content`. Needs the `when=` predicate. Confirm the predicate and that no other field is
   conditionally secret.
3. **`oauth_token_id` / `service_oauth_token_id`** — token *references* (the token lives in the DB,
   not the manifest). Called `secret` (so scrubbed on `_repo` + portable). But they are ids, not
   credentials. Question: is the reference itself sensitive enough to scrub, or is it `reference`
   (round-trips, remapped)? Code emits the id on `_repo` (`manifest_generator.py:261`) — which
   argues `reference`, NOT `secret`. **Likely needs flipping to `reference`.** Decide.
4. **`client_id`** (OAuth/MCP) — half a credential, but code emits it to `_repo`
   (`manifest_generator.py:247,395`). Called `reference`. Confirm it is not `secret`.
5. **`ManifestWorkflow.function_name` vs `name` as match key** — proposed `function_name` (engine
   resolves by it; `SolutionWorkflowNameMismatch` at `deploy.py:156`). Is `name` a fallback/composite
   match key? Decide single vs composite.
6. **Blob fields: `webhook_config`, `discovery_metadata`, `table_schema`, `query`, `app_model`,
   `tool_schema`, `default_launch_params`, `form_schema`.** Called `content`. Confirm none embed a
   secret (a secret inside a JSON blob would slip through scrubbing). If any can, it needs a custom rule.
7. **`ManifestSolutionConfigSchema.default`** — a schema default value; called `content`. If a schema
   can default a *secret* config, this is a leak vector. Confirm.
8. **Deprecated `path`** (Workflow/Form/Agent) — content today is inline; generator no longer emits
   `path`. Proposal: tag `content` but the harness asserts it is ABSENT both directions (the tripwire
   tolerates it; the round-trip expects null↔null). Confirm we don't want a dedicated `deprecated` tag.

## 7. The harness (shape — detailed in the implementation plan)

- **`api/bifrost/field_classes.py`** — the enum + `classify()` + introspection helpers
  (`field_class_of(model, field, row)`, `match_keys(model)`).
- **`RoundTripPath`** objects — one per path (`REPO_SYNC`, `SOLUTION_SHAREABLE`, `SOLUTION_FULL`),
  each declaring its per-class policy (the §4 table) and exposing `run(entity_rows) -> entity_rows`
  that drives the REAL code (`generate_manifest`/`ManifestResolver`; the `_collect_*`+deploy;
  the Fernet capture/restore). No reimplementation of the paths.
- **Generators** — per entity: `all_fields_populated()`, `each_field_isolated()` (one fixture per
  field set, rest at defaults), `known_tricky()` (agent-delegation order-independence; nested event
  subscriptions; cascade override; conditional-secret config both ways). Bounded & deterministic;
  **no Hypothesis**. A generator that doesn't populate a newly-added field is caught by a
  generator-completeness tripwire (introspect model_fields vs what the generator sets).
- **The assertion** (`assert_roundtrip(model, before, after, path)`): pair before/after rows by
  `match_keys` (on portable paths where identity is regenerated; by id on same-env); for each field,
  read its class (calling `when=` predicate against the row); if `path.preserves(class, field)`
  assert equal, else assert scrubbed (None/[]/{} or, for secret-in-full, assert decryptable-equal).
- **Existing known drops become RED**: `auto_fill` (form field dropped by shared FormIndexer),
  agent-delegation order, `max_run_timeout`/`event_type`/`display_name` parity — the harness, built
  against current code, fails on these immediately. Fixing them to green is part of Phase 1.5, and
  turns the prior plan's prose §Handoff checklist into executable tests.

## 8. Sequencing (settled: harness FIRST)

**Phase 1.5 (this spec → its plan), BEFORE convergence:**
1. `field_classes.py` + `classify()` + the tagging tripwire.
2. Tag all 20 entities per the approved §5 table.
3. Build the three `RoundTripPath` drivers over the REAL paths.
4. Build the generators + the round-trip assertion + the generator-completeness tripwire.
5. Run; fix the existing drops the harness surfaces to green (the §7 known-drops list).
**Then convergence (Phases 2–4):** refactor the writers onto the shared contract, keeping the
harness green at every step. The field-class metadata from step 2 IS axis-A of the converged
contract; the `RoundTripPath` policies ARE the per-path ReconciliationPolicy instances (spec
`2026-06-18` §16–17).

## 9. Open questions for review
- §6 rows 1–8 (the targeted-review list) — each needs a yes/confirm or a correction.
- Is `nested` a sixth FieldClass, or just "this field holds child Manifest models, recurse" (no class
  of its own — the CHILDREN carry classes)? **Proposal: not a class** — nested containers aren't
  scrubbed/kept as a unit; the harness recurses into them and applies child field classes. The
  container field itself is implicitly "structural." Confirm — this affects whether nested fields
  need a `classify()` tag (proposal: they get `classify(FieldClass.CONTENT)` as a container but the
  real assertion is on the recursed children).
- Backup/restore round-trip: confirm the harness should exercise the full Fernet encrypt→decrypt
  cycle (not just assert the field is present pre-encrypt), to catch an encryption-layer drop.
