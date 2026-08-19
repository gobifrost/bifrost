# Working against a live instance with no local source

Use this path when Bifrost operations are available but a local source filesystem is not — an MCP-connected harness, a hosted session, or any environment where you cannot edit files on disk and run a build. Treat every mutation as a live change on the connected instance.

This reference is about *that constraint*, not about one transport. Resolve operation names and arguments the way the main Skill directs: discover capabilities at runtime when available, otherwise consult `generated/operations.md` for the intent-to-binding map. Do not translate a remembered flag from one transport into a guessed argument in another, and do not invent a name because it looks like the pattern.

## Establish context first

Identify the target organization, the existing entity, its access controls, and its dependents before changing anything. Do not infer instance or Solution ownership from names alone.

Operations of this kind primarily reach live instance entities and `_repo` content. **If an entity is Solution-managed, stop.** Route the change through its Solution source and deploy. Creating a loose duplicate to bypass ownership is never the answer, and the platform will keep rejecting writes to the managed record.

## Private Solution Builder sessions

When the caller holds `solutions.build` and can access a private Solution, the progressive Agent gateway exposes that Solution's deterministic Builder Agent. Search for it, hydrate its instructions, and use the workspace operations it advertises. Do not create loose `_repo` files or a second Agent to work around it.

Builder workspace operations are session-scoped: each call names the Builder session whose current immutable revision it reads or changes. Mutating operations create a new auditable revision and expose a finalize flag:

- leave finalize off while making intermediate edits;
- set it on the final successful mutation to validate the revision and enqueue the app build;
- inspect the returned revision and job identifiers before claiming the build is ready.

This is the bring-your-own-harness path: the external model performs the coding loop, while Bifrost keeps authorization, revision history, validation, build dispatch, and deployment inside the same Solution boundary as the native Builder.

## Read before you write

Always read the current entity or file before mutating it. Prefer a focused patch when it can express the change safely; use a whole-file write only after preserving unrelated content. Read back and validate after writing.

## Source and registration are separate

Editing a Python file does not create a workflow record. Register each decorated callable that should execute, including organization, access level, and roles. Editing the body of an already-registered function keeps its registration — but when renaming or moving one, preserve the record rather than creating an accidental second workflow.

An agent tool must reference an `@tool`-decorated workflow registration. Source presence, or a plain `@workflow`, is not enough.

Editing v1 app source likewise requires an existing `inline_v1` app record with compatible scope. Apply `apps-v1.md` and `app-quality.md`, then validate and inspect the rendered app rather than stopping after replacing source.

## Entity mutations

Before any create or update:

1. Get the target and its dependents.
2. Confirm organization, access level, roles, and policies.
3. Send complete list fields for agents and other replacement-style updates — a partial list replaces, it does not merge.
4. Re-read the result and exercise the consuming behavior.

Use the operation's live schema for required fields rather than a remembered shape.

## Known parity boundary

Any operation listed in `generated/operations.md` follows the canonical thin REST-wrapper pattern, so its authorization, validation, and side effects match the REST behavior. That table is the boundary: it is generated from the operation catalog, so it is current by construction, and enumerating the domains here would only go stale.

An operation NOT in that table has not been through the canonical boundary and may not match every REST side effect or permission check. Consult the operation's live schema, and prefer a catalogued operation when one covers the same need.

When behavior is ambiguous or production-sensitive, inspect the result carefully and prefer a catalogued operation when one exists.

## Verification and handoff

- Validate edited code or content with the available validation operation.
- Execute the workflow, or inspect the app or entity, as a realistic caller would.
- Test permission denial when access changed.
- Report every live mutation, and name any source or deploy work this environment could not perform.
- Never claim a Solution source change was made when only a loose live entity changed.
