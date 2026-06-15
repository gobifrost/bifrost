# Build-Skill Validation Log

Empirical validation of the rebuilt `bifrost:build` skill (Tasks 11–12). Fresh
Sonnet subagents build real artifacts against the debug stack
(`http://localhost:37791`, port mode) following ONLY the skill. Done bar per
track: **3 consecutive clean runs with no skill-doc edits between them.** Any
misleading-moment fix resets the streak to 0.

## SDK-surface coverage target
- Python SDK: 71 public methods (`generated/python-sdk-signatures.md`)
- Web SDK: 22 exports (`generated/web-sdk-surface.md`)
- The union of Track A + Track B must exercise the surface; gaps logged with a reason.

---

## Track A — Solution build (read-only invariant in force)

Goal: `solution init` → scaffold a Tailwind-styled app → get an agent + table +
form/config into the solution → `solution start` + drive → update an entity →
`solution deploy`. Pin down the entities-into-a-solution open question.

| Run | Result | Styled | Entities | Update | Deploy | Invariant | Misleading moments → fix | Streak |
|-----|--------|--------|----------|--------|--------|-----------|--------------------------|--------|
| A1 | PARTIAL | Tailwind configured, sample uses inline styles | workflows round-trip; table/form/agent/config **captured then DELETED by next deploy** | yes (workflow) | workflows+app clean; **captured entities destroyed** | ✓ 409 on solution-managed update | see below — **blocked on platform bug** | 0 |

### A1 — pivotal finding (verified at code level)

**The entities-into-a-solution mechanism is broken at the PLATFORM level, not the skill level.**
- `bifrost solution capture` is a pure server call (`POST /api/solutions/{id}/capture`, `commands/solution.py:1581+`) — it sets `solution_id`/`is_solution_managed` on the DB record but does **NOT** write `.bifrost/{tables,forms,agents}.yaml`.
- `bifrost solution deploy` is manifest-driven full-replace. So the next deploy **deletes** any captured table/form/agent that isn't in the on-disk manifest. Reproduced twice with a table; confirmed in source.
- **Workflows are the only entity that round-trips** — and only because you manually add a UUID-keyed entry to `.bifrost/workflows.yaml` (deploy does not auto-scan `functions/`).

**Consequence:** the skill cannot be edited into "consistently produces a good solution *with entities*" because no working capture→deploy round-trip exists for table/form/agent/config. This is a release-blocker-class platform gap, escalated to the user (scope decision).

**Genuine skill-doc findings (fixable independent of the bug):** capture requires entities be in the SAME org as the install (global `organization_id: null` not capturable) — undocumented; `solution start [APP_SLUG]` positional needed with multiple apps; capture-by-UUID more reliable than by-name; adding a 2nd workflow needs a manual `.bifrost/workflows.yaml` UUID entry (contradicts the "don't edit .bifrost" guidance — needs reconciling).

**Status: Track A BLOCKED pending user decision on the platform bug.**

---

## Track B — Repo/global build (live mutation correct)

Goal: author workflow `.py` + entities via live CLI create/update → execute →
iterate. Cover SDK surface Track A didn't reach.

| Run | Result | UI/exec | Entities | Update | Execute | Invariant | Misleading moments → fix | Streak |
|-----|--------|---------|----------|--------|---------|-----------|--------------------------|--------|
| _pending_ | | | | | | | | |
