# Entity CLI Reference

This file documents the CLI mutation surface for each entity group. The canonical per-command flag reference is always the live `--help` output for each command — flags are generated from `XxxCreate` / `XxxUpdate` Pydantic DTOs at runtime and cannot drift. This file documents commands, non-obvious semantics, and cross-entity relationships only.

**Core rule (global `_repo` workspace):** `.bifrost/` is export-only. Never read it for entity discovery — use the `list` and `get` subcommands for each entity group. All CLI commands accept `--json` for machine-readable output. Refs resolve by UUID, name, or portable ref (e.g. `path::function` for workflows, slug for apps). (**Solution workspaces differ** — there `.bifrost/*.yaml` is the deploy-read manifest and editing entity *content fields* there is the documented update path; see `references/solutions.md`.)

For the exact generated flag list, see `generated/cli-reference.md`. For the REST passthrough escape hatch, see `references/rest-api.md`.

**Unified `--org` standard (org targeting).** Every org-targeting command — `tables`, `forms`, `agents`, `configs`, `claims`, `workflows`, `events`, and the `solution` subcommands — takes the same flag:

- **Omit it** → your own org ("home"). A bare command never writes a global entity by accident.
- **`--org <uuid|name>`** → that org.
- **`--global`** (or `--org none` / `--org global`) → global scope (`organization_id` NULL).
- **`--organization` and `--scope`** are permanent synonyms for `--org`.

(`claims` are always org-scoped: `--global` is rejected. `apps` and integration `add-mapping`/`update-mapping` predate the standard and keep their own `--organization`/`--scope` flags.)

---

## Organizations

Tenant records that scope users, configs, and most other entities.

Commands: `orgs list / get / create / update / delete`

```bash
bifrost orgs create --name "Acme"
bifrost orgs list
bifrost orgs get <ref>           # UUID or name
bifrost orgs update <ref> --name "Acme Corp"
bifrost orgs delete <ref>
```

---

## Roles

Named permission buckets. Assigned to workflows, forms, agents, and apps to gate role-based access.

Commands: `roles list / get / create / update / delete`

```bash
bifrost roles create --name "Sales Manager" --permissions @perms.yaml
bifrost roles update <ref> --permissions @perms.yaml
```

Non-obvious: `permissions` is a dict (not a list). Pass JSON inline or `@path/to/file.yaml`.

---

## Workflows

Registered Python functions executable by forms, agents, events, or HTTP.

Commands: `workflows list / get / register / execute / update / delete / grant-role / revoke-role / replace / list-orphaned / remap`

```bash
# Register a decorated function from an existing workspace file
bifrost workflows register --path workflows/foo.py --function-name foo

# Execute remotely (streams logs via WebSocket)
bifrost workflows execute <ref> --params '{"name": "World"}'

# Rename/move without breaking references — always use replace, not re-register
bifrost workflows list-orphaned
bifrost workflows replace <uuid> --path workflows/new_path.py --function-name new_func

# Remap all references from one UUID to another
bifrost workflows remap <source-ref> --to <target-ref>
```

Non-obvious semantics:
- `<workflow-ref>` accepts UUID, name, or `path::func` (e.g. `workflows/sales.py::close_deal`).
- `get` is served by list-and-filter (no per-id GET endpoint).
- `register` mints a new UUID. Never re-register after a rename — use `replace` to preserve the UUID and all form/agent/app references.
- `execute --params '{...}'` runs a registered workflow remotely; streams logs and exits 0/1 on terminal status. Use `bifrost run <file> -w <name>` for local-file iteration.
- `delete --force` bypasses the 409 guard on workflows that have dependents.
- Type changes (`@workflow` → `@data_provider` etc.) during `replace` are blocked by default; pass `--allow-type-change` only when intentional.

See `references/workflows-python.md` for authoring and `references/python-sdk.md` for the SDK inside a running workflow.

---

## Forms

User-facing parameter collection UIs that launch a workflow on submit.

Commands: `forms list / get / create / update / delete`

```bash
bifrost forms create --name onboard --workflow path::func --form-schema @schema.yaml
bifrost forms update <ref> --name "Onboarding" --workflow new-wf-uuid
```

Non-obvious:
- `--form-schema` accepts YAML/JSON inline or `@path/to/schema.yaml`. Schema shape: `{fields: [...]}`.
- `--workflow` / `--launch-workflow` accept portable refs (UUID, name, or `path::func`).

---

## Agents

LLM-backed conversational entities with configurable tools, delegations, knowledge sources, and system prompts.

Commands: `agents list / get / create / update / delete`

```bash
bifrost agents create --name support --system-prompt @prompt.md --tool-ids wf1,wf2
bifrost agents update <ref> --system-prompt @new_prompt.md
```

Non-obvious:
- `update` uses PUT (full replacement of `tool_ids` / `delegated_agent_ids` / `knowledge_sources` lists — not merge). Always pass the complete list.
- `--system-prompt @file.md` loads a multi-line prompt from disk.
- `--tool-ids`, `--delegated-agent-ids`, `--role-ids` accept comma-separated refs. Resolved: workflows for tools, agents for delegations, roles by name.
- `--clear-roles` wipes all role assignments.

Do NOT use the removed granular endpoints `/api/agents/{id}/tools` or `/delegations`. Use `update` with the full lists.

---

## Apps

App Builder applications — TSX/TypeScript source + npm dependencies. See `references/apps.md` for design rules.

Commands: `apps list / get / create / update / set-deps / replace / delete`

```bash
bifrost apps create --name dashboard --slug dashboard --deps @package.json
bifrost apps update <ref> --name "Operations Dashboard"
bifrost apps set-deps <ref> --deps '{"recharts": "^2.12.0"}'
```

Non-obvious:
- `create --deps` is a two-call orchestration (POST app, then PUT dependencies). If the deps call fails the app remains created.
- `get` accepts slug, UUID, or name.
- `update` is a patch (no staging step) — metadata changes apply to the live app.
- `replace <ref> --repo-path <new>` repoints an app's source directory. Validates uniqueness and nesting; use `--force` to bypass. Does NOT move S3 files.
- App npm dependencies are separate from Python workflow deps (`bifrost requirements install`).

---

## Custom Claims

Org-scoped, query-resolved facts about the calling user. Table policies reference them as `{claims: "<name>"}`.

Commands: `claims list / get / create / update / delete`

```bash
bifrost claims create --name allowed_campus_ids --type list \
  --query '{"table":"user_campus_access","where":{"eq":[{"row":"user_id"},{"user":"user_id"}]},"select":"campus_id"}'
```

Example policy using a claim:
```json
{ "name": "scoped_read", "actions": ["read"], "when": { "in": [{ "row": "campus_id" }, { "claims": "allowed_campus_ids" }] } }
```

---

## Integrations

External service definitions (HaloPSA, Microsoft Graph, etc.) with config schemas and per-org mappings.

Commands: `integrations list / get / create / update / add-mapping / update-mapping`

```bash
bifrost integrations create --name halopsa --config-schema @schema.yaml
bifrost integrations update <ref> --config-schema @updated.yaml
bifrost integrations add-mapping <integration-ref> --organization <org-ref>
bifrost integrations update-mapping <integration-ref> --organization <org-ref> --config @config.yaml
```

Non-obvious:
- `update --config-schema` refuses to remove keys unless `--force-remove-keys` is set (Config rows cascade-delete when schema rows are removed).
- `update-mapping` never touches `oauth_token_id` unless explicitly passed via `--oauth-token-id`.
- `--organization` on mappings resolves by org ref (UUID or name).

---

## Configs

Key-value configuration entries (global or per-org), optionally encrypted.

Commands: `configs list / get / create / update / set / delete`

```bash
bifrost configs set api_key --value xyz --type secret --organization acme
bifrost configs set api_url --value "https://api.example.com"
bifrost configs get api_key
bifrost configs delete api_key --confirm
```

Non-obvious:
- `set <key>` is an upsert wrapper: routes to PUT if `(key, org)` exists, POST otherwise. This is the recommended write path.
- `get` is served by list-and-filter (no per-id GET endpoint).
- `update` with `--value` omitted preserves the existing (encrypted) value.
- `delete` on a secret-type config refuses unless `--confirm` is set.

---

## Tables

Document-store tables (JSON documents, optional schema hints). See `references/tables.md` for the full data model, filter DSL, and policy rules.

Commands: `tables list / get / create / update / delete`

```bash
bifrost tables create --name clients --schema @schema.yaml
bifrost tables update <ref> --name new_name
```

Non-obvious:
- Tables use policy rules for row-level access. Default: freshly-created table has an `admin_bypass` policy; without other rules, only platform admins can read/write.
- `update --name` prints a warning about SDK references (`sdk.tables.get("clients")` uses the name). Grep the workspace before pushing.
- Browser apps call tables directly via `import { tables, useTable } from "bifrost"`.

---

## Events

Event sources (webhook / schedule / topic) and subscriptions that dispatch events to workflows or agents.

Commands: `events list-sources / get-source / list-subscriptions / get-subscription / create-source / update-source / subscribe / update-subscription`

```bash
# Schedule source
bifrost events create-source --name nightly --source-type schedule --cron "0 2 * * *" --timezone UTC

# Webhook source
bifrost events create-source --name hook --source-type webhook --adapter generic

# Topic source
bifrost events create-source --name "User Invited" --source-type topic --event-type user.invited

# Subscribe a workflow to a source
bifrost events subscribe <source-ref> --workflow <wf-ref> --event-type ticket.created

# Update a subscription
bifrost events update-subscription <source-ref> <subscription-id> --event-type ticket.updated
```

Non-obvious:
- `update-subscription` takes TWO positional args (`<source-ref> <subscription-id>`).
- `--cron` / `--timezone` / `--schedule-enabled` collapse into the DTO's nested `schedule` config.
- `--adapter` / `--webhook-integration` / `--webhook-config` collapse into nested `webhook` config.
- `update-subscription` rejects changes to `target_type` / `workflow_id` / `agent_id` — delete and recreate instead.
- `subscribe` accepts exactly one of `--workflow` or `--agent`; target type is inferred.

### Topic event context (workflow receiving an event)

When a workflow is triggered by a topic, `context.event` is set:
- `context.event.type` — topic string
- `context.event.data` — payload dict
- `context.event.organization_id` — org stamped at emit time
- `context.event.received_at` — ISO-8601 timestamp

### Emitting from a workflow

```python
from bifrost import events
result = await events.emit("acme.deal_won", {"amount": 50000})
# result: {"event_id": "...", "subscribers_notified": N}
```

### Built-in topics

| Topic | When emitted | Key payload fields |
|-------|-------------|-------------------|
| `user.invited` | `POST /api/users` with `invite=true`, or resend invite | `user_id`, `email`, `name`, `registration_url`, `expires_at`, `invited_by` |

Full topic docs: `docs/events/topics.md`.

---

## Requirements (Python workflow deps)

Workspace-wide `requirements.txt` for Python packages. Workers install these at process spawn.

Commands: `requirements list / install / remove`

```bash
bifrost requirements install reportlab
bifrost requirements install httpx==0.27.0
bifrost requirements install          # warm cache + recycle workers (no package arg)
bifrost requirements remove reportlab
```

All install/remove operations recycle workers asynchronously (returns immediately). Wait a few seconds before running a workflow that needed the package.

---

## Solutions

An installable surface — a workspace (apps + workflows + tables + configs + forms + agents) that deploys as one unit. See `references/apps.md` for the Solution app structure and `SKILL.md` for the Solutions skill.

Commands: `solution init / scaffold-app / start / deploy / install`

```bash
bifrost solution init --slug my-solution --name "My Solution"
bifrost solution scaffold-app my-app
bifrost solution start [APP_SLUG]          # APP_SLUG positional (the apps/ dir name); optional org targeting + --port
bifrost solution deploy                    # your org; --global or --org <ref> targets elsewhere
bifrost solution install solution.zip --org acme
```

`solution init` carries **no install scope** — install kind (org vs global) is the deploy-time `--org`/`--global` choice (the unified standard, below), not a descriptor field.

Non-obvious:
- `deploy` (alias: `bifrost deploy`) full-replaces the install; refuses bundles older than the installed version unless `--force`.
- `start` requires a logged-in CLI and a reachable dev API (does not boot Docker). Needs `npm` on PATH.
- Workflow refs are workspace-ROOT-relative: `functions/hello.py::main` regardless of where the calling app lives.

---

## Cross-environment Distribution

To package and install entities into an org: use Solutions (the `solution deploy` and `solution install` subcommands). The old export/import bundle commands were removed (they predated Solutions and their `--portable` scrub did not strip env-specific fields).

For raw workspace movement, use the git subcommands (the commit and push operations in the git subcommand group).

---

## Do NOT

- Edit `.bifrost/*.yaml` by hand to mutate entities **in the global `_repo` workspace** — there it is export-only. (In a **Solution** workspace this rule is reversed: editing entity content fields in `.bifrost/*.yaml` + redeploy is the correct update path — see `references/solutions.md`.)
- Use the removed granular agent endpoints `/api/agents/{id}/tools` or `/delegations`.
- Add direct ORM access to new MCP tools. Thin HTTP wrappers only (enforced by `api/tests/unit/test_mcp_thin_wrapper.py`).
- Duplicate flag lists here. Trust `--help` — it is generated from the DTO.
