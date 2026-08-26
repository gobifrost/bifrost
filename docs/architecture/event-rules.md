# Event Rule Criteria

Bifrost event subscriptions are durable trigger-to-target rules. An active
subscription first matches its event source and optional event type, then
evaluates optional structured criteria before a workflow or agent is queued.

## Contract

Criteria are versioned JSON data, not an expression language:

```json
{
  "version": 1,
  "root": {
    "kind": "all",
    "items": [
      {
        "kind": "condition",
        "field": "event.body.priority",
        "operator": "equals",
        "value": "high"
      }
    ]
  }
}
```

Fields are dot-separated paths rooted at `event` or `schedule`. Each path has
2–10 identifier segments. Criteria support `all`, `any`, and single-child
`not` groups, with at most five levels and 50 total nodes.

Version 1 operators are equality/inequality, membership, existence, string
contains/prefix/suffix, and numeric comparisons. Operator values are validated
before REST persistence, manifest import, or Solution deployment.

## Normalized envelope

- `event.type`
- `event.body`
- `event.headers`
- `event.received_at`
- `event.source_ip`
- `schedule.scheduled_time`
- `schedule.cron_expression`
- `schedule.timezone`

Webhook, schedule, and topic events use the same evaluator and envelope.
Missing fields do not match except for `not_exists`. A runtime type mismatch is
an evaluation error and fails closed.

## Durable decisions

Every created `EventDelivery` records safe `rule_decision` evidence:

- `matched`: delivery starts `pending` and follows normal queueing;
- `not_matched`: delivery is terminal `skipped` and never queues a target;
- `evaluation_error`: delivery is terminal `skipped`, the event fails, and the
  operator sees a bounded diagnostic code.

Evidence contains only criteria version, outcome, and code. Event values are
never copied into it. Unconditional subscriptions record a matched decision
with code `unconditional`.

Skipped decisions are not manually retryable. Re-evaluating an old non-match
through retry would violate its durable decision and could create unintended
side effects. A newly added subscription may still create a new delivery for an
old event; that new delivery evaluates the subscription's current criteria.

## Migration

The removed `filter_expression` string was previously persisted but never
evaluated. Migration `20260827_event_criteria` refuses to run when a non-empty
legacy expression exists. Operators must review and replace or explicitly clear
each value; Bifrost does not guess at JSONPath or executable-expression
semantics.
