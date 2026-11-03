# Pilot acceptance checklist

Run the sample before supplying a live cookie. Report the installed commit,
Bifrost version, selected mode, failing step, and a redacted error. Do not attach
cookies, raw exports, variables, or customer identifiers to a public issue.

## Sample acceptance

- Install into a fresh private test organization. Confirm Configuration starts
  with no session value or source organization from another installation.
- An administrator can open the app; an ordinary user cannot invoke the
  workflows or retrieve its managed files by guessing a migration ID.
- Create a sample migration and export. The result stays labeled sample and
  contains only synthetic records. No Rewst connection is required.
- Inspect a source artifact, inventory relationships, and the coverage register.
  Unsupported classes remain explicit.
- Save a plan, reload the app, and retrieve the same plan through the handoff
  command. Move an item through planning and implementation; record test
  evidence. Reload and verify the same evidence remains.
- Open the plan in two sessions, save one, then save the stale version. The
  stale save must fail without discarding the unsaved text or overwriting the
  newer plan. Refresh context and reconcile manually.
- Start a second migration. Confirm its plan, artifacts, and item statuses do
  not inherit the first migration's progress.
- Open the app at phone width. Setup, inventory, plan, and handoff remain usable
  with keyboard navigation and readable loading/error states.

## Live Rewst acceptance — requires a current customer account

- Confirm authorized source scope and export retention before entry. Use a
  test/small source organization first. Export is read-only; do not run source
  workflows to create test evidence.
- Observe the current GraphQL endpoint and cookie name in the browser. Supply
  the session only through the installed Solution's secret Configuration field.
- Validate the connection. Confirm the reported organizations correspond to the
  configured root and its authorized descendants. An inaccessible root must
  fail validation.
- Export explicitly selected organizations. Check a known workflow, a nested
  workflow, a disabled trigger, a form with a dynamic option generator, a
  template/script, an inherited variable, and integration overrides against the
  source UI. Record missing fields and unresolved dependencies as coverage gaps.
- Check list pagination against source counts. Empty or partial responses must
  not be treated as proof that the environment contains no resources.
- Try an expired session. Confirm a sanitized authentication failure, no cookie
  in app/execution output, and no continued credential use after rejection.
- If the private schema differs, record the operation name and sanitized shape
  discrepancy. Share only a reviewed, credential-free read query if needed.
- Give Codex or Claude the handoff. Start with one low-impact feature, define
  its input/output and side-effect contract, implement it in a sandbox, and
  attach passing behavioral evidence. Do not infer parity from a successful
  Python execution alone.

## Pilot boundaries

Passing the sample checks is an offline package demonstration. Passing a live
export checks that capture against that account and schema; it is not a universal
Rewst backup certification. Migration completeness requires a disposition for
every scoped resource and verified replacements for every required behavior.

The pilot deliberately keeps one active executor. Revisions prevent stale
updates, but they do not coordinate simultaneous coding agents or grant
production change authority. Production cutover requires a separately approved
ownership, observation, reconciliation, and rollback plan.
