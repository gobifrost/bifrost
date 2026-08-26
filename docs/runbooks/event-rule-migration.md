# Event Rule Migration and Diagnosis

## Preflight

Run this read-only query in every deployed environment before applying
`20260827_event_criteria`:

```sql
SELECT
    id,
    event_source_id,
    target_type,
    workflow_id,
    agent_id,
    filter_expression,
    created_by,
    updated_at
FROM event_subscriptions
WHERE filter_expression IS NOT NULL
  AND BTRIM(filter_expression) <> ''
ORDER BY updated_at DESC;
```

This development host exposed no deployed database credentials or running
debug/test stack, so repository work could not substitute for this
environment-by-environment operator check. The migration repeats the check and
fails with an actionable hint rather than discarding or activating data.

For each returned row, choose one of two explicit actions:

1. translate the intended behavior into version-1 structured criteria and
   deploy it through the supported subscription/Solution surface; or
2. confirm the subscription is intentionally unconditional and clear the
   legacy value before upgrading.

Do not implement an expression compatibility runtime.

## Diagnosis

On the event detail page, inspect each delivery's rule outcome:

- `criteria_matched`: target was eligible and any later failure belongs to
  dispatch or execution;
- `criteria_not_matched`: expected terminal skip;
- `field_type_mismatch`: payload field existed but did not have the type needed
  by a string or numeric operator;
- `invalid_persisted_criteria`: stored data failed the current versioned
  contract; repair the subscription/Solution definition;
- `internal_evaluator_error`: unexpected evaluator defect; capture event and
  subscription IDs, not raw payload values, and escalate.

Evaluation errors make the event failed. Ordinary non-matches allow it to
complete. Neither outcome can be retried as an existing delivery.

## Rollback

Application rollback should precede database downgrade. Downgrade recreates the
legacy nullable string column but cannot translate structured criteria back
into expressions; the old field remains null. Preserve an export of current
subscription definitions before rollback if criteria will need to be restored
after re-upgrade.
