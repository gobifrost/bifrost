# Plan — Solutions knowledge own-first resolution (NOT YET BUILT)

**Date:** 2026-06-14
**Design:** `docs/superpowers/specs/2026-06-14-solutions-v1-coverage-design.md` (§1)
**Status:** PLAN ONLY. Deliberately not built in the overnight triggers run — the route/Context
threading is the kind of silent wrong-scope surface that needs awake, live-driven verification
(embedding search is flagged fragile in memory `project_knowledge_embedding_space_drift`).
**Branch when built:** continue on `solutions/connection-references` (or a fresh worktree).

## Goal
A Solution's agent (and a solution workflow's `knowledge.search`) resolves its OWN install's
knowledge namespace first, then falls back to org, then global — an **additive** 3-leg cascade
(own → org → global), mirroring how tables resolve. Today knowledge cascades org→global only;
it has **no solution leg at all**.

## Why this is LOWER reuse than triggers (~80% net-new)
Triggers reused the generic capture/reconcile/guard machinery. Knowledge can copy the *design* of
tables' own-first but not the *code*, because:
- `KnowledgeStore` has **no `solution_id` column** (`api/src/models/orm/knowledge.py:55-57` — org only).
- `KnowledgeRepository` (`api/src/repositories/knowledge.py:43`) is an `OrgScopedRepository` in name
  only — `search()` writes its OWN inline `(org==target)|(org IS NULL)` cascade
  (`knowledge.py:178-188`), not the base primitive. So own-first must be added inline, bespoke.
- The knowledge **SDK** (`api/bifrost/knowledge.py`) sends only `scope` — no `_scope_query`/`?solution=`
  analog like the tables SDK (`api/bifrost/tables.py:24`).
- The search **route** `cli_knowledge_search` (`api/src/routers/cli.py:2315-2349`) takes a plain
  `CurrentUser`/`UserPrincipal` + `request.scope` — it has **no execution Context and never sees
  `ctx.solution_id`** (unlike tables, which run under an execution Context that carries it). This is
  the crux: the install id must be threaded into a surface that doesn't have it today.

## Tasks

### K1 — Schema: `solution_id` on KnowledgeStore
- Migration + ORM column `solution_id` (nullable FK `solutions.id` ON DELETE CASCADE, indexed),
  mirroring `Table.solution_id`. Read-only guard coverage follows automatically (keys off the column).
- **Open question to settle:** the unique key today is `(namespace, organization_id, key, chunk_index)`
  (`knowledge.py:111-115`). Decide whether `solution_id` joins the unique key (so two installs can hold
  the same namespace name independently — likely yes, mirroring per-install table scoping) and add the
  partial unique indexes (own vs _repo vs global) like the custom_claims migration did.

### K2 — Own-first leg in the repository
- In `KnowledgeRepository.search` (`knowledge.py:132-188`): when an install id is present, run a first
  pass `WHERE solution_id == <install>` (org-gated for non-superusers, mirroring tables.py:682), and if
  it yields results, prefer them; else fall through to the EXISTING org/global cascade. Additive — global
  is never removed, it's the bottom leg.
- Same own-first add to `delete`/`count`/`list_namespaces` only if a solution needs to manage those by
  install (probably just `search` + `list_namespaces` for V1).

### K3 — Thread the install id into the search surface
- SDK: add a `_scope_query`-analog to `api/bifrost/knowledge.py` that appends `solution=<ctx.solution_id>`
  (copy `api/bifrost/tables.py:24-39`).
- DTO: add `solution: str | None` to `CLIKnowledgeSearchRequest` (contracts/cli).
- Route `cli_knowledge_search`: accept the install id from the request and pass it to
  `KnowledgeRepository(...)` / `repo.search(..., solution_id=...)`. **This is the riskiest change** —
  the route is a user surface, so gate the client-supplied install id to the caller's org-or-global
  (a caller must not name a FOREIGN org's install), exactly like `_resolve_solution_table_by_name`'s
  org gate (tables.py:682-688).
- Agent path: `AgentExecutor._execute_knowledge_search` (`agent_executor.py:1499-1556`) — a
  solution-managed agent should search its install's namespaces own-first. The agent's `solution_id`
  is on the Agent row; pass it into the repo search. Verify the existing `fallback=True` (org+global)
  still applies as the lower legs.

### K4 — Deploy: declaration-only (V1)
- V1 deploys the namespace BINDING + `solution_id` ownership, NOT the documents. Pairs with the
  install-preview knowledge note already shipped (the audit run) that warns the corpus must be populated.
- Document-carry (capturing `KnowledgeStore` rows + embeddings into the bundle) is a SEPARATE follow-up:
  large bundles + embedding-config portability risk (`project_knowledge_embedding_space_drift`). Out of
  scope for the first knowledge cut.

### K5 — Tests
- Unit: repo `search` own-first (install rows preferred, then org, then global); org gate rejects a
  foreign install id for a non-superuser.
- E2E: a solution-managed agent resolves its install namespace own-first; a `_repo` agent unaffected;
  read-only guard 409s a managed KnowledgeStore mutation.
- Live drive (REQUIRED, awake): deploy a knowledge-backed agent in a solution, populate the namespace,
  confirm the agent retrieves the install's docs and NOT another org's same-named namespace.

## Risk notes
- The route-threading (K3) is where a silent cross-scope leak would hide. Drive it as the exact
  principal (a non-admin org user) via a real agent run, not just unit tests — per memory
  `project_org_scoping_blocker2_retracted` (test scope by running as the principal, not static grep).
- Don't reuse a forced shared own-first helper with tables — the shapes differ (name-lookup vs vector
  search); a ~5-line inline own-first in each is cleaner than awkward coupling.

## Build order when picked up
K1 (schema) → K2 (repo own-first) → K3 (thread install id, the careful one) → K4 (declaration deploy) →
K5 (tests + live drive). Estimate: larger than triggers; do it in a dedicated awake session.
