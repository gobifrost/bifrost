# Agent Workbench Production Design

**Status:** Approved direction; written implementation specification pending user review.

**Visual authority:** The accepted interactive prototype in
`client/public/mockups/agent-test-explorer/`, especially its contained
collection-and-inspector workspace. Production must be compared directly with
the prototype rather than treating it as loose inspiration.

## Job and audience

An MSP operator uses Agent Workbench to turn observed agent behavior into
durable, testable improvement work. The operator may arrive from the agent
fleet, an individual agent, a run, a scheduled review, or a finding. The
workspace must make the next useful step clear without requiring the operator
to understand suites, candidates, execution matrices, or other backend storage
concepts.

The durable loop is:

```text
Runs -> Reviews -> Findings -> Tests -> Draft Changes -> Evidence
```

Findings are the fleet-level inbox. Tests are the primary working collection
inside an individual agent. Reviews are saved, plain-English statements that
inspect completed runs and produce findings. Human run review is lightweight
triage and is distinct from these automated Reviews.

## Product language

`Quality` is not a product term for this surface.

- Fleet destination and global page name: `Agent Workbench`
- Individual-agent page name: `Workbench`
- Agent action: `Open Workbench`
- Collections: `Findings`, `Tests`, `Reviews`, `Run History`
- Human verdict activity: `Run Review`
- Proposed remediation: `Draft Changes`
- Primary actions use Title Case, including `Create Finding`, `Create Test`,
  `Evaluate Recorded Runs`, `Run Simulation`, `Review Changes`, and
  `Apply Changes`.

Descriptions, evidence, helper text, and other prose remain sentence case.
Internal implementation names may retain `quality` temporarily while the
production migration is in progress, but user-visible copy must not expose it.

## One workspace at two scopes

One reusable Agent Workbench component owns collection navigation, filters,
search, selection, execution context, and the attached inspector.

- `/agents/quality` remains a compatibility route but renders fleet-scoped
  Agent Workbench and defaults to `Findings`.
- `/agents/:id/quality` remains a compatibility route but renders the same
  component scoped to that agent and defaults to `Tests`.
- Existing deep links continue to resolve while visible labels and generated
  links use Workbench terminology.
- Fleet scope supports cross-agent Findings directly. Tests and Reviews show
  agent identity and use the same collection grammar rather than forwarding to
  a visually unrelated page.
- Agent scope removes the redundant agent filter while preserving a clear
  breadcrumb back to Agents or the originating agent.

The top-level Agents page uses `Agent Workbench` for the fleet destination.
Agent Detail uses `Open Workbench`. Back navigation belongs in the breadcrumb
or standard workspace header, never as an isolated top-right button.

## Workspace anatomy

Production follows the accepted prototype and the existing Files, Tables, and
Knowledge workspace grammar:

1. A standard page breadcrumb establishes fleet or agent context.
2. One contained workspace owns its header, navigation, collection, inspector,
   and bounded scrolling.
3. A persistent navigation rail contains `Tests`, `Reviews`, `Findings`, and
   `Run History`. Test status filters such as `All Tests`, `Failing`, and
   `Not Run` remain grouped under Tests.
4. A collection toolbar contains search, filters, selection context, execution
   mode, profile selection, and the collection's primary action.
5. Full-width rows show identity first, then concise status, provenance,
   profile, time, duration, and cost when relevant.
6. Selecting a row attaches an inspector to the same workspace. The collection
   remains visible on wide screens.
7. The inspector owns item details, editing, investigation, results, and
   contextual next actions. It does not create a separate workflow route.

Use the established `WorkspaceHeader`, standard navigation selection classes,
standard Tabs where tabs are semantically appropriate, full-row selected
treatment, `WorkspacePrimaryAction`, design tokens, and shared loading/error
components. Ordinary workspace structure uses tonal surfaces and hairline
dividers rather than nested cards and decorative elevation.

## Collections and flows

### Findings

Findings are problems or opportunities worth investigating. The collection
shows title, agent, kind, status, source Review or run, occurrence/evidence
summary, and most recent observation. Opening a Finding attaches an inspector
with Markdown evidence, source runs, linked Tests, relevant recent failures,
and provenance.

Actions are `Investigate`, `Create Test`, `Draft Changes`, and `Dismiss`.
Investigation preserves the selected source runs and linked Tests. Drafts,
candidate comparisons, simulations, and proposed tools are artifacts attached
to this investigation; they are not a second inbox.

Repeated scheduled Reviews should eventually consolidate logically equivalent
observations under the same durable Finding. The current backend does not yet
provide that identity contract. The UI must not invent occurrence counts or
pretend duplicate Finding rows are consolidated.

### Tests

Tests are readable should/should-not behaviors. The toolbar owns search,
selection, `Run Simulation` versus `Evaluate Recorded Runs`, profile selection,
`Add Test`, and the primary run action. Rows show the latest result, mode,
profile, duration, cost, and time without requiring suite setup.

The inspector handles result inspection and test editing. Test creation starts
with Situation and Expected Behavior. Simulated Environment remains an
advanced disclosure. Definition changes invalidate the displayed latest result
until a compatible new result exists.

### Reviews

Reviews are saved plain-English statements checked against completed runs.
Rows show name, agent, manual or scheduled cadence, last run, number of runs
reviewed, and linked Finding count. The inspector owns the statement, run
scope, evidence-format instructions, scheduling controls, latest run status,
and linked Findings.

The interface must not conflate Reviews with human thumbs-up/thumbs-down run
triage. User-facing copy should use `Run Review` when referring to the latter.

### Run History

Run History contains Workbench operations and distinguishes `Simulation`,
`Recorded Evaluation`, `Review`, and `Draft Comparison`. Execution lifecycle
states remain separate from test verdicts. In-progress, cancelled, failed, and
partial operations never appear as passing evidence.

## Human run triage and tuning retirement

The existing thumbs-up/thumbs-down verdict and optional note remain as a quick
human annotation on a run. A negative verdict exposes a prominent prefilled
`Create Finding` action. If a Finding is already linked, the surface shows
`Open Finding` instead. Creating a Finding is explicit so casual triage cannot
silently flood the durable inbox.

The `Discussion` tab and `Improvement Conversation` are legacy tuning surfaces
and are removed from active run review. Existing historical data remains
preserved and accessible only where retention requires it; it is not presented
as the path to improve an agent. No new tuning recommendation flow is added.

## Interaction states

Every interactive row provides default, hover, focus-visible, pressed, and
persistent selected states. The row itself is the primary target; a small
`Open` button is not required. Nested controls stop propagation and retain
their own accessible names. Selection is communicated with shape, border, or
position as well as color.

The workspace defines loading, cached-read warning, empty, permission denied,
error with retry, partially complete, cancelled, stale-definition, and deleted
source states. Primary mutations prevent duplicate submission and keep the
operator's context after failure. Long names, Markdown, evidence, and IDs wrap
or disclose deliberately without hiding task-critical content.

On narrow screens, the inspector replaces the collection inside the workspace.
Closing it returns to the same collection, filters, scroll position, and
selection. Toolbars wrap by priority; the primary action stays discoverable.
Touch targets and keyboard focus follow the existing Bifrost accessibility
contract.

## Data and accounting boundaries

The Workbench consumes existing authorized Agent, Finding, Test, Review,
recorded-evaluation, simulation, candidate, and PlatformJob contracts. It does
not add browser polling or a feature-specific job lifecycle.

`AIUsage` remains the sole monetary and token ledger. The inspector presents a
compact operation total and expandable attribution. Original source-run cost
is context, not newly incurred Workbench spend. Missing usage or cost remains
explicitly unknown and is never rendered as zero. The UI must not duplicate an
attempt or operation's accounting.

## Scope and anti-goals

This production correction includes the shared workspace, fleet and agent
scope, route/navigation integration, collection and inspector interactions,
legacy tuning-surface retirement, real contract wiring, and focused component
and browser coverage.

It does not implement a model-driven tuning wizard, real tool implementation,
automatic application of changes, a separate candidate inbox, tuning
retirement beyond the surfaces replaced here, new backend lifecycle systems,
or framework-wide redesigns.

The logical Finding-consolidation contract and durable experiment-to-Finding
linkage remain backend gaps. They require explicit backend design rather than
fabricated client behavior.

## Verification and fidelity gate

Implementation is not accepted on tests alone. The final candidate must be
rendered and compared with the accepted prototype at wide and narrow
viewports, in light and dark themes. The fidelity ledger must cover workspace
anatomy, navigation, toolbar composition, row density and selection,
inspector attachment, typography, casing, empty/loading/error states, and
mobile pane replacement.

Focused verification must include component tests for navigation and
selection state, run-triage-to-Finding behavior, legacy tuning-surface removal,
route compatibility, mutation recovery, and accounting display of unknown
values. A Playwright happy path must drive Finding -> Investigate -> Create
Test -> Run or evaluate -> inspect evidence without paid provider calls.
Existing seeded rows and mocked provider behavior supply the data.

The implementation must also pass TypeScript type checking, scoped linting,
the applicable focused client tests, and the mechanical design detector.
Broader suites are reported separately rather than implied.
