# Python Workflows

Use workflows for server-side logic, integrations, secrets, durable execution, form handlers, event subscribers, and agent tools.

## Authoring contract

Import decorators and SDK modules from `bifrost`. Workflow inputs come from the function signature; do not add a `ctx` parameter.

```python
from bifrost import tables, workflow


@workflow
async def list_open_tasks(limit: int = 50) -> dict:
    result = await tables.query(
        "tasks",
        where={"status": "open"},
        limit=limit,
    )
    return {
        "items": [dict(id=row.id, **row.data) for row in result.documents],
        "total": result.total,
    }
```

Prefer `async def`. Use type annotations, explicit defaults, a useful first-line docstring, and JSON-serializable results. Handle expected integration/API failures with actionable errors and avoid returning secrets.

Choose the decorator by consumer:

| Decorator | Use |
|---|---|
| `@workflow` | General execution from apps, forms, events, other workflows, or the CLI |
| `@tool` | A callable intentionally exposed to an AI agent; equivalent to a tool workflow |
| `@data_provider` | Dynamic form or select options |

An agent's `tool_ids` must reference `@tool` registrations, not plain workflows.

## Test-driven iteration

Search existing modules before implementing the behavior. Reuse or extend the
module that owns the domain or integration; keep the decorated workflow thin:
validate inputs, orchestrate module calls, and shape the result. Put new
reusable logic in a module rather than burying it in a workflow body.

Test module behavior locally without requiring a registration or platform
round trip. Write a focused failing test, implement it, then test the thin
workflow boundary and exercise the decorated function against the selected
instance.

For a local file:

```bash
bifrost run functions/tasks.py -w list_open_tasks --org <org-ref> --params '{"limit":10}'
```

`bifrost run` executes local source without registration. Use `bifrost workflows execute <ref>` for a registered loose workflow and `bifrost solution start` for Solution app-to-workflow behavior.

## Ownership and registration

### Solution-owned workflow

Place source under `functions/` and reference it by the portable workspace-root-relative locator `functions/tasks.py::list_open_tasks`.

Every callable that should become a workflow entity needs an entry in `.bifrost/workflows.yaml`. Adding a `.py` file alone bundles source but does not create the workflow row.

```yaml
workflows:
  <fresh-uuid>:
    id: <fresh-uuid>
    name: list_open_tasks
    path: functions/tasks.py
    function_name: list_open_tasks
```

Create/update the row with `bifrost solution deploy`. Do not run live registration for Solution-owned workflows.

### Loose instance workflow

Write the file into instance `_repo` with `bifrost files`, then register each executable callable:

```bash
bifrost workflows register \
  --path workflows/tasks.py \
  --function-name list_open_tasks \
  --org "Org A" \
  --access-level authenticated
```

Registration creates the stable, scoped, permissioned record. Editing the body keeps the registration. For a move or rename:

```bash
bifrost workflows list-orphaned
bifrost workflows replace <existing-ref> \
  --path workflows/new_tasks.py \
  --function-name list_open_tasks
```

Never re-register a rename: that mints a new UUID and breaks dependents. Use `workflows remap <source-ref> --to <target-ref>` only to move references between two intentional existing records.

## Dependencies

Python worker packages are instance-wide and use the requirements surface. Treat changes as shared environment mutations even when a Solution workflow needs the package:

```bash
bifrost requirements install httpx==0.27.0
bifrost requirements list
bifrost requirements remove httpx
```

Worker recycling is asynchronous after a requirements change. App npm dependencies are unrelated.

## Agent-tool discoverability

When exposing a workflow through an agent/MCP server, use a distinctive `{context}_{action}` name such as `halopsa_search_tickets`, not a generic name such as `search`. Make the description self-contained: name the feature/domain, action, and important objects so deferred tool search can find it without server-name context.

Use one stable context prefix across a related toolset. Do not force this convention on internal workflows that are not exposed as tools.

## Verification

Before handoff:

- test normal, invalid, empty, and downstream-failure inputs as relevant;
- verify the intended org/access/roles on the workflow row;
- confirm every form, agent, app, or event reference resolves;
- execute in the same Solution/loose context production will use;
- consult `python-sdk.md` for SDK return-shape traps and `../generated/python-sdk-signatures.md` for exact signatures.
