# Solutions + Knowledge — decision record (V1 done, corpus-carry is V2)

**Date:** 2026-06-15
**Supersedes:** the "knowledge own-first = V1" framing in
`docs/superpowers/specs/2026-06-14-solutions-v1-coverage-design.md` §1 and
`docs/superpowers/plans/2026-06-14-solutions-knowledge-ownfirst.md` (that plan is now
**shelved** — see "What changed" below; do not execute it as written).

## TL;DR
- **V1 knowledge: nothing to build. It already works.** A solution's agent carries its
  `knowledge_sources` (namespace strings); that binding already captures/deploys/imports. On install
  the operator populates the namespace (the install-preview knowledge note, shipped 2026-06-14, warns
  them). For a solution that expects customer-specific knowledge, this is correct and complete.
- **Carrying the corpus (pre-loaded, isolated, backed-up) is a real feature — but it's V2.** It's
  write-path + read-path + export/restore, and its central design problem is embedding portability.

## How knowledge is stored (so the decision is legible)
- One table, `KnowledgeStore` (`api/src/models/orm/knowledge.py`). Each row = one CHUNK of one document:
  `namespace` (free-form string), `organization_id` (NULL = global), `key`, `chunk_index`, embedding
  vector, text. Unique on `(namespace, organization_id, key, chunk_index)`.
- A "knowledge base" is **all rows sharing a `namespace` string** — not a row of its own.
- **`KnowledgeStore` has NO `solution_id` column.** (Tables/agents/workflows got one; knowledge did not.)
- An agent's `knowledge_sources` is a `list[str]` of namespace names (`agents.py:64`). That list — the
  POINTER — already travels in a solution bundle (`capture.py:556` → manifest → import). The DOCUMENTS
  (the `KnowledgeStore` rows) do NOT travel: capture/deploy carry zero knowledge rows.
- Agents query knowledge IN-PROCESS via `KnowledgeRepository` (`agent_executor.py:1499-1556`), not the
  HTTP route. Workflows query via `api/bifrost/knowledge.py` → `POST /api/sdk/knowledge/search`
  (`cli.py:2318`), a `CurrentUser` route (org/global cascade, no solution awareness).

## Why own-first is NOT a V1 gap (the correction)
Own-first is load-bearing for TABLES because deploy CREATES solution-owned table rows that a plain
name-lookup would miss — a real bug `_resolve_solution_table_by_name` fixes. **Knowledge deploys zero
rows**, so there is nothing for own-first to prefer; the namespace is empty after install regardless.
Adding `solution_id` to `KnowledgeStore` for V1 would add a column nothing ever writes (deploy doesn't
ingest). The only sharp edge — two solutions pick the same namespace string AND an operator loads docs
for both into one org — is rare, operator-visible, and mitigated for free by a namespace-naming
convention (e.g. `acme-crm/support_kb`). Recommend documenting that convention; build nothing.

## The V2 feature: "Solutions carry & back up their own knowledge corpus"
Shape (the `if declared use solution_id else none` model Jack described — write-aware, not just a read
preference):
- `solution_id` column on `KnowledgeStore` (+ it joins the unique key, so two installs hold the same
  namespace independently → **isolation**).
- Ingest (`KnowledgeRepository.store_chunked`) stamps `solution_id` when in solution context; search
  reads solution-scoped rows when declared, else today's org/global cascade (additive; non-solution
  paths unchanged).
- SDK threads `ctx.solution_id` — **trivial, no route surgery**: `ctx.solution_id` is just
  `request.query_params.get("solution")` (`auth.py:320`); the knowledge SDK adds a `solution` body
  field from `_execution_context.get().solution_id` (mirror `api/bifrost/tables.py:24-41`). Agent path
  is in-process: pass `agent.solution_id` to the repo. No `/api/sdk` Context refactor needed.
- **Backup/restore + capture include-data:** because rows now carry `solution_id`, knowledge documents
  can travel in the bundle's data export and reattach on reinstall scoped by `solution_id` — the same
  machinery table rows use. This is the actual value (pre-loaded corpus + survives backup/restore as the
  solution's own data). Read-only guard coverage follows from the column.

### The central V2 design problem: embeddings don't travel
**If the target instance doesn't use the same embedding model, restored vectors are meaningless — you
must re-index.** (Confirmed instinct; see memory `project_knowledge_embedding_space_drift` — a config
change already stranded a corpus once.) So the corpus-carry feature cannot just ship vectors:
- Same-instance backup/restore (same embedding config): carrying vectors works.
- Cross-instance / community share: carry the SOURCE TEXT and **re-embed at restore time** using the
  target instance's config. That means an ingest pipeline at restore, and pinning/recording the
  embedding config so a mismatch is detected, not silently wrong.
This is the question to resolve FIRST in the V2 brainstorm — it determines whether the bundle carries
vectors, text, or both, and whether restore triggers re-embedding.

## Why V2, not V1.5
This is a new capability (ship + isolate + back up a corpus), not a gap-fill on the V1 surface. Its
correctness hinges on the embedding-portability decision, which deserves its own brainstorm rather than
a tack-on to the V1 coverage push. V1 stands on its own: the binding travels, operators populate, the
preview warns.

## When V2 is picked up
Start with brainstorming on the embedding question (carry-text-and-reindex vs same-instance-vectors),
THEN spec the write/read/backup build off this record. Do it awake with a live drive — knowledge search
is the fragile surface.
