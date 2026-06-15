# Workflows — Python Authoring

Python workflow functions are the compute layer of Bifrost. They live in `.py` files in the workspace, are decorated with `@workflow` (or `@tool` / `@data_provider`), and run inside the platform's worker pool.

---

## Decorators

Three decorators from `bifrost.decorators`. All accept only **identity** parameters — execution configuration (schedules, timeouts, endpoints) is managed via the UI or CLI `update`, not in source code.

### @workflow

General-purpose executable callable by forms, agents, events, HTTP, or directly.

```python
from bifrost import workflow

@workflow
async def greet_user(name: str, count: int = 1) -> dict:
    """Greet a user multiple times."""
    return {"greetings": [f"Hello {name}!" for _ in range(count)]}

@workflow(category="Admin", tags=["m365"])
async def onboard_user(email: str, license_type: str = "E3") -> dict:
    """Onboard a new M365 user."""
    ...
```

Parameters: `name` (override function name), `description` (override docstring first line), `category` (default `"General"`), `tags` (list), `is_tool` (bool — mark as AI agent tool).

Unknown parameters are silently ignored for backwards compatibility (a warning is logged).

### @tool

Alias for `@workflow(is_tool=True)`. Use when the function is designed specifically as an AI agent tool.

```python
from bifrost import tool

@tool
async def get_user_info(email: str) -> dict:
    """Get user information by email address."""
    ...

@tool(description="Search for users by name or email")
async def search_users(query: str, limit: int = 10) -> list[dict]:
    ...
```

### @data_provider

Provides dynamic options for form fields or app builder selects. Stored in the workflows table with `type='data_provider'`.

```python
from bifrost import data_provider

@data_provider
async def get_departments() -> list[str]:
    """Get list of departments."""
    return ["Engineering", "Sales", "Marketing"]

@data_provider(category="m365")
async def get_m365_users() -> list[dict]:
    """Returns M365 users for the organization."""
    ...
```

Parameters are derived from function signatures automatically — no `@param` decorator is needed.

---

## Local Iteration

For offline/local testing of a workflow without registering it on the platform:

```
bifrost run <file.py> -w <function_name> --org <uuid>
```

This runs the decorated function in-process using the local code. Use it to iterate on logic before registering.

---

## Lifecycle Commands

### Register

After writing a `.py` file, register the function so it becomes executable on the platform:

```bash
bifrost workflows register --path workflows/onboard.py --function-name onboard_user
```

Optional flags: `--org <ref>`, `--access-level authenticated|everyone|role_based`, `--role-ids <ref,...>`.

The file must already exist in the workspace. `register` mints a new UUID for this workflow.

### Replace (rename/move without breaking references)

When a function is renamed or its file moved, `list-orphaned` finds the stranded record; `replace` repoints it to the new location while preserving the UUID — so every form, agent, and app reference stays valid:

```bash
# Find orphaned UUIDs
bifrost workflows list-orphaned

# Repoint to the new location (file must exist and contain the named decorated function)
bifrost workflows replace <uuid> --path workflows/new_path.py --function-name new_func

# Allow decorator type to change (default: blocked to protect form bindings)
bifrost workflows replace <uuid> --path workflows/new_path.py --function-name new_func --allow-type-change
```

Do NOT re-register after a rename. That mints a new UUID and breaks all existing references. Always use `replace`.

### Remap

Move all references from one workflow UUID to another active workflow UUID (useful when consolidating duplicates):

```bash
bifrost workflows remap <source-ref> --to <target-ref>
```

### Execute (remote)

Run a registered workflow and stream logs:

```bash
bifrost workflows execute <ref> --params '{"name": "World"}'
bifrost workflows execute <ref> --params-file input.json
```

Opens a WebSocket to stream logs; exits 0/1 based on terminal status. Use `bifrost run <file>` for local-file iteration; `execute` targets workflows already on the platform.

### List, Get, Update, Delete

```bash
bifrost workflows list
bifrost workflows get <ref>          # accepts UUID, name, or path::function
bifrost workflows update <ref> --name new_name
bifrost workflows delete <ref>
bifrost workflows delete <ref> --force   # bypass 409 on dependents
```

### Role management

```bash
bifrost workflows grant-role <ref> <role-ref>
bifrost workflows revoke-role <ref> <role-ref>
```

---

## Python Dependencies

Workspace-wide Python packages for workers. Workers install these at process spawn.

```bash
bifrost requirements install reportlab
bifrost requirements install httpx==0.27.0    # pin version
bifrost requirements install                  # warm cache + recycle workers
bifrost requirements remove reportlab
```

Workers recycle asynchronously — returns immediately. Wait a few seconds before running a workflow that needs a newly-installed package.

App npm dependencies (`bifrost apps update --deps`) are separate and unrelated.

---

## Python SDK

For the full SDK surface available inside a running workflow (tables, integrations, config, ai, events, etc.), see `references/python-sdk.md`.
