# Manifest Field-Class Contract + Generated Round-Trip Test Harness — Design Spec (v2)

> **Status: DRAFT v2 (2026-06-19), Codex-reviewed.** v1 was reviewed by Codex (independent model);
> it confirmed the core design is sound but found 4 code-verified corrections (path-dependent match
> keys; full-backup does not keep environment; two missed bundle envelopes) + 3 false-confidence
> risks. All are folded into this v2. The per-entity classification table (§6) and the path policies
> (§4) are still the parts most worth scrutinising before the plan is executed.

## 0. Why this exists

Bifrost has **multiple, divergent code paths** that turn manifest entity declarations into DB rows
(and back) for the same entities. Phase 1 (prior, complete) fixed three reproduced field-drop bugs
one at a time. The **convergence** refactor (Phases 2–4) wants to put these paths on one shared
writer — but we have no safety net proving a refactor preserves every field on every path. A field
silently dropped by the converged writer would only be caught by luck (that is how Bug C —
`tool_description` — was found).

This spec defines (1) **field-class metadata** on every manifest field declaring how it behaves per
path, and (2) a **generated round-trip test harness** that drives each REAL path and asserts each
field obeys its class's per-path policy. **Built FIRST, against current code, as the regression
oracle the refactor must keep green.** The metadata is also *axis A* of the convergence design
(`2026-06-18-...-contract-unification.md` §16–17) — building it here produces the contract the
refactor is meant to be built on.

## 1. Terminology (settled)

**Field-level metadata / annotations** — the Python analog of a **C# attribute** (`[JsonProperty]`,
`[Key]`), read via reflection. **NOT a decorator** (decorators wrap a class/function, not a field).
Carried in the Pydantic `Field()` descriptor via `json_schema_extra`, introspected through
`Model.model_fields[name]`. Chosen over `typing.Annotated[T, ...]` to keep all field metadata in one
call site.

### 1.1 ELI5 framing (the mental model — keep this in the plan's preamble)
A solution/export is a **shipping box with several labeled envelopes**: a **manifest** envelope
(entity *blueprints* — every field we sticker), a **code** envelope (`.py`/app source), a
**table_data** envelope (table rows, full-backup only), and a **secrets** envelope (Fernet-encrypted,
full-backup only). **Field-class stickers label fields INSIDE the manifest envelope only.** The other
envelopes are separate round-trip checks (§5). On each kind of trip the robot checks every stickered
field obeys its color's rule. Five colors (§2); but think of them in two groups for intuition —
**"always travels"** (content, references) vs **"sensitive/scoped, handled specially"** (environment,
secret, identity) — with the important caveat (§4) that the three paths each treat the second group
differently, which is exactly why we can't collapse to two buckets.

## 2. The mechanism (settled)

New module `api/bifrost/field_classes.py`:

```python
from enum import Enum
from typing import Callable, Any

class FieldClass(str, Enum):
    IDENTITY    = "identity"     # the row's own PK/UUID. On _repo it round-trips (same-env keeps id);
                                 # on Solution install it is REGENERATED — so it is an ASSERTION/MATCH
                                 # behavior, not a scrub bucket: the harness never asserts identity
                                 # equality on a path that regenerates ids, and uses the match key
                                 # (§3) to pair rows instead.
    CONTENT     = "content"      # portable payload (blueprint) — MUST round-trip on every path.
    ENVIRONMENT = "environment"  # org / roles / access / local binding tied to an install. Kept by
                                 # _repo (same env); STAMPED from the target on Solution install
                                 # (export carries no scope; deploy sets organization_id from the
                                 # install — deploy.py:797). i.e. NOT preserved on ANY Solution path,
                                 # shareable OR full.
    SECRET      = "secret"       # credential/sensitive. Scrubbed on _repo (it writes to git!) AND on
                                 # shareable export. Kept ONLY in the full-backup SECRETS envelope
                                 # (Fernet-encrypted, password-gated) — never in the manifest envelope.
    REFERENCE   = "reference"    # FK to another entity by id/name — round-trips, REMAPPED per install
                                 # on Solution deploy (deploy.py:495/557).

def classify(field_class, *, match_key=False, when=None, keep_on_portable=False) -> dict:
    # match_key: this field is (part of) the natural key used to re-pair entities on a path that
    #            regenerates ids (Solution install). One-or-more fields. See §3.
    # when:      conditional class — receives the row, returns a FieldClass (e.g. Config.value is
    #            SECRET when config_type=='secret', else CONTENT).
    # keep_on_portable: an ENVIRONMENT field nonetheless kept on shareable export because it is the
    #            portable shadow of a scrubbed field (role_names carries roles across the scrub).
    extra = {"bifrost_field_class": field_class.value}
    if match_key:        extra["bifrost_match_key"] = True
    if keep_on_portable: extra["bifrost_keep_on_portable"] = True
    if when is not None: extra["bifrost_class_predicate"] = when
    return {"json_schema_extra": extra}
```

### 2.1 The tripwire (keeps the contract honest)
A unit test introspects every `Manifest*` model and asserts **every field carries
`bifrost_field_class`**. An untagged field is a HARD failure — prevents silent drift when Phase 3
adds fields. (Same idea as `test_dto_flags.py` / `test_contract_version.py`.)

## 3. Match keys — PATH-DEPENDENT (Codex correction #2, the big one)

The harness must pair "the entity I exported" with "the entity I imported." **How it pairs depends
on the path, because the two families of path identify entities differently:**

- **`_repo` git-sync pairs by `id`.** It is same-env; `_diff_and_collect` explicitly "Index[es] by
  entity .id" (`manifest_import.py:94`) and forms/agents/events/MCP-servers upsert **by `id` only**
  (`:2531/2579/2235/2387` — MCP server: *"Always upserts on the UUID — never the natural key"*).
  Natural keys exist in `_repo` ONLY as a *fallback* for cross-env ID realignment in the prefetch
  cache (`:1019/1059` "Try by name (cross-env ID sync)") — they are NOT the primary matcher.
- **Solution install pairs by NATURAL KEY**, because install regenerates ids
  (`solution_entity_id(install_id, manifest_id)`; Phase-1 Task 10's remap). The natural keys (from
  the prefetch cache `_diff_and_collect` builds at `:466-540`, which IS the cross-env matcher there):

| Entity | Natural key (Solution-install matcher) | Code |
|---|---|---|
| Organization | `name` | :467 |
| Role | `name` | :475 |
| **Workflow** | **`(path, function_name)`** (the lone composite; `name` is a renameable display field, fallback only) | :482 |
| Integration | `name` | :493 |
| IntegrationConfigSchema | `(integration_id, key)` (within parent integration) | :501 |
| App | `slug` | :514 |
| Table | `(name, organization_id)` | :520 |
| Config | `(key, integration_id, organization_id)` | :530 |
| CustomClaim | `(name, organization_id)` | :538 |
| MCPConnectionTool | `(connection_id, tool_name)` (within parent connection) | :2509 |
| Form / Agent / EventSource / MCPServer | **id only — NO natural key matcher exists** (Codex #2) | :2531/2579/2235/2387 |

**Implication of the last row:** Forms, agents, event sources, and MCP servers have NO name-based
match in the code. On a Solution round trip where their id is regenerated, the harness must pair them
by the **manifest position / deterministic remap** (`solution_entity_id(install, manifest_id)` is a
pure function — the harness can compute the expected post-install id), NOT by name. So `match_key`
metadata is set only on the entities that actually have a natural-key matcher; for id-only entities
the harness pairs via the known remap function. **Do not invent name match keys the code doesn't
honor** (v1's error).

### 3.1 Two key-mechanics, no new annotation
- **Composite key** = 2+ fields flagged `match_key` (Workflow path+function_name; Config
  key+integration_id+organization_id). Matched together.
- **Parent-scoped key** = a nested child's `match_key` field (MCPConnectionTool.tool_name,
  IntegrationConfigSchema.key), matched within its already-paired parent. The parent FK isn't in the
  manifest, so it's never tagged — scope is implicit from nesting.
- **Scrubbed key-part rule + its caveat (Codex #2):** when a path scrubs/stamps a key-part (org is
  stamped from the install on Solution paths), the matcher matches on the *surviving* parts. **This
  is only safe if the surviving parts are unique in the fixture.** For Table/Config/Claim the
  surviving parts after dropping org are `name` / `(key, integration_id)` / `name` — the harness
  fixtures MUST enforce uniqueness on those within a single target org, or two same-name rows would
  mis-pair. The generator (§5) guarantees this.

## 4. The contract (3 paths × 5 classes) — CORRECTED (Codex #1, #3), matching existing code

| Path | identity | content | environment | secret | reference |
|---|---|---|---|---|---|
| **`_repo` git-sync** (same-env, writes to a git repo) | **keep (match by id)** | keep | **keep** | **scrub** | keep |
| **Solution export `shareable`** | regen (match by natural key / remap) | keep | **stamp from target** (scrub source; except `keep_on_portable`) | **scrub** | keep (remap) |
| **Solution export `full` / backup** | regen (match by natural key / remap) | keep | **stamp from target** (NOT preserved) | **keep in encrypted SECRETS envelope** | keep (remap) |

Key corrections vs v1:
- **`_repo` keeps environment, scrubs secret** (not "keep secret"). `generate_manifest` emits
  org/role/access (`manifest_generator.py:83/268/384`) but nulls `ConfigType.SECRET` values (`:279`)
  and omits `encrypted_client_secret` (`:390`).
- **Full backup does NOT keep environment in the manifest** (Codex #3). Org is *stamped from the
  install* on deploy (`deploy.py:797` "Scope is inherited from the install"); the exported descriptor
  carries no scope (`export.py:86`). Full mode's extra is the **encrypted secrets** + optional
  **table_data** envelopes, not preserved environment.
- **Secret in full backup lives in the SECRETS envelope, not the manifest** — the manifest envelope
  scrubs secrets on every path; only the separate Fernet envelope carries them (`secrets_blob.py:50`,
  `routers/solutions.py:311`).

## 5. The other envelopes (NOT manifest fields — separate round-trip checks)

The field-class stickers label the **manifest** envelope only. These are separate checks:

1. **Table row data** (Codex #4a / Jack's catch) — `SolutionContent.table_data`, gated by
   `include_data` (default False, `mode=full` only — `routers/solutions.py:269`), encrypted +
   applied separately (`capture.py:794`, `deploy.py:814` "schema + policies only",
   `zip_install.py:697`). Separate check: rows survive a **full-backup** round trip; absent on
   shareable / `_repo`.
2. **Secrets envelope** — Fernet blob (`secrets_blob.py`). Separate check: a sentinel secret value
   survives **full-backup** encrypt→decrypt; and a **leak check** that shareable / `_repo` outputs
   do NOT contain the sentinel anywhere.
3. **Code envelope** (`.py` / app source) — not entity fields; round-trips on `_repo` and all
   Solution paths. Light check (present + byte-identical); not the focus of this harness.
4. **Solution CONNECTION DECLARATIONS** (Codex #4b — new) — a solution's declared integration
   *connections* are NOT full `ManifestIntegration`/`ManifestOAuthProvider`/`ManifestMCPServer`
   entities. They are **secret-scrubbed setup skeletons** (`integration_template.py`): config-schema
   shape + a whitelist of safe OAuth fields, with `client_id`/`client_secret`/tokens/mappings/org-ids
   **dropped by construction**. The harness needs a SEPARATE policy for this envelope — asserting the
   skeleton shape survives AND that none of the dropped credential fields ever appear — NOT the full
   integration field-class policy, or it tests the wrong shape.

## 6. PROPOSED per-entity field-class table (REVIEW EVERY ROW)

> `MK`=match_key (Solution-install matcher per §3). `env+keep`=environment, keep_on_portable.
> `secret?`=conditional via `when=`. Nested = recurse into child models. **For id-only entities
> (Form/Agent/EventSource/MCPServer) NO field carries MK — the harness pairs them by remap (§3).**

**ManifestOrganization:** id=identity · name=content **MK** · is_active=environment ⚠️(state)
**ManifestRole:** id=identity · name=content **MK**
**ManifestWorkflow:** id=identity · name=content · path=content **MK** · function_name=content **MK** · type=content · organization_id=environment · roles=environment · role_names=env+keep · access_level=environment · endpoint_enabled=content ⚠️ · timeout_seconds=content · public_endpoint=content · description=content · tool_description=content · category=content · tags=content
**ManifestForm:** id=identity *(matcher: remap, no MK)* · name=content · path=content(deprecated) · organization_id=environment · roles=environment · role_names=env+keep · access_level=environment · description=content · workflow_id=reference · launch_workflow_id=reference · default_launch_params=content · allowed_query_params=content · form_schema=content
**ManifestAgent:** id=identity *(remap, no MK)* · name=content · path=content(deprecated) · organization_id=environment · roles=environment · role_names=env+keep · access_level=environment · description=content · system_prompt=content · channels=content · tool_ids=reference · delegated_agent_ids=reference · knowledge_sources=content · system_tools=content · mcp_connection_ids=reference · llm_model=content · llm_max_tokens=content · max_iterations=content · max_token_budget=content
**ManifestApp:** id=identity · path=content · slug=content **MK** · name=content · description=content · dependencies=content · organization_id=environment · roles=environment · role_names=env+keep · access_level=environment · app_model=content · logo=content
**ManifestIntegrationConfigSchema:** key=content **MK**(within integration) · type=content · required=content · description=content · options=content · position=content
**ManifestOAuthProvider:** provider_name=content **MK** · display_name=content · oauth_flow_type=content · client_id=reference ⚠️ · authorization_url=content · token_url=content · token_url_defaults=content · scopes=content · redirect_uri=content
**ManifestIntegrationMapping:** organization_id=environment · entity_id=reference · entity_name=content · oauth_token_id=⚠️(reference vs secret — see §7.3)
**ManifestIntegration:** id=identity · name=content **MK** · entity_id=reference · entity_id_name=content · default_entity_id=reference · list_entities_data_provider_id=reference · config_schema=nested · oauth_provider=nested · mappings=nested
**ManifestConfig:** id=identity · integration_id=reference **MK** · key=content **MK** · config_type=content · description=content · organization_id=environment **MK**(stamped) · value=**secret?**(when config_type==secret else content) ⚠️
**ManifestSolutionConfigSchema:** id=identity · key=content **MK** · type=content · required=content · description=content · default=content ⚠️ · position=content
**ManifestCustomClaim:** id=identity · name=content **MK** · description=content · organization_id=environment **MK**(stamped) · type=content · query=content
**ManifestPolicy:** name=content **MK**(within parent table) · description=content · actions=content · when=content
**ManifestTable:** id=identity · name=content **MK** · description=content · organization_id=environment **MK**(stamped) · table_schema=content · policies=nested
**ManifestEventSubscription:** id=identity *(within parent source)* · target_type=content · workflow_id=reference · agent_id=reference · event_type=content · filter_expression=content · input_mapping=content · is_active=environment ⚠️(state)
**ManifestEventSource:** id=identity *(remap, no MK)* · name=content · source_type=content · organization_id=environment · is_active=environment ⚠️ · cron_expression=content · timezone=content · schedule_enabled=environment ⚠️ · overlap_policy=content · adapter_name=content · webhook_integration_id=reference · webhook_config=content ⚠️(blob) · rate_limit_*=content · subscriptions=nested
**ManifestMCPConnectionTool:** tool_name=content **MK**(within connection) · tool_schema=content · enabled=content · disabled_reason=content
**ManifestMCPConnection:** organization_id=environment · client_id=reference ⚠️ · server_url_override=content · available_in_chat=content · available_to_autonomous=content · service_oauth_token_id=⚠️(reference vs secret — §7.3) · tools=nested
**ManifestMCPServer:** id=identity *(remap, no MK)* · name=content · server_url=content · oauth_provider_id=reference · redirect_url=content · discovery_metadata=content ⚠️(blob) · organization_id=environment · is_active=environment ⚠️ · connections=nested

## 7. ⚠️ Rows needing an explicit human decision (targeted review)

1. **State flags: `is_active` (org/event/source), `schedule_enabled`, `endpoint_enabled`,
   `public_endpoint`.** v2 proposes `environment` (per-install state — a shared bundle shouldn't
   dictate active/paused in the target; the installer decides). v1 had them `content`. **Decide:
   environment or content?** (Leaning environment for is_active/schedule_enabled; endpoint_enabled /
   public_endpoint are arguably definition → content.)
2. **`ManifestConfig.value` conditional secret** — `secret` when `config_type=='secret'` else
   `content`. Needs `when=`. Confirm predicate; confirm no other field is conditionally secret.
3. **`oauth_token_id` / `service_oauth_token_id`** — token *references* (token lives in DB, not
   manifest). `_repo` emits the id (`manifest_generator.py:261`), arguing **`reference`** (round-trip,
   remap) NOT `secret`. But it points at a credential. **Decide reference vs secret** (Claude leans
   reference, matching code).
4. **`client_id`** (OAuth/MCP) — emitted to `_repo` (`:247/395`), so **not** treated as secret today
   → `reference`. Confirm.
5. **Workflow match key** — `(path, function_name)` confirmed (Codex). `name` is fallback only, NOT a
   key. Confirm no composite-with-name is wanted.
6. **Blob fields:** `webhook_config`, `discovery_metadata`, `table_schema`, `query`, `app_model`,
   `tool_schema`, `default_launch_params`, `form_schema`. `content`, compared as **canonical JSON**
   (§8 risk 3). Confirm none can embed a secret (a secret-in-blob bypasses scrubbing → needs a rule).
7. **`ManifestSolutionConfigSchema.default`** — a schema default value; `content`. If a schema can
   default a *secret* config, leak vector. Confirm.
8. **Deprecated `path`** (Workflow/Form/Agent) — content is inline; generator no longer emits `path`.
   Harness asserts ABSENT both directions. Confirm no dedicated `deprecated` tag wanted.
9. **`nested`** — NOT a class; the container field holds child models and the harness recurses,
   applying child field-classes. Confirm the container field gets `classify(CONTENT)` as a structural
   default while the real assertion is on recursed children.

## 8. The harness shape + the 3 false-confidence defenses (Codex risks)

- **`api/bifrost/field_classes.py`** — enum + `classify()` + introspection (`field_class_of(model,
  field, row)` resolving `when=`; `match_keys(model)`).
- **`RoundTripPath` objects** — `REPO_SYNC`, `SOLUTION_SHAREABLE`, `SOLUTION_FULL`, each declaring its
  §4 per-class policy + how it pairs (`by_id` vs `by_natural_key` vs `by_remap`), exposing
  `run(rows) -> rows` that drives the REAL code (`generate_manifest`/`ManifestResolver`; the
  `_collect_*` + `deploy`; the Fernet `capture`/`zip_install`). **Plus** a `CONNECTION_DECL` check
  (§5.4) and the `table_data` / secrets-envelope checks (§5.1–5.2).
- **Generators** — per entity: `all_fields_populated()`, `each_field_isolated()`, `known_tricky()`
  (agent-delegation order; nested event subscriptions; cascade override; conditional-secret both
  ways; org-name-collision to exercise the §3.1 uniqueness caveat). Bounded, deterministic, **no
  Hypothesis**.
- **Defense vs Risk 1 (generator misses a field / only defaults):** a **generator-completeness
  tripwire** introspects `model_fields` and asserts the generator sets a **non-default sentinel** for
  every field. A new field with no sentinel = hard failure.
- **Defense vs Risk 2 (drivers reimplement instead of calling real code):** `RoundTripPath.run` MUST
  call the real exported functions end-to-end (`generate_manifest`, the real collectors, real
  `deploy`, real export-zip + decrypt + install). A reimplementation is the test lying to itself; a
  code-review gate on the driver enforces "calls real API, no inline re-derivation."
- **Defense vs Risk 3 (loose equality on blobs / secrets):** dict/JSON fields compared via
  **canonical JSON** (sorted keys); secrets asserted via **explicit decrypt-then-equal**; plus a
  **sentinel-leak assertion** that shareable / `_repo` outputs contain the sentinel secret string
  NOWHERE.

## 9. Sequencing (settled: harness FIRST)
Phase 1.5 (this spec → its plan), BEFORE convergence: (1) `field_classes.py` + tripwire; (2) tag all
20 entities per approved §6; (3) the `RoundTripPath` drivers + the 4 envelope checks over REAL code;
(4) generators + assertion + completeness tripwire; (5) run; fix the drops the harness surfaces to
green (`auto_fill`, agent-delegation order, `max_run_timeout`/`event_type`/`display_name` parity —
the prior plan's prose §Handoff becomes failing tests). THEN convergence keeps the harness green; the
metadata IS axis-A and the `RoundTripPath` policies ARE the per-path ReconciliationPolicy instances.

## 10. Open questions for review
- §7 rows 1–9 — each needs a confirm/correct.
- Whether the `CONNECTION_DECL` skeleton (§5.4) should get its own tiny field-class set or just an
  allow/deny field whitelist mirroring `_SAFE_OAUTH_FIELDS` + the dropped list.
- Whether id-only entities (§3) should ALSO grow a real natural-key matcher as part of convergence
  (would make Solution reinstall idempotent by name) — likely a convergence decision, not this spec.
