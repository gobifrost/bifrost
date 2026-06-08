export const meta = {
  name: 'solutions-scope-shakeout',
  description: 'Fan out independent agents to stress Solutions scope/cascade/read-only/UI, return triaged findings',
  phases: [
    { title: 'Find', detail: 'one agent per risk axis, drives the live stack' },
    { title: 'Critic', detail: 'name untested combinations' },
  ],
}

// Shared context every agent needs. The live stack + fixtures are already seeded.
const ENV = `
ENVIRONMENT (already set up — do NOT re-seed orgs):
- Worktree (branch code): /home/jack/GitHub/bifrost/.claude/worktrees/solutions-success-criteria — read code here.
- Live dev stack (port mode, branch code mounted): http://localhost:37791 — login dev@gobifrost.com / password (superuser, MFA off).
- Scratch CLI (API-matched, branch code rsync'd in): /tmp/bifrost-shakeout/.venv/bin/bifrost — run it FROM /tmp/bifrost-shakeout (its .env points at the stack). It is logged in; if a call 401s, re-login: \`cd /tmp/bifrost-shakeout && .venv/bin/bifrost login --url http://localhost:37791 --email dev@gobifrost.com --password password\`.
- Orgs: Provider = 00000000-0000-0000-0000-000000000002 ; ScopeTest-OrgA = 237c62c0-80f8-4031-a7ab-1fe48268c812 ; ScopeTest-OrgB = a1b883df-099e-47d9-b1b2-f3bcbe3f3d1a.
- Curl the API directly with a bearer token from the CLI's .env (BIFROST_ACCESS_TOKEN in /tmp/bifrost-shakeout/.env) when that's faster than the CLI.

RULES OF ENGAGEMENT:
- VERIFY EMPIRICALLY. The memory rule for this codebase: "test scope/auth claims by running a workflow as the exact principal via a form, not by static grep." Drive the running stack; reading code is for forming hypotheses, not for concluding.
- You may create solutions/workflows/tables/configs and install them to specific orgs to set up a test. Clean up what you can, but correctness findings matter more than tidiness.
- Do NOT modify product source. This is a read/drive audit. (Writing throwaway test fixtures via the CLI/API is fine.)
- A finding is only real if you reproduced it. Include the exact commands/requests and the observed vs. expected result.
`

const FINDING_SCHEMA = {
  type: 'object',
  properties: {
    axis: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'info'] },
          kind: { type: 'string', enum: ['scope-leak', 'enforcement-gap', 'cascade-wrong', 'ux', 'correctness', 'inconsistency', 'ok'] },
          what_i_did: { type: 'string', description: 'Exact steps/commands to reproduce' },
          observed: { type: 'string' },
          expected: { type: 'string' },
          reproduced: { type: 'boolean', description: 'true only if you actually ran it and saw the result' },
          code_ref: { type: 'string', description: 'file:line if known' },
        },
        required: ['title', 'severity', 'kind', 'what_i_did', 'observed', 'expected', 'reproduced'],
      },
    },
    coverage_note: { type: 'string', description: 'What combinations within your axis you tested and what you did NOT get to' },
  },
  required: ['axis', 'findings', 'coverage_note'],
}

const AXES = [
  {
    key: 'scope-leak',
    prompt: `${ENV}

YOUR AXIS: SCOPE LEAK. The user's #1 fear: a solution's entity ever resolving/appearing for an org it shouldn't, or solution code/data bleeding into _repo/ or another install.

Drive these (empirically):
1. Install a solution (with a workflow + a table + a config) to ScopeTest-OrgA only. Then, as a principal in ScopeTest-OrgB (use --org on bifrost run / the execute endpoint, superuser can impersonate), confirm OrgB CANNOT resolve or execute OrgA's install's workflow by name/path, cannot read its table, cannot see its config value.
2. Install the SAME solution slug to BOTH OrgA and OrgB (independent installs). Confirm each org resolves ITS OWN install's workflow, never the sibling's, even though they share path::fn. (This is the deterministic own-first rule — workflows.py _resolve_by_path_ref.)
3. Global (scope=global) install: confirm it IS visible to all orgs, but an org-scoped install is NOT visible globally or cross-org.
4. List endpoints: do GET /api/workflows, /tables, /configs, /forms, /agents leak a solution's entities to an org that didn't install it? Try as OrgB after installing only to OrgA.
5. Does a solution entity ever resolve by NAME cascade (it must resolve by id / install-scope only, never leak into a name lookup for a non-owning caller)?

Read for hypotheses: api/src/repositories/org_scoped.py (_apply_cascade_scope, ~line 348, solution_id.is_(None) at 222), api/src/repositories/workflows.py (_resolve_by_path_ref ~136-151). But CONCLUDE from running it.

Return structured findings. axis="scope-leak".`,
  },
  {
    key: 'global-repo-access',
    prompt: `${ENV}

YOUR AXIS: GLOBAL_REPO_ACCESS ("disable global") ACTUALLY WORKING — both directions. The user is "deathly afraid disable global is not working."

The flag governs whether an install's code can import from the bare _repo/ workspace at runtime. Enforcement: api/src/core/module_cache_sync.py (_candidate_storage_paths ~78-103; set_solution_context), propagated via api/src/jobs/consumers/workflow_execution.py (solution_global... -> solution_n in my redacted view) and api/src/services/execution/worker.py.

Drive these (empirically — you MUST execute workflows, not just read):
1. global_repo_access OFF: deploy a solution whose workflow does \`from modules.something import x\` where that module exists ONLY in _repo/ (NOT vendored into the solution). Execute it. It MUST fail to import (criterion 4 — a _repo/ import must NOT silently resolve). Confirm the failure.
2. global_repo_access OFF but the dep IS vendored into the solution bundle: execute — MUST resolve the vendored copy, NOT _repo/. Confirm which copy ran (make the two copies return different values).
3. global_repo_access ON: the same _repo/ import MUST now resolve. Confirm it runs.
4. Toggle the flag on an existing install (PATCH install-local fields) and re-execute — does the change take effect, or is a stale module/context cached? (module_cache / Redis warm-on-write is a known risk area.)
5. Cross-check: with the flag OFF, can the solution still accidentally reach _repo/ via any other path (sys.path leak, a cached module from a prior execution in the same worker process)? The worker runs a long-lived template process — a prior execution's import could poison a later one.

Return structured findings. axis="global-repo-access".`,
  },
  {
    key: 'cascade-matrix',
    prompt: `${ENV}

YOUR AXIS: THE SOLUTION -> ORG -> GLOBAL CASCADE "IN BETWEEN". The user: "we haven't really identified every combination of solution -> global, and the global/org scoping in between."

Resolution order for a path::fn ref from a solution app: OWN install's workflow FIRST, then global _repo/ row, NEVER a sibling install's. Plus org-scoped vs global _repo/ rows. Enumerate and DRIVE every combination:

Set up rows that share a path::fn across sources, then resolve as different callers:
- Caller = OrgA's install. Rows present: {only own}, {own + global _repo/}, {own + a SIBLING OrgA install's row + global}, {no own, only global}, {no own, only sibling}, {nothing}. For each, what resolves? (own wins; else global; else None — NEVER sibling.)
- Caller = NO install scope (a plain _repo/ / system caller). Rows: {global only}, {global + a solution row}, {solution row only}. Expected: prefer global; only a solution row when there's no global. Confirm a _repo/ shared path is NEVER hijacked by a solution reusing the path.
- org-scoped _repo/-style rows vs global: confirm the org+global cascade still holds for non-solution entities and isn't broken by the solution filtering.
- Do this for WORKFLOWS (path::fn) and TABLES (by name — tables.py::_resolve_solution_table_by_name) and CONFIGS (by key) — they're implemented differently, so test each.

Drive via execute endpoints / form runs as the exact principal. Read api/src/repositories/workflows.py:136-151 and api/src/routers/tables.py for hypotheses only.

Return structured findings, with a clear matrix of which combinations you tested and their results. axis="cascade-matrix".`,
  },
  {
    key: 'readonly-enforcement',
    prompt: `${ENV}

YOUR AXIS: READ-ONLY ENFORCEMENT HAS NO HOLES. A solution-managed entity must be unwritable from EVERY surface except deploy.

Surfaces (api/src/routers/{workflows,tables,forms,agents,applications,app_code_files,roles}.py; api/src/services/solutions/guard.py has assert_not_solution_managed / assert_entity_id_not_solution_managed / a session-wide before_flush backstop SolutionManagedWriteError; MCP tools at api/src/services/mcp_server/tools/).

Drive these:
1. Install a solution with a workflow, table, form, agent, app. For EACH, attempt EVERY mutation via REST: update, delete, rename, patch access/roles, app draft-save, app code-file write, table schema change. Each must be refused (the locked message), NOT silently applied.
2. Table ROWS/documents are a deliberate carve-out (runtime state) — confirm row writes ARE allowed (criterion 7) while the table DEFINITION is locked. Verify both halves.
3. MCP path: via the MCP tools (or by calling the same endpoints MCP wraps), attempt to mutate a solution-managed entity. Confirm refusal. (MCP authenticates as the user directly.)
4. Secondary/indirect mutation paths: anything that updates an entity as a side effect (e.g. bulk ops, role sync, a workflow rename that repoints refs) — does it bypass the guard? Try to find a write path that reaches a solution row without hitting assert_*.
5. The before_flush backstop is GLOBAL: confirm a LEGIT non-solution write to the same entity types still works (no false positive lock-out).

Return structured findings, one per surface tested. axis="readonly-enforcement".`,
  },
  {
    key: 'ui-walkthrough',
    prompt: `${ENV}

YOUR AXIS: WEB UI CONFIDENCE. The user: "I don't feel confident in the web UI. Every first pass it's missing things, stuff isn't aligned, UX is poor."

Audit every Solutions web surface as a critical first-time user. Components: client/src/pages/Solutions.tsx, client/src/pages/SolutionDetail.tsx, client/src/components/solutions/{SolutionManagedBadge,SolutionManagedBanner}.tsx, client/src/services/solutions.ts, client/src/lib/solution-back-nav.ts. The "Show orphaned" toggle lives on Tables/Config pages.

You have two tools: (a) READ the component source for alignment/spacing/missing-state/inconsistent-pattern issues vs. the rest of the app (compare to a sibling page like a Roles or Apps list for the house style); (b) DRIVE the live UI if you can — the stack is at http://localhost:37791 (port mode). If you can use Playwright (cd client && there's a playwright setup) or curl the rendered routes, screenshot/inspect: the Solutions list, a solution detail view, the install dialog (drag zip -> preview -> scope -> config values), the edit dialog, the delete dialog (non-destructive copy), the configs tab value entry, the SolutionManagedBadge across Forms/Workflows/Fleet/Applications, the badge->owner navigation, the ?from back-nav.

For EACH surface, judge: is it aligned/consistent with the rest of the app? are loading/empty/error states handled? is anything missing (e.g. no confirm, no feedback, a dead link, an unstyled element)? is the copy clear? does the read-only affordance (badge + disabled controls) actually show on managed entities?

Be specific and harsh — the user expects this pass to find real sloppiness. Return structured findings, one per surface, kind="ux" (or "inconsistency"/"correctness"). axis="ui-walkthrough".`,
  },
  {
    key: 'lifecycle-roundtrip',
    prompt: `${ENV}

YOUR AXIS: LIFECYCLE ROUND-TRIPS as a real user would do them. Independent of pure scope — this is "does the whole flow hold together."

Drive these end to end:
1. EXPORT/IMPORT round-trip: \`bifrost export --portable <dir>\` of a solution, then \`bifrost import <dir> --org <uuid> --role-mode name\` into a different org. Does it round-trip cleanly? Are env-specific fields (org_id, oauth token ids, access_level, roles) scrubbed? Does the imported solution then INSTALL and WORK (execute a workflow, read a table)?
2. DELETE -> REATTACH with REAL data: install a solution with a table, add documents/rows to that table, DELETE the solution (uninstall). Confirm the table + its docs are ORPHANED (not destroyed) — visible via "Show orphaned". Then RE-INSTALL the same solution. Confirm the orphaned table + its data REATTACH to the new install (provenance: origin_solution_slug/id). Verify the row data survived.
3. REDEPLOY preserves data: deploy a solution with a table, seed rows, redeploy with a CHANGED schema. Rows must survive (deploy upserts the Table row, never touches Documents).
4. PROD-CONTENT COEXISTENCE: create a normal _repo/ workflow/app, then install a solution that has its own entities. Confirm they coexist — no name/path collision breaks either; the _repo/ one still resolves for normal callers, the solution one for its install.
5. INSTALL with config values (--set KEY=VALUE): confirm the values land on the install and are readable at runtime, and are instance-owned (not in the bundle/export).

Return structured findings. axis="lifecycle-roundtrip".`,
  },
]

phase('Find')
log(`Stressing ${AXES.length} Solutions risk axes in parallel...`)

const results = await parallel(
  AXES.map((a) => () =>
    agent(a.prompt, { label: `find:${a.key}`, phase: 'Find', schema: FINDING_SCHEMA })
  )
)

const axisReports = results.filter(Boolean)
const allFindings = axisReports.flatMap((r) =>
  (r.findings || []).map((f) => ({ ...f, axis: r.axis }))
)
const real = allFindings.filter((f) => f.reproduced && f.kind !== 'ok')

log(`Collected ${allFindings.length} findings (${real.length} reproduced non-OK). Running completeness critic...`)

phase('Critic')
const critic = await agent(
  `${ENV}

You are the COMPLETENESS CRITIC for a Solutions scope/cascade/read-only/UI audit. Six agents each covered one axis (scope-leak, global-repo-access, cascade-matrix, readonly-enforcement, ui-walkthrough, lifecycle-roundtrip).

Here are their coverage notes and findings (JSON):
${JSON.stringify(axisReports.map((r) => ({ axis: r.axis, coverage_note: r.coverage_note, findings: (r.findings || []).map((f) => ({ title: f.title, kind: f.kind, severity: f.severity })) })), null, 2)}

The user's specific fears: scope leak; "disable global" (global_repo_access) not actually working; untested combinations of solution->org->global; low confidence in the web UI alignment/completeness.

Your job: name the IMPORTANT combinations or surfaces that were NOT tested, ranked by how likely they are to hide a real bug given those fears. Be concrete (e.g. "no agent tested global_repo_access toggled OFF->ON mid-session with a warm worker process" or "the install dialog's config-value validation path was not driven"). Do not repeat what was covered. Output the gaps as findings with kind="info" and severity reflecting risk. axis="completeness-gaps".`,
  { label: 'critic:gaps', phase: 'Critic', schema: FINDING_SCHEMA }
)

return {
  axes: axisReports.map((r) => r.axis),
  total_findings: allFindings.length,
  reproduced_real: real.length,
  by_severity: ['critical', 'high', 'medium', 'low', 'info'].reduce((acc, s) => {
    acc[s] = allFindings.filter((f) => f.severity === s).length
    return acc
  }, {}),
  findings: allFindings,
  coverage_notes: axisReports.map((r) => ({ axis: r.axis, note: r.coverage_note })),
  completeness_gaps: critic?.findings || [],
}
