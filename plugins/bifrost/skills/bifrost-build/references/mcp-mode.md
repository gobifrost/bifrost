# MCP-Only Bifrost Work

Use this path when Bifrost MCP tools are available but the CLI or local source filesystem is not. Treat mutations as live changes on the connected instance.

## Establish context

Use the available list/get tools to identify the target organization, existing entity, access controls, and dependencies. Do not infer instance or Solution ownership from names alone.

MCP tools primarily operate on live instance entities and `_repo` content. If an entity is Solution-managed, stop and route the change through its Solution source/deploy workflow; do not create a loose duplicate to bypass ownership.

## Private Solution Builder sessions

The progressive Agent gateway exposes a private Solution's deterministic
Builder Agent when the caller has `solutions.build` and can access that
Solution. Search for the Builder Agent, hydrate its instructions, and use its
existing workspace tools; do not create loose `_repo` files or a second Agent.

Every Builder workspace call requires the `builder_session_id` shown by the
native Builder session. Read tools inspect that session's current immutable
revision. Mutating tools create a new auditable revision and accept
`finalize`:

- leave `finalize` false while making intermediate edits;
- set `finalize` true only on the final successful mutation to validate the
  revision and enqueue the app build;
- inspect the returned `revision_id` and `deploy_job_id` before claiming the
  build is ready.

This is the bring-your-own-harness path: the external model performs the
coding loop, while Bifrost keeps authorization, revision history, validation,
build dispatch, and deployment inside the same Solution boundary as the native
Builder.

## Discover, read, then write

Tool names can vary by installed server version. Search the available tools and use their provided schemas rather than inventing a name or argument.

Common entity patterns are:

- `list_*` and `get_*` for discovery;
- `create_*`, `update_*`, and `delete_*` for live entities;
- `bifrost_list_files`, `bifrost_search_files`, `bifrost_read_file`,
  `bifrost_stat_file`, and `bifrost_exists_file` for `_repo` discovery;
- `bifrost_patch_file` for focused source edits;
- `bifrost_write_file` and `bifrost_delete_file` for complete file mutations;
- `bifrost_register_workflow`, `bifrost_validate_workflow`, and
  `bifrost_execute_workflow` for loose workflows;
- `bifrost_validate_app`, `bifrost_publish_app`, and the canonical App
  dependency/status tools for v1 apps.

Always read the current entity/file before mutation. Prefer `bifrost_patch_file`
when it can express the change safely; use `bifrost_write_file` only after
preserving unrelated content. Read back and validate after writing.

## Source and registration

Editing a Python file does not create a workflow record. Register each decorated callable that should execute, including organization/access/roles. Editing an existing registered function body keeps its registration; preserve the record when renaming/moving rather than creating an accidental second workflow.

An agent tool must reference an `@tool`-decorated workflow registration. Source presence or a plain `@workflow` is insufficient.

Editing v1 app source also requires an existing `inline_v1` app record and compatible scope. Apply `apps-v1.md` and `app-quality.md`; validate and inspect the rendered app rather than stopping after source replacement.

## Entity mutations

Before create/update:

1. Get the target and its dependents.
2. Confirm organization, access level, roles, and policies.
3. Send complete list fields for agents and other replacement-style updates.
4. Re-read the result and exercise the consuming behavior.

Use exact tool descriptions for required fields. Do not translate a remembered CLI flag into a guessed MCP argument.

## Known parity boundary

Agent, Form, Table, App, Event, and Workflow lifecycle tools use the canonical
thin REST-wrapper pattern. Some uncatalogued platform domains still predate
that boundary and may not match every REST side effect or permission check;
consult the generated operation catalog and the tool's live schema rather than
assuming parity.

When behavior is ambiguous or production-sensitive, inspect the tool result carefully and prefer a verified REST-wrapper tool if one is available. Do not add direct ORM/repository behavior to new MCP tools; new platform MCP tools should call the REST endpoint.

## Verification and handoff

- Validate edited code/content with the available validation tool.
- Execute the workflow or inspect the app/entity as a realistic caller.
- Test permission denial when access changed.
- Report every live mutation and any source/deploy work that MCP could not perform.
- Never claim that a Solution source change was made when only a loose live entity was changed.
