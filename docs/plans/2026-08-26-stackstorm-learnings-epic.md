# StackStorm Learnings Epic

Date: 2026-08-26
Status: proposed; Milestone 1 recommended for approval
Research: [StackStorm Learnings for Bifrost](../research/stackstorm-for-bifrost.md)

## Outcome

Make Bifrost's event-driven automation model behave like an intentional rules
system and expose its causal history, while retaining Bifrost as the sole
authority for identity, definitions, authorization, execution, persistence,
packaging, and operations.

This epic does not install StackStorm or add compatibility with its APIs or pack
format. It integrates the lessons that improve Bifrost's own architecture.

## Approval shape

Approve this as a gated sequence, not a blanket commitment to nine issues:

- **Gate A — preflight:** inventory deployed `filter_expression` data and name
  at least one conditional-routing use case. Stop if migration requires an
  open-ended expression runtime or there is no consumer.
- **Gate B — rules pilot:** Issues 1–2 prove safe criteria and durable decisions
  through API/E2E tests. Stop or correct course if authorization, execution
  semantics, or ingestion performance regress.
- **Gate C — usable product:** Issue 3 proves authors can configure and operators
  can diagnose the rules in the rendered product. This completes the minimum
  independently valuable slice.
- **Gate D — expansion:** approve Issues 4–9 separately from observed operator,
  Solution, producer, and policy needs. No later issue is justified merely by
  architectural symmetry with StackStorm.

The operator-facing rationale, relative value, preconditions, and stop
conditions are in the linked research document.

## Invariants

- Existing source, organization, Solution, workflow, and agent resolution rules
  remain authoritative.
- Workflow source pinning and isolated execution are unchanged.
- PlatformJob remains canonical for durable non-workflow work.
- The execution-policy work from the Celery-learnings epic is reused, not
  duplicated.
- Rule matching is deterministic, bounded, side-effect free, and fail-closed.
- Event payloads and secrets are not copied into audit or decision metadata.
- No arbitrary expression language or executable template is introduced.
- Existing subscriptions without criteria remain unconditional.

## Milestone 1 — First-class event rules

### Issue 1: Define the criteria contract

Replace the misleading inert `filter_expression` contract with structured,
versioned criteria data. Define:

- a bounded field path rooted in the normalized event envelope;
- allow-listed operators for equality, inequality, membership, existence,
  string prefix/suffix/contains, and numeric comparison;
- `all`, `any`, and `not` composition with depth and node limits;
- write-time validation shared by REST, manifest import, and Solution deploy;
- an explicit migration decision for existing non-null `filter_expression`
  values based on production data, rather than silently interpreting strings.

Acceptance:

- malformed paths, unknown operators, incompatible value types, excessive
  depth, and excessive node counts are rejected deterministically;
- criteria serialize identically through repo manifests and Solutions;
- API-generated schemas describe the full contract;
- DTO parity and contract-version tripwires pass.

### Issue 2: Evaluate and record rule decisions

Evaluate criteria once per Event/Subscription pair before target queueing.
Create an EventDelivery for both outcomes:

- match: `pending`, then normal queueing;
- no match: terminal `skipped` with safe structured decision evidence.

The evidence records criteria version, outcome, and bounded diagnostic codes,
not copied payload values. Evaluation errors fail closed and remain distinct
from ordinary non-matches.

Acceptance:

- webhook, schedule, and topic paths call the same evaluator;
- a matching subscription queues exactly once;
- a non-match never creates an Execution or AgentRun;
- errors never queue a target and are visible to operators;
- event aggregate status/counts include skipped decisions correctly;
- replay/retry cannot turn a recorded non-match into an accidental execution;
- unit, repository, router, and E2E tests cover match, non-match, error, and
  duplicate/retry behavior.

### Issue 3: Authoring and operator surfaces

Expose structured criteria in the event subscription UI and thin CLI/MCP/REST
surfaces. Provide an operator-readable decision display on event deliveries.

Acceptance:

- authors can build nested criteria without writing an expression string;
- field and operator validation is visible before submission;
- event details distinguish matched, skipped, failed-to-evaluate, queued,
  successful, and failed targets;
- accessibility, light/dark theme, narrow viewport, loading, empty, and error
  states are rendered and inspected;
- generated client types are refreshed rather than hand-written.

## Milestone 2 — Causal automation history

### Issue 4: Canonical automation trace projection

Add one read projection that joins source, event, subscription/rule decision,
delivery, and workflow Execution or AgentRun without creating a new state
authority.

Acceptance:

- one event trace shows every target and terminal outcome;
- links preserve existing authorization and tenant visibility;
- missing/deleted targets render as historical tombstones rather than breaking
  the trace;
- pagination avoids loading unbounded payloads or execution results;
- payload bodies remain behind their existing privileged detail endpoints;
- query plans and indexes are verified for representative history volume.

### Issue 5: Trace UI and diagnostics

Make the projection the canonical drill-down from event history and link back
from executions/agent runs when an event delivery exists.

Acceptance:

- an operator can answer “what event caused this, which rule matched, what ran,
  and where did it fail?” from one navigation path;
- retry attempts and retry-exhausted events are visible without forming an
  unbounded recursive tree;
- live updates converge to the durable projection after reconnect;
- representative workflow and agent traces are visually verified.

## Milestone 3 — Integration packaging discipline

### Issue 6: Solution automation completeness validation

Add a StackStorm-pack-inspired validation report for a Solution's event-driven
automation graph. Validate that declared sources, subscriptions, target
workflows/agents, integration/config requirements, and criteria are complete
and portable before deployment.

Acceptance:

- dangling source/target references fail validation;
- missing integration mappings/config schema are reported before deployment;
- criteria and input mappings are validated against the same runtime contracts;
- the report distinguishes required environment bindings from portable content;
- no second pack format or registry is introduced.

### Issue 7: External event producer contract

Document and implement the supported boundary for polling or streaming systems
that would be StackStorm sensors: an externally operated producer or scheduled
Bifrost workflow emits a typed topic with scoped credentials and idempotency.

Acceptance:

- producers do not run arbitrary long-lived code inside API/scheduler services;
- topic emission authenticates, authorizes, scopes, rate-limits, and deduplicates
  producer events;
- producer identity and source event ID are present in lineage without storing
  secrets;
- a reference producer is exercised through the full rule and trace path.

## Milestone 4 — Policy and documentation convergence

### Issue 8: Execution policy discovery

After the Celery-learnings execution-policy registry settles, expose a read-only
projection explaining which runner and policy govern each event target. Extend
the canonical registry only where the event-driven use cases prove a missing
policy dimension.

Acceptance:

- no parallel policy registry exists;
- concurrency/retry/timeout ownership is unambiguous for workflows, agents, and
  PlatformJobs;
- resource-attribute concurrency is either implemented with durable semantics
  or explicitly rejected with evidence;
- operator documentation uses one vocabulary and links to the authoritative
  policy definitions.

### Issue 9: Architecture decision and runbook

Publish the final ADR and operational runbook covering rule evaluation,
decision evidence, replay/retry, trace diagnosis, producer onboarding, and why
StackStorm is not embedded.

Acceptance:

- architecture docs match code and generated contracts;
- migration and rollback procedures are tested;
- on-call exercises a non-match, evaluator error, target failure, retry, and
  retry exhaustion using the runbook;
- the research conclusions are updated if implementation evidence contradicts
  an assumption.

## Delivery sequence

1. Inventory production `filter_expression` usage and make the explicit data
   migration decision.
2. Implement Issues 1–2 behind the normal subscription contract, not a shadow
   runtime.
3. Deliver authoring/operator surfaces (Issue 3) and use the pilot gate.
4. Build the read projection before UI trace work (Issues 4–5).
5. Add Solution validation and producer contract (Issues 6–7).
6. Reconcile with the completed Celery policy work (Issue 8).
7. Prove the runbook and close the ADR (Issue 9).

## Notional sizing and dependencies

Sizing is relative and must be revised after production-data preflight.

| Work | Size | Primary dependencies |
| --- | --- | --- |
| Preflight and migration decision | S | Read-only deployed database access and an owner for existing values |
| Issue 1 | M | Contracts, migration, manifests/Solutions, CLI/MCP parity |
| Issue 2 | M | Issue 1, EventProcessor, delivery model, retries/status aggregation |
| Issue 3 | M | Issues 1–2, generated client types, event subscription/detail UI |
| Issue 4 | M | Stable decision evidence schema and authorization review |
| Issue 5 | M | Issue 4 and live-update reconciliation |
| Issue 6 | M | Stable criteria/input-mapping contracts and Solution validators |
| Issue 7 | M–L | A named producer, auth/idempotency/rate-limit product decisions |
| Issue 8 | S–M | Completion of the Celery-learnings execution-policy registry |
| Issue 9 | S | All funded implementation issues and an operable test environment |

Issues 1–3 are the recommended minimum investment. Issues 4–9 remain options,
not sunk-cost obligations.

## Epic completion gate

The epic is complete only when every issue acceptance item is evidenced by
current code, migrations, generated contracts, focused tests, relevant full
suites, rendered UI inspection where applicable, and updated architecture and
operations documentation. Research and a plan alone do not complete the epic.
