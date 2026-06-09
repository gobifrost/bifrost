export const meta = {
  name: 'solutions-qa-fanout',
  description: 'Adversarial UI/CLI QA on Solutions: 6 axis agents (own worktree + port-mode stack) drive real UI/CLI, findings independently verified, then synthesized into a ranked backlog',
  phases: [
    { title: 'Cleanup', detail: 'kill stray stacks, prune merged worktree-agent-* branches' },
    { title: 'Find', detail: 'one agent per axis; each provisions its own port-mode debug stack and drives UI+CLI' },
    { title: 'Verify', detail: 'independent agent refutes/confirms each reproduced finding' },
    { title: 'Synthesize', detail: 'dedup, rank, write the findings backlog' },
  ],
}

// One finding produced by an axis agent.
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
          surface: { type: 'string', enum: ['forms-ui', 'apps-ui', 'solutions-page', 'cli', 'mcp', 'export-import', 'other'] },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'info'] },
          did: { type: 'string', description: 'Exact steps/commands/URLs to reproduce' },
          observed: { type: 'string', description: 'What actually happened (note screenshot path for UI)' },
          expected: { type: 'string' },
          reproduced: { type: 'boolean', description: 'true only if actually run and observed' },
          code_ref: { type: 'string', description: 'file:line best guess at cause, if known' },
        },
        required: ['title', 'surface', 'severity', 'did', 'observed', 'expected', 'reproduced'],
      },
    },
    coverage_note: { type: 'string', description: 'What within the axis was tested and what was NOT reached' },
    blocked: { type: 'boolean', description: 'true if the agent could not boot a healthy stack' },
  },
  required: ['axis', 'findings', 'coverage_note'],
}

// One verifier verdict on a single finding.
const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    title: { type: 'string' },
    confirmed: { type: 'boolean', description: 'true only if the verifier reproduced it' },
    note: { type: 'string', description: 'what the verifier observed when re-running the repro' },
  },
  required: ['title', 'confirmed', 'note'],
}

// Injected into every axis agent. Each agent provisions its OWN isolated,
// port-mode debug stack (Chrome/Playwright cannot drive netbird stacks), drives
// it, and tears it down. Per-worktree Compose project name = automatic isolation.
const REPO = '/home/jack/GitHub/bifrost'
const BASE_WORKTREE = `${REPO}/.claude/worktrees/solutions-success-criteria`

const BOOTSTRAP = `
YOU PROVISION YOUR OWN STACK. Do this before any testing, and tear it down at the end.

1. Create an isolated worktree off the branch under test:
   AXIS=<your-axis-key>
   WT=/tmp/qa-$AXIS
   git -C ${BASE_WORKTREE} worktree add "$WT" HEAD
   cd "$WT"

2. Boot a PORT-MODE stack (MANDATORY — netbird stacks can't be driven by a browser).
   Export BIFROST_FORCE_PORT=1 for EVERY debug.sh call — this forces port mode even
   when the global ~/.config/bifrost/debug.env carries NETBIRD_SETUP_KEY (plain
   \`env -u NETBIRD_SETUP_KEY\` does NOT work — debug.sh re-sources that file). Do NOT
   edit the global debug.env.
   export BIFROST_FORCE_PORT=1
   ./debug.sh up
   ./debug.sh status        # the URL is the "Open: http://localhost:<port>" line — call it $URL; confirm Mode: port
   If Mode is not "port", or the api container is not healthy within ~90s, retry \`./debug.sh down && ./debug.sh up\` ONCE (BIFROST_FORCE_PORT still exported). If still unhealthy or still netbird, set blocked=true in your output and STOP (do not invent findings).

3. Install the API-matched CLI in a scratch dir OUTSIDE the repo:
   mkdir -p /tmp/qa-cli-$AXIS && cd /tmp/qa-cli-$AXIS
   python3 -m venv .venv && .venv/bin/pip install --quiet --upgrade pip
   .venv/bin/pip install --quiet "$URL/api/cli/download"
   .venv/bin/bifrost login --url "$URL" --email dev@gobifrost.com --password password
   Run the CLI FROM /tmp/qa-cli-$AXIS (its .env carries the tokens). Default creds: dev@gobifrost.com / password (superuser, MFA off).

4. Drive the UI with Playwright against $URL (headless is fine). Capture screenshots
   to /tmp/qa-$AXIS/shots/ for any UI finding and put the path in observed.

5. TEARDOWN (always, even on failure): cd "$WT" && BIFROST_FORCE_PORT=1 ./debug.sh down ; git -C ${BASE_WORKTREE} worktree remove --force "$WT"

RULES OF ENGAGEMENT:
- VERIFY EMPIRICALLY. Reading code forms hypotheses; driving the running stack concludes. A finding is REAL only if you reproduced it on your stack — include exact commands/URLs/clicks and observed-vs-expected.
- You MAY create solutions/workflows/tables/configs/forms/apps and install them to orgs to set up tests. Correctness findings matter more than tidiness.
- Do NOT modify product source. This is a drive/audit pass. Throwaway fixtures via CLI/API/UI are fine.
- If you could not boot a healthy stack, return blocked=true with an empty findings array — never fabricate.
`

const AXES = [
  {
    key: 'scope-isolation',
    prompt: `${BOOTSTRAP}

YOUR AXIS: SCOPE / CROSS-ORG ISOLATION (the #1 fear). Set AXIS=scope-isolation in the bootstrap.
Drive empirically on your own stack:
1. Create two orgs (CLI: \`bifrost orgs create\`). Install a solution (workflow + table + config) to OrgA only.
2. As a principal in OrgB (use --org on execute, or impersonate as superuser), confirm OrgB CANNOT resolve/execute OrgA's install's workflow by path, CANNOT read its table, CANNOT see its config value.
3. Forge an X-Bifrost-App header naming OrgA's app while authenticated as an OrgB user; confirm it does NOT reach OrgA's table (curl the API with the OrgB bearer token from your scratch .env).
4. Superuser scope-override: confirm pinning scope to OrgA returns OrgA's entities and to OrgB returns OrgB's — never bleeding.
5. S3 isolation: confirm OrgA's _solutions/{id}/ content is not readable as OrgB.
Return structured findings, axis="scope-isolation".`,
  },
  {
    key: 'lifecycle',
    prompt: `${BOOTSTRAP}

YOUR AXIS: INSTALL / UNINSTALL / REDEPLOY LIFECYCLE. Set AXIS=lifecycle in the bootstrap.
Drive empirically:
1. Full install round-trip (CLI \`bifrost solution install\` AND the Solutions UI page) — both must work.
2. Seed rows into an installed solution table, UNINSTALL, confirm the table+rows are ORPHANED not destroyed (orphaned_at set, solution_id NULL'd; visible via "Show orphaned" in UI). RE-INSTALL the same slug; confirm orphaned data REATTACHES (origin_solution_slug/id provenance) and row data survived.
3. Redeploy with a CHANGED table schema; rows must survive (deploy upserts the Table row, never deletes Documents).
4. Same-slug independence: two different installs of solutions sharing a workflow path each resolve their OWN workflow.
5. Install with config values (\`--set KEY=VALUE\` and via the UI install dialog); confirm values land on the install and are readable at runtime, instance-owned (not in the export bundle).
Return structured findings, axis="lifecycle".`,
  },
  {
    key: 'readonly-enforcement',
    prompt: `${BOOTSTRAP}

YOUR AXIS: READ-ONLY / MANAGED-ENTITY ENFORCEMENT. Set AXIS=readonly-enforcement in the bootstrap.
Deploy is the SOLE writer of solution-managed entities. Try to MUTATE managed forms/agents/tables/configs through EVERY surface and confirm each is refused (409 / skipped), not silently applied:
1. UI: open a solution-managed form/agent/app in the builder and try to edit/save.
2. CLI: \`bifrost forms update\` / \`agents update\` / \`tables update\` / \`configs set\` against a managed entity.
3. API: PATCH/PUT the managed entity directly with a superuser token.
4. \`/api/workflows/{id}/remap\` pointed at a managed form/agent's workflow binding.
5. MCP tools (if reachable) that mutate forms/agents/tables.
For each: is it blocked with a clear error, or does it silently corrupt managed state? A silent mutation is a high/critical finding.
Return structured findings, axis="readonly-enforcement".`,
  },
  {
    key: 'global-repo-data-fallback',
    prompt: `${BOOTSTRAP}

YOUR AXIS: GLOBAL-REPO / DATA-FALLBACK. Set AXIS=global-repo-data-fallback in the bootstrap.
CODE fallback is gated by global_repo_access; DATA fallback (tables/configs/storage) is currently UNGATED by design — your job is to produce EVIDENCE on whether that asymmetry bites.
1. Install a solution with global_repo_access=FALSE that contains a workflow which \`from modules.x import y\` references a _repo/ module. Run it; confirm the _repo/ import does NOT resolve (sealed). Flip global_repo_access=TRUE, redeploy/re-run; confirm it now DOES resolve.
2. With a SEALED install (global_repo_access=FALSE): from its workflow, read a _repo/ TABLE by name and a _repo/ CONFIG by key that the install does NOT own. Document whether the sealed install can currently reach _repo/ DATA (expected: yes, ungated). Capture whether this serves surprising/wrong data (e.g. a _repo/ table shadowing an install-intended name).
3. Toggle global_repo_access OFF->ON and ON->OFF and re-run with a warm worker; confirm the per-execution import root reflects the current flag (no stale context bleed).
Return structured findings, axis="global-repo-data-fallback". In coverage_note, state plainly whether the ungated data fallback caused any real wrong-data outcome.`,
  },
  {
    key: 'ui-ux',
    prompt: `${BOOTSTRAP}

YOUR AXIS: UI/UX CORRECTNESS (drive the BROWSER with Playwright, screenshot everything). Set AXIS=ui-ux in the bootstrap.
1. Forms: render and SUBMIT a form (incl. a solution-managed form — confirm it runs the install's OWN workflow, not a _repo/ one). Watch for 404s/empty results.
2. Standalone v2 apps: open an installed solution's app; confirm it mounts and works.
3. BifrostHeader STANDALONE: a v2 app using the SDK BifrostHeader must render STYLED outside the platform theme (this was just fixed to use inline styles). Screenshot it; confirm it is NOT unstyled (has borders/spacing/colors, hover works).
4. Solutions install/manage page: install dialog, config-schema prompts (incl. secret-typed fields masked), "Show orphaned", uninstall confirm.
5. The Solutions page itself: list/preview/entity-summary chips render correctly.
Return structured findings, axis="ui-ux". Put screenshot paths in observed.`,
  },
  {
    key: 'cli-docs-literalism',
    prompt: `${BOOTSTRAP}

YOUR AXIS: CLI / DOCS-LITERALISM. Set AXIS=cli-docs-literalism in the bootstrap.
Be the user who copies the published docs VERBATIM and a user copying STALE patterns.
1. Follow CLAUDE.md's "Spinning up / connecting" and docs/llm.txt solution recipes exactly; note any step that doesn't work as written.
2. \`bifrost solution init\` -> \`scaffold-app\` -> \`deploy\` first-run path; confirm the scaffold's sample workflow actually deploys+runs (a prior bug dropped functions/).
3. \`bifrost watch\` INSIDE a solution workspace must REFUSE with the message pointing at \`bifrost solution start\` (just landed). Confirm. Then \`bifrost solution start\` local dev: app + local workflows behind one origin.
4. \`bifrost export --portable <dir>\` then \`bifrost import <dir> --org <uuid> --role-mode name\` into another org: clean round-trip? env-specific fields scrubbed? imported solution then INSTALLS and WORKS?
5. Try a stale pattern a user might copy from old docs (e.g. an old import flag, a removed subcommand) — does it fail with a helpful error or a confusing traceback?
Return structured findings, axis="cli-docs-literalism".`,
  },
]

const CLEANUP_PROMPT = `
You are the PRE-FLIGHT CLEANUP for a QA fan-out. Do EXACTLY these, then report what you freed. Be careful and conservative — when unsure, KEEP.

1. KILL STRAY STACKS. List running bifrost stacks:
   docker ps --format '{{.Names}}' | grep -E 'bifrost-(debug|test)' | sed -E 's/-(api|client|worker|scheduler|postgres|redis|rabbitmq|seaweedfs|pgbouncer|init)-[0-9]+$//' | sort -u
   For every project EXCEPT the one for ${BASE_WORKTREE} (its debug stack — KEEP it), tear it down with: \`docker compose -p <project> down\` (or stop its containers). Do NOT remove volumes you are unsure about; stopping is enough to free RAM.

2. PRUNE MERGED AGENT WORKTREES. For each branch matching 'worktree-agent-*' that is MERGED into main:
   git -C ${REPO} branch --merged main | grep -E 'worktree-agent-' | while read b; do
     wt=$(git -C ${REPO} worktree list | grep "\\[$b\\]" | awk '{print $1}')
     [ -n "$wt" ] && git -C ${REPO} worktree remove --force "$wt"
     git -C ${REPO} branch -d "$b"
   done
   NEVER touch named feature worktrees (entity-access, bridge-cse, pr-288-review, bifrost-plugin-testing, solutions-success-criteria, etc.). ONLY worktree-agent-* that are merged.

3. REPORT: RAM freed (free -m before/after), worktrees removed, branches deleted, what you KEPT and why. Return this as your text.
`

// Cap concurrency at 6 stacks regardless of core count (each stack ~2.2GB).
async function throttle(thunks, limit) {
  const out = []; const running = new Set()
  for (const t of thunks) {
    const p = Promise.resolve().then(t); out.push(p)
    running.add(p); p.finally(() => running.delete(p))
    if (running.size >= limit) await Promise.race(running)
  }
  return Promise.all(out)
}

phase('Cleanup')
log('Pre-flight: killing stray stacks + pruning merged agent worktrees...')
const cleanupReport = await agent(CLEANUP_PROMPT, { label: 'cleanup', phase: 'Cleanup' })
log('Cleanup done.')

// PHASE 2 — FIND: 6 axes in parallel, each on its own port-mode stack.
phase('Find')
log(`Fanning out ${AXES.length} axis agents (each provisions its own port-mode stack)...`)
const axisReports = (await throttle(
  AXES.map((a) => () =>
    agent(a.prompt, { label: `find:${a.key}`, phase: 'Find', schema: FINDING_SCHEMA })
  ),
  6
)).filter(Boolean)

const blocked = axisReports.filter((r) => r.blocked)
const allFindings = axisReports.flatMap((r) =>
  (r.findings || []).map((f) => ({ ...f, axis: r.axis }))
)
const toVerify = allFindings.filter((f) => f.reproduced && f.severity !== 'info')
log(`Collected ${allFindings.length} findings; ${toVerify.length} reproduced non-info to verify. ${blocked.length} axes blocked on stack boot.`)

// PHASE 3 — VERIFY: independent refutation per finding. Each verifier provisions
// its own stack via the same bootstrap, so cap concurrency at 6 stacks (RAM ceiling)
// rather than letting all reproduced findings boot stacks at once.
phase('Verify')
const verdicts = await throttle(
  toVerify.map((f) => () => agent(
    `${BOOTSTRAP}

You are an INDEPENDENT VERIFIER. Set AXIS=verify-${(f.axis || 'x').slice(0,12)} in the bootstrap. Provision your own port-mode stack.
A prior agent reported this finding on the ${f.axis} axis. Your job is to REFUTE it: reproduce the EXACT steps and report whether it actually manifests. Default to confirmed=false if you cannot reproduce it.

FINDING TITLE: ${f.title}
SURFACE: ${f.surface}
STEPS THEY TOOK (did): ${f.did}
THEY OBSERVED: ${f.observed}
THEY EXPECTED: ${f.expected}

Run those steps on YOUR fresh stack. Set confirmed=true ONLY if you see the same wrong behavior; otherwise confirmed=false and explain what you saw instead. Tear your stack down.`,
    { label: `verify:${(f.title || '').slice(0, 32)}`, phase: 'Verify', schema: VERDICT_SCHEMA }
  ).then((v) => ({ finding: f, verdict: v })).catch(() => null)),
  6
)
const checked = verdicts.filter(Boolean)
const confirmed = checked.filter((c) => c.verdict?.confirmed).map((c) => ({ ...c.finding, verify_note: c.verdict.note }))
const refuted = checked.filter((c) => !c.verdict?.confirmed).map((c) => ({ title: c.finding.title, axis: c.finding.axis, why: c.verdict?.note }))
log(`Verify: ${confirmed.length} confirmed, ${refuted.length} refuted.`)

// PHASE 4 — SYNTHESIZE: dedup, rank, write the backlog doc.
phase('Synthesize')
const synthDoc = await agent(
  `You are the SYNTHESIS agent for a Solutions adversarial QA fan-out. Produce the final findings backlog and WRITE it to ${BASE_WORKTREE}/docs/plans/2026-06-09-solutions-qa-fanout-findings.md (use your Write tool).

CONFIRMED findings (already independently verified — these are REAL), JSON:
${JSON.stringify(confirmed, null, 2)}

REFUTED (a verifier could not reproduce — list these separately so they are not re-investigated), JSON:
${JSON.stringify(refuted, null, 2)}

BLOCKED axes (could not boot a stack — note for re-run), JSON:
${JSON.stringify(blocked.map((b) => ({ axis: b.axis, note: b.coverage_note })), null, 2)}

COVERAGE NOTES per axis, JSON:
${JSON.stringify(axisReports.map((r) => ({ axis: r.axis, note: r.coverage_note })), null, 2)}

The doc must have: a STATUS line (counts by severity of CONFIRMED only); a CONFIRMED section grouped by severity (critical first), each item with did/observed/expected/code_ref and a one-line proposed fix shape; a REFUTED section (title + why); a DATA-FALLBACK VERDICT section (pull from the global-repo-data-fallback axis coverage_note: did the ungated data fallback actually bite? this decides whether the deferred gate becomes a real follow-up); a COVERAGE/GAPS section; and a BLOCKED section if any. Return a 5-line summary as your text.`,
  { label: 'synthesize', phase: 'Synthesize' }
)

return {
  axes: axisReports.map((r) => r.axis),
  blocked_axes: blocked.map((r) => r.axis),
  total_findings: allFindings.length,
  confirmed: confirmed.length,
  refuted: refuted.length,
  by_severity_confirmed: ['critical', 'high', 'medium', 'low'].reduce((acc, s) => {
    acc[s] = confirmed.filter((f) => f.severity === s).length
    return acc
  }, {}),
  cleanup: cleanupReport,
  synthesis: synthDoc,
  findings_doc: `${BASE_WORKTREE}/docs/plans/2026-06-09-solutions-qa-fanout-findings.md`,
}
