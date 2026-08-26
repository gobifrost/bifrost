# Event subscription rules design

Issue: [#652](https://github.com/gobifrost/bifrost/issues/652)

Status: proposal

## Problem

`EventSubscription.filter_expression` is accepted by the REST API, CLI, Git
manifests, and Solution bundles, but neither webhook nor topic event processing
evaluates it. Once the source and optional event type match, Bifrost creates a
pending delivery and queues the target regardless of the stored expression.

That is an unsafe contract for an automation platform. An operator can believe
that a rule limits execution to high-priority or production events while the
runtime executes the target for every event of that type.

The field is described as JSONPath, but examples use comparison expressions
such as `$.priority == 'high'`. Standardizing that ambiguous string language
would also require decisions about parsing, coercion, resource bounds, error
handling, and injection safety.

## Proposed direction

Replace the inert expression string with versioned structured criteria. Treat
an event subscription as a durable trigger-to-target rule:

1. Match the source and optional event type.
2. Evaluate optional criteria against a normalized event envelope.
3. Persist the decision for each subscription and event.
4. Queue the workflow or agent only when the decision is `matched`.

This follows a useful boundary seen in mature automation and task systems:
declarative routing remains separate from executable workflow code, dispatch
decisions are observable, and retry does not silently change a prior routing
decision.

## Criteria contract

Criteria are JSON data rather than executable text:

```json
{
  "version": 1,
  "root": {
    "kind": "all",
    "items": [
      {
        "kind": "condition",
        "field": "event.body.priority",
        "operator": "in",
        "value": ["high", "urgent"]
      },
      {
        "kind": "condition",
        "field": "event.body.occurrence_count",
        "operator": "greater_than_or_equal",
        "value": 3
      }
    ]
  }
}
```

Version 1 should support bounded `all`, `any`, and single-child `not` groups.
Conditions should cover:

- equality and inequality;
- membership and non-membership;
- field existence;
- string contains, prefix, and suffix;
- numeric comparisons.

The contract must cap field-path length, nesting depth, group width, and total
node count. Mutation surfaces validate the same contract before persistence.

## Normalized envelope

All ingress paths evaluate the same shape:

- `event.type`
- `event.body`
- `event.headers`
- `event.received_at`
- `event.source_ip`
- `schedule.scheduled_time`
- `schedule.cron_expression`
- `schedule.timezone`

Webhook, topic, and schedule processing must call the same evaluator before
queueing. A feature is incomplete if only one ingress path enforces rules.

## Type and error semantics

Comparison is strict and predictable:

- strings do not equal numbers;
- booleans do not equal integers;
- numeric comparisons require a numeric event value;
- missing fields do not match, except `not_exists`;
- invalid persisted criteria and runtime type mismatches fail closed.

An evaluation error must never queue a target. This is preferable to coercion:
automation filters are safety boundaries, and guessing can create irreversible
side effects.

## Durable decision evidence

Create one delivery record per subscription considered for an event, including
non-matches. Store bounded evidence such as:

```json
{
  "criteria_version": 1,
  "outcome": "not_matched",
  "code": "criteria_not_matched"
}
```

Supported outcomes are:

- `matched`: normal pending-to-queued delivery lifecycle;
- `not_matched`: terminal skipped delivery, no target execution;
- `evaluation_error`: terminal skipped delivery, no target execution, event
  marked failed for operator visibility.

Evidence must not copy event values or rendered expressions. An unconditional
subscription records `matched` with an `unconditional` code.

Skipped deliveries are not retryable. Retrying a prior non-match under changed
criteria would change the meaning of the recorded decision. Creating a new
delivery for a newly added subscription remains a separate, explicit action.

## Migration

Do not silently interpret or discard non-empty `filter_expression` values.
Before removing the column, migration should refuse to proceed when such rows
exist and provide a query/runbook for operators to review them. Operators must
replace each expression with structured criteria or explicitly clear it.

Deploy, manifest, CLI, MCP, and REST surfaces must move together so criteria
cannot be accepted by one path and ignored by another.

## Acceptance criteria

- A matching event creates exactly one queueable delivery and one target run.
- A valid non-match creates an observable skipped delivery and no target run.
- A missing field or heterogeneous comparison follows the documented strict
  semantics and never queues accidentally.
- Invalid persisted criteria fail closed with bounded diagnostic evidence.
- Webhook, topic, and schedule ingress share the evaluator.
- Duplicate delivery creation and retry cannot create a second execution for a
  terminal successful or skipped delivery.
- Existing non-empty legacy expressions require explicit operator action during
  migration.

## Delivery sequence

1. Add pure evaluator contracts and exhaustive unit tests.
2. Integrate webhook and topic delivery creation with durable decisions.
3. Integrate schedule delivery creation through the same helper.
4. Add persistence, API, CLI, manifest, Solution, and operator UI surfaces.
5. Ship the guarded legacy-expression migration and migration runbook.
6. Exercise match, non-match, type-error, and duplicate-safety cases end to end.
