# Moving things TO Solutions — the Capture story (design proposal)

Status: proposal, not implemented — needs Jack's sign-off on the open decisions
Date: 2026-06-12
Companion to: `2026-06-04-solutions-success-criteria.md`
Prompted by: feedback 2026-06-11 — "Do we have a story for moving things TO
solutions? Particularly tables. I'm thinking how we move messes from `_repo/`
like apps which will certainly be utilizing workflows, tables, configs, etc.
that were really meant to be specific to that thing."

---

## 1. The problem

`_repo/` accumulates per-client/per-product clusters: an app plus the
workflows, tables, and configs that exist only to serve it. Conceptually each
cluster is a Solution; physically it is loose entities in the shared
workspace. Today the only road into a Solution is *authoring* a workspace and
deploying it — there is no road for *live* entities. Hand-rolling the move
means re-creating tables (losing rows), re-creating workflows (breaking
`path::func` references from forms), and hand-writing manifests.

The hard part is **tables**: their rows are production data. Any design where
the move creates a *new* table row-copy is a migration project; the design
below moves ownership in place and never touches rows.

## 2. What already exists (the building blocks)

| Mechanic | Where | What it proves |
|---|---|---|
| Orphan + re-adopt | uninstall orphans tables/config values with `origin_solution_*` provenance; the next install of the same slug re-adopts them BY NAME, keeps their real (non-uuid5) ids, and protects them from the reconcile sweep | ownership can move in place; deploy already tolerates same-install rows whose ids are not `uuid5(install, manifest_id)` |
| Export-at-write-time (shipped 2026-06-12) | every write persists the install's workspace zip to `_solution_exports/{id}.zip`; `GET /{id}/export` serves it | the platform can hand you a complete, re-installable workspace for an install |
| Deploy ownership guard | a bundle id owned by `_repo/` (`solution_id IS NULL`) is refused | accidental hijack is structurally blocked — capture must be an explicit, separate operation |

## 3. Proposed design — Capture = adopt in place, then export

One new admin operation:

```
POST /api/solutions/{id}/capture
{ "workflows": [ids], "tables": [ids], "apps": [ids], "forms": [ids],
  "agents": [ids], "configs": [keys] }
```

CLI: `bifrost solution capture --solution <id|slug> --app portal --table docs …`
UI: "Capture into Solution…" action on the solution page (entity picker,
pre-seeded by the dependency walker, §3.3).

Semantics, under the install's write lock:

1. **Validate**: every entity must be unowned (`solution_id IS NULL`) and in a
   scope compatible with the install (same org, or global entities into a
   global install). Owned-by-another-install → 409, same guard philosophy as
   deploy.
2. **Stamp ownership in place**: set `solution_id` on the rows. Tables keep
   their id, their policies, and **every row of data** — nothing is copied or
   recreated. From this instant the entities are solution-managed: read-only
   in the UI, mutations 409, Managed badge — all existing enforcement applies
   with zero new code.
3. **Move the code**: for captured workflows, move their Python source (and
   `modules/*` files they import, found by the existing import scanner) from
   `_repo/` to `_solutions/{id}/` — same relative paths, so `path::func`
   references from forms keep resolving. App source: captured apps are
   `_repo/`-era apps, so their source lives in `_repo/apps/<slug>` — move it
   into the bundle (and stop serving it from `_repo/`).
4. **Declare configs**: captured config keys become `SolutionConfigSchema`
   declarations on the install; the existing Config VALUES stay exactly where
   they are (they are instance-owned — same rule as install-time values).
5. **Persist the export**: rebuild the install's bundle (now including the
   captured entities) and write the export zip — capture is "a write", so it
   produces a bundle like every other write. The operator downloads the
   export and commits it to the solution's workspace/git repo; from then on
   the normal one-writer deploy flow owns everything.

### 3.1 The identity problem (the one real technical nub)

Deploy remaps every manifest id to `uuid5(install, manifest_id)`. A captured
entity keeps its live id, which is NOT a uuid5 of anything — so the next
deploy from the exported workspace would see "manifest id X → remapped X′,
X′ doesn't exist; live row X not in bundle" → create a duplicate and sweep
the original (for a table: **data loss**). Two options:

- **(a) Rewrite ids at capture time** to `uuid5(install, live_id)` so the
  exported manifest round-trips through remap onto the same rows. Touches
  every FK referencing the entity (form→workflow, app_roles, policies,
  documents→table…) — invasive, and external references (bookmarked form
  URLs) break.
- **(b) Identity-preserving remap exception** *(recommended)*: in
  `_remapped_bundle`, when a manifest id already exists owned by THIS
  install, keep it as-is instead of remapping. Fresh installs (criterion 9,
  multi-install) are unaffected — their manifest ids never exist on the
  target install. One condition, one unit test, no FK surgery, no broken
  URLs. The orphan-adopt path already established the precedent that
  same-install rows may keep non-uuid5 ids.

### 3.2 Scope guard — "genuinely shared stays in `_repo/`"

Capture must not vacuum up shared infrastructure (§3.3 of the success
criteria: shared importable code stays in `_repo/`). Before stamping, check
each candidate for references from OUTSIDE the capture set:

- a workflow referenced by a form/agent/app not in the set,
- a `modules/*` file imported by a workflow not in the set,
- a table named in `sdk.tables.*("…")` calls of a workflow not in the set.

Outside references → block (or `--force` with an explicit warning listing the
dependents). This is the same dependency scan the walker (§3.3) runs, used in
reverse.

### 3.3 Dependency walker (the UX that makes it usable)

Given a seed (usually an app), pre-compute the candidate set:

- app source → `useWorkflow("path::func")` / `executeWorkflow(` strings →
  workflows;
- workflow source → `sdk.tables.<op>("name")` → tables; `sdk.config["KEY"]` /
  `sdk.config.get("KEY")` → configs; `from modules.x import` → module files;
- forms whose `workflow_id`/`path::func` target a captured workflow.

Present as a checklist ("capturing **portal** pulls in 9 workflows, 6 tables,
2 configs, 1 form — review"), individually deselectable. The walker is
heuristic (string scan); the checklist makes the human the authority. The
same scan already exists in spirit in the vendoring shared-dep scanner —
reuse it.

### 3.4 What capture does NOT do

- No row copying, no data migration, no S3 app-source persistence beyond the
  export zip (§3.6 still holds).
- No cross-org moves (capture inherits the install's scope; moving an org A
  entity into an org B install is out of scope).
- No un-capture — leaving a solution is the existing uninstall path
  (tables/config values orphan back to ordinary org entities, which IS the
  inverse operation, already shipped).

## 4. Implementation sketch (rough order)

1. Identity-preserving remap exception in `_remapped_bundle` + unit test
   (small, unblocks everything; option (b) above).
2. `capture_entities(db, install, selectors)` service: validate → stamp →
   move python/app source → declare configs → rebuild bundle → persist
   export. Unit tests per entity type; e2e: capture a `_repo/` table with
   rows + a workflow a form references → rows survive, form still executes,
   next deploy from the export is a no-op.
3. Reverse-dependency guard + walker (reuse vendoring scanner).
4. CLI `bifrost solution capture` (+ `--dry-run` printing the walker result).
5. UI: "Capture into Solution…" with the checklist.

## 5. Open decisions for Jack

1. **Remap exception (3.1b) OK?** It relaxes "manifest ids never touch the
   DB" to "…except ids this install already owns". I think it's sound; it is
   the one place capture touches deploy invariants.
2. **Move vs copy for app/python source out of `_repo/`**: proposal says MOVE
   (the mess leaves `_repo/`, which is the point) — but that's destructive to
   `_repo/` git history's working tree and shows up in the next `_repo/`
   sync as deletions. Acceptable?
3. **Where capture is initiated**: solution page ("pull entities in") vs the
   entity pages ("send to solution…"). Proposal: solution page only, first
   pass.
4. Should capture require the install to be **disconnected** (manual)?
   Git-connected installs auto-pull from their repo; capturing into one
   creates state the repo doesn't have until someone commits the export.
   Proposal: allow, but show "commit the export to the repo before the next
   sync" as a blocking-style warning (sync would otherwise reconcile the
   captured entities away — that's the one-writer contract working as
   designed, but surprising).
