# Capability Source Model — Viability Report

**Prepared for:** Bifrost engineering leadership (go/no-go)
**Subject doc:** `docs/plans/2026-06-03-capability-source-model.md`
**Scope of review:** execution engine, module cache, repo storage / file index, manifest models + generator + importer, github-sync, app bundler, CLI sync/watch, REST routers, RBAC/org-scoping, OAuth/integrations, typegen/frontend, tests/CI, existing data shape. Grounded in code with file:line citations across four review phases.

---

> **Review provenance.** Produced by a 4-phase adversarial multi-agent study (41 agents, ~4.9M tokens). Phase A mapped 17 subsystems in parallel; 2 readers (execution-engine, rest-routers) failed to emit structured output, so the blast-radius table ran on 15/17 maps — but both gaps were independently re-covered in Phase B (Claim 1: engine path-based, confirmed high-confidence; Claim 2: router path assumptions, partially-true high-confidence), so no area is unreviewed. Every claim below is backed by file:line citations gathered against the live worktree code.


## 1. Verdict

**VIABLE — AS A PHASED, ADDITIVE PROGRAM. NOT viable as the multi-repo / immutable-lockfile / "Vite apps" vision the doc leads with.**

The redesign rests on one true and load-bearing fact: **the execution engine is purely path-based and capability-agnostic.** A workflow UUID resolves to a DB row, then to `path::function_name`, then to a Redis/S3 `_repo/{path}` module load. There is no `code` column on the Workflow ORM; the engine never reads code from the DB; capability-prefixed paths work at runtime today with zero engine changes (Phase B, Claim 1, confirmed high-confidence). This is the single best thing the design has going for it and it is genuinely solid.

Because of that, the **foundational, additive form of the design is low-risk and has the "stop halfway and still works" property**: capability ownership can be layered on as nullable metadata, existing bare-path entities coexist with capability-owned ones indefinitely, and execution does not care (Phase B, Claim 7, confirmed).

But the doc's *headline* program is not the additive form. Its three marquee promises each collapse on contact with the code:

1. **"Runtime remains unchanged" hides a destructive sync-layer hazard** (the deletion sweep). The riskiest phase is the one the doc labels low-risk.
2. **An immutable per-environment lockfile is architecturally incompatible** with the system's actual git model (one repo == the S3 `_repo/` tree) and its daily live-edit loop (`watch`/`push` write straight to `_repo/` with no git step).
3. **"Portable capability repos that round-trip across environments" overstates reality.** Cross-entity refs are predominantly UUID-only in the actual export; the portable scrub rewrites none of them; the path::function machinery is wired for 2 of ~8 ref kinds and is never produced by the exporter.

And it would inherit two concrete defects in the existing boundary it builds on (MCP service-token leak; un-org-scoped manifest generation).

**Recommendation:** Approve the additive foundation (Phases 1+3 in the doc's own "Initial Recommendation"), gate it on three prerequisite fixes, and treat multi-repo (Phase 4), shared-dependency resolution (Phase 5), and Vite apps (Phase 6) as **separate initiatives requiring independent justification** — two of which are orthogonal to or incompatible with the "no engine change" premise and must not be sold as "enabled by capabilities."

---

## 2. Blast Radius by Subsystem

| Subsystem | Change impact (Phase 1 additive) | Change impact (Phase 4 multi-repo) | Why |
|---|---|---|---|
| Execution engine | **None** | None | Path-agnostic; UUID -> path -> Redis/S3. No `code` column (workflows.py:45-148). |
| Module cache | None | Low (partitioned keys for capability-aware invalidation, later) | Path-agnostic; accepts any string key. |
| Repo storage / file index | None | Low | `_repo_key()` is structure-agnostic; FileIndex stores paths as-is. |
| Virtual import hook | None (capabilities); **constraint** (shared) | Medium (version/namespace awareness) | `capabilities.<slug>.modules.x` resolves via PEP-420 namespace branch; `shared.x` can be shadowed by the API's own `/app/shared` package (Claim 3). |
| App bundler | None | Low (accept explicit repo_path) | Fully `repo_prefix`-driven; `bundle.js` has zero `apps/` refs (Claim 5). |
| Manifest models | **High** | High | New optional `capability_id`; portable-ref encoding for cross-entity refs. |
| Manifest generator | **High** | High | Emit capability ownership; switch cross-entity refs from UUID to path::function/@name; org-scope generation. |
| Manifest importer / github-sync | **High** | High | Path-rewrite-on-install; per-source deletion scoping; source provenance; per-capability `.bifrost/` discovery. |
| CLI sync/watch/export/import | Medium | High | App detection hardcodes `apps/` (cli.py:2262,2268; migrate_imports.py:223); hydration metadata; source_install round-trip. |
| REST routers | Low (accept any path) | High (provenance, list-by-capability, write-guard) | Routers accept arbitrary path strings; new endpoints + auth layer for Phase 4. |
| RBAC / org-scoping | Medium | Medium | New Capability entity through OrgScopedRepository; decide whether org-scoped capabilities cascade scope to owned entities. |
| OAuth / integrations | High (design choice) | High | Portable/env-owned split already mostly exists; needs capability tagging; **service_oauth_token_id leak must be fixed**. |
| Typegen / frontend | Medium | Medium | Types insulated (generated from OpenAPI); CLI app-slug regex breaks on capability paths. |
| Tests / CI | Medium | Medium | ~84 test files hardcode `workflows/`/`apps/` paths; fixtures and cleanup sweeps must accept capability prefixes. |
| Existing data shape | **Critical** (if entities are moved) | Critical | Bare-path rows need coordinated DB+S3+artifact migration; no tooling exists. |

**Reading of the table:** the storage/execution/bundler core is genuinely insulated (the doc is right about that). The blast radius concentrates in the **manifest + sync + CLI** layer and in **existing-data migration** — exactly where the doc is thinnest.

---

## 3. Claim Adjudication

| # | Claim | Verdict | Confidence | What it means for go/no-go |
|---|---|---|---|---|
| 1 | Engine is purely path-based; no execution path reads code from the DB | **Confirmed** | High | Foundation is sound. Capability paths work at runtime with no engine change. Docstrings ("DB-first", "from database") are misleading but behaviorally harmless. |
| 2 | No route hardcodes `workflows/`/`apps/` in a way that breaks capability paths | **Partially true** | High | All *functional* paths are prefix-agnostic. Two CLI convenience helpers hardcode `apps/` (cli.py:2262,2268; migrate_imports.py:223) with working escape hatches. No `workflows/` prefix hardcoded anywhere. |
| 3 | Virtual import resolves `from capabilities.<slug>...` and `from shared.x` with only a slug constraint | **Partially true** | High | `capabilities.<slug>.modules.x` — clean, only the import-safe-slug (underscore) constraint. `shared.x` — can be shadowed by the API's own `/app/shared` package (auth.py:18, main.py:272); the hook is *appended* to sys.meta_path (virtual_import.py:435), so PathFinder wins for colliding names. Pre-existing, not introduced by the design, but the doc's "only a slug constraint" understates it. |
| 4 | Manifests are portable enough for capability repos to round-trip; refs not predominantly UUID-only | **Partially true** | High | Round-trip *works* — but via UUID preservation, not path-based refs. Refs ARE predominantly UUID-only in the real export; the scrub rewrites none of them; round-trip only holds when the referenced entity travels in the same bundle. Cross-repo capability splits break this — exactly the scenario the design introduces. |
| 5 | Capability-owned apps build with no bundler change, only a path change | **Confirmed** | High | The build path is repo_prefix-driven end to end. Caveat: app *creation* hardcodes `apps/{slug}` (applications.py:148) and scaffolding writes there — a capability app needs a changed default or a post-create repoint + source move. |
| 6 | Manifest-import layer is the right and sufficient place to add source awareness without making the engine multi-repo aware | **Partially true** | High | "Right place" + "engine stays path-based" — confirmed. "Sufficient" — false: source bytes enter `_repo/` via CLI+files router (not import); path-rewrite must be added; deletion sweep is global and must be re-scoped; CLI app detection needs updating; import only discovers a single top-level `.bifrost/`. |
| 7 | Existing capability-less entities coexist indefinitely without forced migration | **Confirmed** | High | No capability concept exists to be null against; execution resolves by UUID+is_active+path. "Coexist" must mean "leave paths where they are," NOT "retroactively re-home" (that's the HIGH-risk MOVE scenario). |
| 8 | Integration/config/OAuth env-bindings are cleanly separable from portable source | **Partially true** | High | Secret/OAuth dimension is robust and well-tested. BUT non-secret tenant bindings (mapping entity_id, non-secret config values, default_entity_id, webhook_config, MCP server_url_override, discovery_metadata) pass through export verbatim, and **service_oauth_token_id leaks** (absent from scrub, untested). A lockfile built directly on the current scrub carries tenant identifiers and a live token FK. |

---

## 4. Hard Problems

### HP-1 — Backwards compatibility / coexistence (the migration story)
**The doc under-specifies implicit-default vs null-capability vs forced migration, and asserts "runtime unchanged" without naming the destructive sync coupling.**

- **The good news (cheap coexistence):** Workflow.path is an unconstrained String; the only uniqueness rule is `(path, function_name)`. Capability paths work at runtime immediately when DB row and S3 file agree. There is no Capability/SourceInstall/lock concept anywhere yet.
- **The landmine (verified):** `_resolve_deletions` builds `present_wf_uuids` gated on `_path_exists(mwf.path)` (manifest_import.py:1692-1695) and `_bulk_delete` hard-deletes any active workflow whose UUID is absent from that set (1734-1762), cascading to `agent_tools`/`agent.workflow_id` FKs. **A path move that updates the manifest before the S3 file lands deletes the workflow and its agent bindings — silently.** The sweep defaults to opt-in (`delete_removed_entities=False`), which softens but does not remove the hazard for any cleanup-enabled multi-repo flow.
- **Why forced alembic is dangerous here:** alembic can rewrite `workflows.path` but cannot move S3 objects, warm Redis, or rebuild app bundles. A DB-only path rewrite makes every affected workflow fail to load. There is no coordinated DB+S3+cache migration primitive.

**Options:** (A) null-capability coexistence — additive nullable metadata, no path move; (B) opt-in atomic move (S3-write -> DB-update -> old-S3-delete), one capability at a time, sweep disabled/scoped; (C) forced alembic backfill — rejected (S3-blind, breaks everything); (D) dual-read fallback — rejected (violates the repo's explicit "no unrequested fallbacks" rule, citing the process_pool leak).

**Recommendation:** **A as the foundation, B for entities that actually relocate.** Make "an entity is runnable iff its DB path and S3 file agree; capability ownership has zero bearing on runnability" an explicit, tested invariant. **Fix the deletion-sweep gating (re-gate on "UUID absent from manifest," not "path file missing") BEFORE any path-moving work.** This is a prerequisite, not a Phase-4 detail.

---

### HP-2 — UUID portability for portable capability repos
**Cross-entity refs are env-specific UUIDs; the same logical workflow has a different UUID per env.**

- **Inventory (manifest.py):** UUID-only fields — form.workflow_id/launch_workflow_id (114-115), agent.tool_ids/delegated_agent_ids/mcp_connection_ids (148-152), integration.list_entities_data_provider_id (227), event subscription.workflow_id/agent_id (299-300), config.integration_id (236). Entity self-IDs are preserved across export by design.
- **The exporter never emits path::function** (manifest_generator.py:153,224,357,525). The portable scrub (portable.py) rewrites org IDs and role-IDs->names but touches NO cross-entity ref. So the import-side path::function resolvers — wired only for form workflow refs and agent tool_ids, plus a 3-tier resolver for event subscription workflow_id — are dead on the happy path.
- **Strict-UUID gaps with no fallback:** integration data_provider (`UUID(...)` only), delegated_agent_ids (passed through unresolved), subscription agent_id. These dangle or silently drop across environments.
- **Apps are different and worse for tooling:** app->workflow refs live only as string literals in TSX (`useWorkflowQuery('...')`), in no manifest field, invisible to validation.

**Recommendation (Option D — hybrid, prerequisite for Phase 4):** workflows -> emit `path::function` (capability-prefixed) and extend resolvers to integration data_provider + subscriptions; agents/integrations/configs -> name-based remap pass at import (`@agent:`/`@integration:`, mirroring the existing `@role` convention); add a portable-scrub rule that converts cross-entity UUIDs to portable form; **fail loud on unresolved refs** (mirror `_resolve_role_names` raising ValueError) instead of the current log-and-skip; document app TSX name-refs as the one portable surface and add them to validation. Do not pursue the new-slug-column approach (Option B) yet — it fights the canonical `(path, function_name)` natural key.

---

### HP-3 — Environment bindings & secrets (the lockfile boundary)
**The design proposes a lockfile with inline `oauth_token: env-owned` / `value: secret/env-owned` placeholders. Bifrost already has a working two-layer boundary the design should reuse, not reinvent — and that boundary has a concrete leak.**

- **Already correct:** secret config values nulled at serialize (manifest_generator.py:276) and re-nulled in scrub; OAuth tokens never serialized as values; client_secret omitted; webhook `state` doubly protected. OAuth tokens encrypted at rest.
- **Verified defect 1 — MCP service-token leak:** `MCPConnection.service_oauth_token_id` is serialized (manifest_generator.py:396-399) but is **NOT** in the scrub's `_OAUTH_SECRETS` set (confirmed at portable.py:46-51) and has zero test coverage. A portable export of an env with MCP connections leaks a usable FK to a live shared service token. Any "env lock redacts secrets" acceptance test would falsely pass.
- **Verified defect 2 — no org-scoping:** `generate_manifest(db)` (manifest_generator.py:444) dumps every org's entities with no filter. A per-client `bifrost env hydrate` built on it cross-contaminates every tenant's mappings/configs/event-sources/MCP connections.
- **At-rest weakness:** `Config.value` is plaintext JSONB; the entire config-secret boundary rests on the `config_type` enum being set correctly, with no encryption backstop and no validation forcing secret-typed entry.
- **Live-data:** no row/sample-data export path exists; the doc's `--include-sample-data` implies an unbuilt PII-sanitization engine.

**Recommendation:** Lockfile = thin provenance pointer (source refs + manifest content-hash + environment_key); reuse `generate_manifest` -> `portable.scrub` as the SINGLE boundary. Reject duplicating bindings/secret-placeholders into the lockfile body (a second boundary that will drift — the codebase already documents three-surface drift as a recurring failure). **Prerequisites before any hydration ships:** fix `service_oauth_token_id` (add to scrub + test); add org-scoping to manifest generation; make hydration default-deny for bindings/secrets/live-data (placeholders only, value/sample-data behind explicit superuser-audited flags). Convert the denylist scrub toward an allowlist for the lockfile serializer (fail safe, not fail open).

---

### HP-4 — Git model reconciliation
**The doc's many-source-repos + immutable per-environment lockfile cannot coexist with how git and the daily authoring loop actually work.**

- **One repo == the runtime tree:** GitHubSyncService takes a single `repo_url` + single `branch`; the working tree at `/tmp/git` is materialized from S3 by `aws s3 sync s3://{bucket}/_repo/` **including `.git/` stored under `_repo/.git/`**. The entire flat prefix is one repo on one branch. A deployment-wide Redis lock serializes all git ops.
- **The live-edit loop is git-unaware:** `bifrost watch`/`sync`/`push` POST to `/api/files`, which writes to S3 `_repo/` with no git commit (files.py:215; 381 explicitly: "Code file reconciliation is handled by git, not by this endpoint"). **A pinned lock is stale the moment anyone saves a file, and nothing records the drift.**
- **Multi-repo / submodules are greenfield:** there is no `source_installs` table, no ref pinning, no rollback, no multi-remote, no write-authorization layer; `.git/` shuttled by `aws s3 sync` cannot host nested submodule gitlinks.

**Recommendation:** **Monorepo-with-folders + branch-per-capability now (Option A); design toward per-capability DRAFT/LOCKED mode (Option C).** Capabilities are folders; ownership/review are GitHub CODEOWNERS + path-filtered CI + Kodiak — all native, zero Bifrost code. The "lock" degenerates honestly to "current HEAD of the one repo." Add a per-capability DRAFT/LOCKED flag later (LOCKED enforced by a write-guard in the file write path, routed through OrgScopedRepository for org-scoping) plus `bifrost git status` drift detection. **Explicitly reject the doc's "immutable lock + daily watch" coexistence as written** — you cannot have both for the same paths. Defer true multi-checkout hydrated workspaces (Option B) and submodules (Option D); both are rewrites of the S3<->git contract.

---

### HP-5 — Pure-React/Vite apps (doc Phase 6)
**This is orthogonal to capabilities and structurally blocked; capabilities enable none of it.**

- Today's apps are NOT a Vite project: server-side esbuild via a Node subprocess; synthesized `_entry.tsx` + synthesized `node_modules/bifrost` package; platform names proxied over `globalThis.__bifrost_platform`; deps resolved at runtime via esm.sh; "hot reload" is a websocket-driven full re-import, not HMR.
- `@bifrost/client` / `@bifrost/ui` exist **only in docs** (grep: zero code references). No `bifrost app dev`. No Vite anywhere in the pipeline.
- **The deep blocker:** `BundledAppShell` renders the bundle's default export **inline** specifically to inherit host AuthContext/QueryClient/theme/router (BundledAppShell.tsx:133-139; a prior createRoot approach broke this). A standalone Vite `main.tsx`/createRoot/BrowserRouter is structurally incompatible without re-architecting context delivery or iframe isolation.
- **The bundler is prefix-agnostic**, so capability paths build identically — proving the two problems do not touch.

**Recommendation:** Decouple entirely. Start with incremental de-magic of the esbuild pipeline (continue what `migrate-imports` started). Run any Vite work as its own initiative, NOT as Phase 6 of capabilities. Guard against the two-build-pipeline drift class (parity test). Do not promise "apps feel like Vite" as a capability deliverable.

---

### HP-6 — Shared dependency resolution (doc Phase 5)
**Transparent multi-version dedup is architecturally incompatible with the "no engine change" premise.**

- The Python namespace is flat and path-IS-module-name: `sys.modules` key == the `_repo` path; there is exactly one global `shared` and one `capabilities` per worker. Pooled, long-lived workers share one module table across capabilities. Two versions of the same `shared.*` module cannot coexist in one worker — last write wins, order-dependent.
- pip deps are even more global: one `_repo/requirements.txt` installed once into a shared filesystem for all workers. This is where the hardest conflicts (incompatible PyPI versions) actually live, and the doc ignores this layer.
- "Install once at a canonical path" is the only thing the runtime can express — which is the source of the conflict problem, not its solution. Namespaced duplicates (`shared/halo_v1`) break on transitive shared deps (the diamond problem) and force version strings into every import.

**Recommendation:** Reframe Phase 5 as **shared install-conflict DETECTION**, not resolution. Honest contract: `_repo/shared/<x>` is single-version-per-environment; a second version in one env is a fail-the-install error with a clear message. Push genuine multi-version needs to real pip/uv packages (still capped at one env-wide version until per-capability isolation is built). **Pick one:** keep the flat runtime and accept single-version-per-env, OR build per-capability import isolation (large engine blast radius). The doc tries to have both.

---

### HP-7 — Agent / developer experience regression
**The doc admits hydration quality is load-bearing; the entity-map it wants to "generate" partly exists already, with three real gaps.**

- The build skill already decouples agent context into three things — generated `llms.txt`, live CLI/MCP queries, a separate community examples repo — NOT a monolithic source tree. The monorepo advantage to preserve is "simple imports + one filesystem to grep shared code," not "one giant context blob."
- The proposed entity-map already exists as `DependencyGraphService` (form->workflow/launch/data-provider, agent->workflow, app->workflow via source scan, reverse used_by, UUID/name/path::func resolution). **But:** (1) app->workflow extraction is regex-on-literals and misses the skill's OWN recommended const pattern (`useWorkflowQuery(WF_CONST)`) — apps would show zero workflow deps; (2) no Python import edges (`from shared/...`) are tracked — the "shared code without duplication" value is invisible to agents; (3) no workflow->table edges.
- `_detect_repo_prefix` derives the install target purely from cwd-relative position — ambiguous in a multi-checkout layout. No runtime-map (local path -> `_repo` path -> UUID::function -> owning capability) exists.

**Recommendation:** A hydrated workspace must contain, at minimum: fresh `llms.txt` (from the live endpoint, never checked-in), env-matched generated types, **all shared dependency source physically present and import-resolvable** (the single most load-bearing requirement), an entity-map from the existing service **after closing its three gaps**, a runtime-map + secret/scope labeling, and a `bifrost env diff` freshness gate. Do this in the existing single workspace first (zero migration risk) before attempting multi-repo source installs.

---

## 5. Backwards Compatibility & Migration Path (the "stop halfway and still works" property)

**The additive path has the stop-halfway property; the path-moving path does not until the deletion sweep is fixed.**

- **Steady state is half-migrated.** Under null-capability coexistence, capability ownership is orthogonal to execution. NULL capability == legacy/implicit-default. Existing bare-path workflows/apps run unchanged forever. This is one additive nullable column / join table, no backfill, no S3/cache coordination — reversible and safe.
- **The invariant to enforce and test:** an entity is runnable iff its DB `path` and its S3 `_repo` file agree. Capability ownership has zero bearing on runnability.
- **Moving an entity into a capability is the dangerous operation**, and it is governed entirely by sync ordering. It requires: (a) fixing the deletion-sweep gating; (b) atomic S3-write -> DB-update -> old-S3-delete (reuse the `replace_application` pattern); (c) one capability at a time; (d) dry-run + verification gate; (e) cleanup of orphaned old S3 source. Forms with `path::func` refs must be re-imported after a rename or they dangle.
- **What has no migration tooling today:** no versioned path schema, no dual-read, no batch path-rewrite, no coordinated DB+S3+Redis job, no slug-collision rule across capabilities. All must be built before any forced or bulk move.

---

## 6. Risk Register

| Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|
| Path-move deletion cascade (sweep gated on `_path_exists`) | Critical | High if path moves attempted | Re-gate on "UUID absent from manifest"; disable/scope sweep during moves. **Prerequisite.** |
| MCP `service_oauth_token_id` leak in portable export | High | Certain on MCP-having envs | Add to `_OAUTH_SECRETS` + test. **Prerequisite for hydration.** |
| Un-org-scoped manifest generation -> cross-tenant disclosure on hydrate | High | High in multi-tenant | Add org-scoping to `generate_manifest`. **Prerequisite for hydration.** |
| Stale lockfile under live editing (no drift detection / write-guard) | High | Certain | DRAFT/LOCKED mode + `bifrost git status` drift detection; reject immutable-lock-as-written. |
| Cross-env ref dangling / silent mis-bind (UUID-only refs) | High | High for cross-repo capabilities | HP-2 hybrid; fail-loud on unresolved refs. |
| Config secrets plaintext at rest (single enum gate) | Medium-High | Latent | Encrypt secret configs OR hard-commit to placeholder-only export; accept at-rest exposure explicitly. |
| Forced alembic path rewrite breaks all affected workflows | Critical | Only if Option C chosen | Reject Option C; build coordinated DB+S3+cache job if ever needed. |
| `shared.x` shadowed by API's `/app/shared` | Medium | Latent (name-collision dependent) | Document the collision; pre-existing, not new. |
| App slug global-uniqueness vs per-capability naming | Medium | Certain when 2 capabilities share a leaf name | Define namespacing/collision rule before multi-capability apps. |
| Two-build-pipeline drift (esbuild vs Vite) | Medium | High if Vite added without parity guard | Parity test; or defer Vite entirely. |
| Diamond shared-dependency conflicts | Medium-High | Certain in multi-client envs | Detection + fail-install; push multi-version to packages; or build isolation. |
| ~84 test files hardcode `workflows/`/`apps/` | Medium | Certain | Update fixtures/cleanup to accept capability prefixes; add coexistence test. |
| CLI app detection misses capability apps (cli.py:2262,2268; migrate_imports.py:223) | Low-Medium | Certain for capability apps | Widen detection patterns (localized fix; escape hatches exist). |

---

## 7. Per-Phase Effort & Recommendation

| Phase | Doc framing | Real risk | Effort | Recommendation |
|---|---|---|---|---|
| 1 — capability folder convention inside `_repo/` | "low-risk" | **High** until deletion sweep fixed | S — additive metadata; M if entities move | Approve additive form; **fix sweep first**. |
| 2 — capability metadata / ownership | metadata | Low | S-M (nullable col / join table; manifest models) | Approve. Decide org-scope cascade question. |
| 3 — hydration / generated context | "not optional" | Medium (staleness) | M-L (entity-map gaps, runtime-map, freshness gate, org-scoped generation) | Approve; do in single workspace first. |
| 4 — multi-repo source installs + lockfile | headline | **Very high / greenfield** | XL (source_installs, ref pinning, rollback, write-guard, per-source deletion scoping, path-rewrite, S3<->git rewrite) | **Defer.** Re-justify after 1-3 prove out. Adopt monorepo-with-folders instead near-term. |
| 5 — shared dependency resolution | resolution | **Architecturally constrained** | M (detection) / XL (isolation) | Reframe to detection only; reject auto-resolution on flat runtime. |
| 6 — pure-Vite apps | "enabled by" capabilities | Orthogonal + blocked | XL (packages, dev server, provider-inheritance rework) | **Decouple.** Separate initiative; not a capability deliverable. |

---

## 8. Go / No-Go and What To Do First

**GO — on the additive foundation only, gated on three prerequisite fixes. NO-GO on the multi-repo/immutable-lockfile/Vite framing as written.**

**Do first, in this order (each independently valuable, none requires the full vision):**

1. **Fix the deletion-sweep hazard.** Re-gate `_resolve_deletions` so a workflow is only deleted when its UUID is absent from the manifest, not when its path file is missing (manifest_import.py:1692-1695). Add a regression test that a path rename with the same UUID does NOT delete. This makes Phase 1 actually low-risk and is the single highest-leverage change.
2. **Fix the two security/correctness defects in the existing boundary.** Add `service_oauth_token_id` to the portable scrub with a test (portable.py:46-51); add org-scoping to `generate_manifest` (manifest_generator.py:444). These are required before any hydration command and are worth doing regardless of the redesign.
3. **Add capability as additive, nullable metadata** (manifest-only `capabilities.yaml` first, per the doc's own no-new-tables option) and codify the runnability invariant as a test. Leave every existing path in place.
4. **Enrich generated agent context in the existing single workspace** — close the three entity-map gaps (const-indirected app->workflow, Python import edges, workflow->table), add a runtime-map and secret/scope labeling, add a `bifrost env diff` freshness gate.
5. **Adopt monorepo-with-folders + CODEOWNERS + path-filtered CI + Kodiak** for review ownership. This delivers the doc's git-review/ownership goal with zero new git plumbing.

Only after 1-5 are proven should multi-repo source installs (Phase 4) be re-scoped — and Vite apps (Phase 6) and shared-dependency multi-version resolution (Phase 5) should be split out as separate decisions with their own justification, because capabilities do not enable the former and cannot deliver the latter on the current runtime.

The design is worth pursuing. The engine being path-based is a real, verified asset. But the team should approve the cheap, reversible, additive core and the prerequisite fixes — and decline the expensive, greenfield, partly-incompatible headline features until they are justified on their own merits rather than bundled under "redesigning the project."