# Agent review and regression testing loop

Status: user authorized replacing tuning in this session on 2026-09-19.
The existing durable-platform implementation is locally verified. This document
describes the next iteration; it does not claim these additions are implemented.

## Product goal

Make investigation, findings, regression tests, and isolated Agent improvement
one platform workflow. Findings can originate from a person, a run, a failed
test, an external review, or a future automated reviewer. The current custom
review skill must not define the platform's data model or permissions.

The core loop is:

Finding → reviewed test → saved suite → isolated Agent changes → selected
provider profiles → comparison → reviewed production change.

## Session delivery: replace tuning

The existing tuning entrypoint becomes the single improvement workbench. Reuse
its proposal generation, discussions, editing, diff review, and prompt history.
Replace the predictive dry-run journey with actual synthetic evaluation, and
consolidate the new Studio features into this workbench. Do not ship two parallel
tuning/testing workbenches. Preserve the existing Review page's human-review
workflow and historical conversation access.

Organize the workbench around Evidence, Tests, and Changes & Results using the
current design system. Evidence can be a flagged run, successful run, or a
manually recorded finding. Findings may be dismissed without creating a test;
tests may exist without a finding. Selected context must survive navigation.

Applying a change must preserve original verdicts, findings, and source evidence.
The existing consolidated tuning apply operation clears flagged verdicts; do
not carry that behavior into the replacement. Retain change history and detect
changes to live configuration between diff review and apply.

Automated reviewers and recurring/change-triggered test scheduling are a later
phase. The saved definitions and provenance must support those callers, but
this delivery does not quietly enable background discovery or fresh test runs.

## Remove, change, retain, add

| Action | Scope | Reason |
| --- | --- | --- |
| Remove | Standalone browser debugger and its duplicate timeline | One place to understand a run. |
| Change | Existing Activity/Advanced surfaces and evaluation evidence links | Add useful durable evidence without another investigation workflow. |
| Retain | Existing run APIs, journal/snapshot/checkpoint endpoints, durable execution and authorization | These are useful backend contracts, independent of the browser route. |
| Retain | Current run actions, live activity, tool results, usage, and execution links | Avoid losing existing inspection capabilities. |
| Add | First-class findings with source evidence and linked regression cases | Support multiple discovery sources and an auditable path to a fix. |
| Change | Test authoring | Use scenario and expectation controls; keep JSON available for advanced work. |
| Add | Saved suite execution across selected Agent configurations and provider profiles | Make on-demand and future automated testing use the same definition. |
| Defer by default | Automated discovery and recurring/change-triggered evaluation | Separate policy and cost decisions; the user has been asked whether discovery belongs in this pass. |

## Existing run experience

Waiting tools and delegated work remain part of live Activity. A deliberate
durable pause should say "Waiting until [time]" with its reason. It means the
platform saved the run and released its worker until the requested time; it is
not a recurring schedule or a new task.

Checkpoints, execution attempts, frozen configuration, worker lease details,
and completion-delivery errors belong in Advanced inspection. Ordinary run
details should not display empty infrastructure fields. Evaluation links must
retain run and journal-sequence identity and open the existing run detail.
Historical runs remain readable when durable evidence is unavailable.

## Findings

A finding records the observed problem, expected behavior, source, relevant
authorized evidence, and links to tests intended to reproduce it. Its identity
is separate from a run or test: a manually reported problem may have no run,
and several tests may address one finding.

Evidence access follows existing tenant and resource authorization. Linking an
external source does not authorize fetching it or granting access to its data.
Generated drafts remain distinguishable from reviewed findings and accepted
tests. A passing test is evidence about a finding, not automatic proof that the
finding is resolved.

The repository currently has no general persisted Agent finding model/API.
Case provenance currently identifies manual/generated/historical inspiration
and selected historical run IDs. Provenance alone is insufficient to claim a
mature platform review feature.

## Testing and iteration

A user can create a draft from a finding, review the invocation, mocked tool
behavior and expectations, and accept a frozen case into a suite. Instructions,
tool attachments and other candidate settings are isolated from the live Agent.
Actual tool implementation changes require separate integration coverage;
synthetic results alone cannot validate a vendor connection or workflow code.

A saved test definition selects the suite, Agent configurations, provider/model
profiles, repetitions, and required results. Each execution freezes the selected
versions. The baseline/profile pairing must be explicit so a prompt change is
not silently confounded with a model change. Results identify every selected
combination, including incomplete, cancelled, or failed combinations.

Accepted expectations do not change as an Agent is tuned. Editing expectations
is a deliberate new case version. Expected behavior remains independent from
the proposed Agent's output. Semantic scoring must identify its judge and
settings rather than silently changing with the profile under test.

Current execution supports one suite and an optional candidate comparison;
candidate overlays already support a model profile. A loop of separate API
calls is not yet a saved, coherent multi-profile execution feature.

## Automation boundaries

On-demand and future automated runs must invoke the same saved definition and
durable execution service. Continue using PlatformJob and shared notifications;
do not introduce a feature worker, job-status table, or polling transport.

Synthetic tool calls remain isolated from real integration actions. Model calls
still incur usage. Concurrency limits and post-run cost assertions already exist;
they must not be described as a pre-execution hard spending cap. Automated runs
need an explicit spending/admission policy, overlap policy, owner, cancellation,
and result notification before being enabled.

Existing reconciliation repairs already-requested work. It does not authorize
fresh test campaigns. Automatic discovery should produce reviewable findings;
it must not silently accept tests or change a production Agent. No automatic
promotion is included. Normal authorized Agent-update APIs remain available;
the UI review step is not a universal approval enforcement mechanism.

## Execution and review

OpenCode Muse is the implementation executor, one bounded file-owned slice at
a time. The primary assistant owns design decisions and final code/UI review.
Do not repeat the completed backend audit or broad suites without a changed
boundary or new failure that justifies them.

Sequence: consolidate run inspection; establish finding-to-case contracts;
extend saved multi-profile execution; improve authoring and comparison UI;
review actual rendered journeys and focused regression evidence. Each slice
needs a concrete implementation plan and ownership before delegation.

OpenCode reconnaissance is retained at `/tmp/agent-review-testing-scope.md`.
It is advisory: backend `debug_links` are API URLs and must be retained;
shared `PlatformEvidence` components must not be deleted while Studio uses them.
