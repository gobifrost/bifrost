# Tuning replacement: review acceptance

Status: implementation under review; not approved for release.

The user requires the existing Bifrost design system and familiar testing
patterns. OpenCode implements the bounded corrections; the primary assistant
owns design decisions and the final code and rendered UI review.

## Backend review

The first implementation review found four blockers: selected profiles retained
the original frozen model configuration; reused executions disappeared from new
matrix reads; reviewed applies had a check/write race; shared Agent findings
crossed tenant boundaries. The correction handoff reports 140 unit tests and 28
API tests passed. The primary independently inspected the full profile override,
Agent row lock, and finding tenant/source guards, and confirmed the saved API
JUnit report contains 28 tests with no failures, errors, or skips.

The second review requires three additional corrections before acceptance:

- Reused batch cells must pass the same reference authorization as new cells.
- The membership migration must populate existing matrix memberships.
- Reuse flags must map by execution identity rather than zip unsorted inputs
  with sorted results.

## UI acceptance

Principle: Consistency and design systems. Reuse Bifrost's page, tab, list/table,
form, selector, status, and prompt-diff components. Do not introduce an alternate
visual system for testing.

Principle: Content and information architecture. The main results surface must
answer what ran, what passed, and what needs attention. Use suite, candidate,
profile, and model names. Keep hashes, raw configuration, and infrastructure
details in Advanced. Show a failed test's expectation and observed outcome or
reason before usage telemetry.

Principle: Forms, error prevention, and recovery. Candidate tool selection uses
named controls; JSON is optional Advanced input. Finding-to-test authoring keeps
the finding and its expected behavior visible without silently accepting test
expectations. Saving or cancelling allows the finding context to be cleared.

Principle: Interactions, feedback, and system status. Distinguish a completed
execution from passing tests. Preserve accurate queued, running, failed,
cancelled, and incomplete states. Displayed saved results must not acquire the
labels of a newly selected candidate, suite, or profile. Applying changes stays
explicit, with a readable production diff and stale-change protection.

Principle: Layout, spacing, and responsive behavior. Review actual desktop and
390px mobile views, with long names and content. Do not force short content into
full-height scrolling panels. Capture the actual apply diff, not a screenshot
above it or after it disappears.

## Verification boundary

Run focused backend regressions for the second review, relevant component tests,
forced TypeScript checking, lint, and the live improvement journey. Preserve
desktop/mobile evidence for empty, running, failed, completed, and apply-review
states. Identify mocked state captures separately from live-service evidence.
Do not infer accessibility or behavioral correctness from screenshots alone.

Fleetwide Findings/Tests navigation, automated discovery, and scheduled test
campaigns remain deferred. These corrections should help a user turn evidence
into a test, compare candidate behavior, and understand a production change
without needing to understand the runtime implementation.
