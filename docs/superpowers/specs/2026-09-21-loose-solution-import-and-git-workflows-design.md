# Loose Solution Import and Git Workflows Design

## Summary

Bifrost will keep managed Solution installs and add a second, explicitly
unmanaged path: **Import into workspace**. A workspace import treats a Solution
package as a portable bundle, applies selected definitions and files directly
to `_repo`, and then ends. It does not create a Solution install, mint an
install scope, or retain an upgrade/uninstall relationship to the package.

The work is intentionally divided into three independently shippable projects:

1. Harden the shared workspace Git synchronization boundary.
2. Add previewed, collision-aware workspace bundle imports.
3. Make repository and managed-Solution Git connection workflows explicit in
   the CLI and UI.

The ordering matters. Workspace imports deliberately produce dirty `_repo`
changes, so they should land on top of a Git path whose consistency and recovery
semantics are clear.

## Goals

- Preserve the current isolated, lifecycle-aware Solution install behavior.
- Allow a Solution package to be imported as ordinary workspace content.
- Resolve collisions per file or entity, with compact bulk controls.
- Preserve destination database identities and runtime-owned state.
- Perform the import as a durable PlatformJob with bounded memory.
- Leave imported content as ordinary dirty Git changes for review and commit.
- Make first-time Git connection, sync, conflicts, and recovery understandable
  from both the UI and CLI.

## Non-goals

- A third persisted Solution lifecycle type.
- Upgrade, uninstall, or provenance tracking for loose imports.
- A separate Git repository for loose imports.
- Arbitrary textual search-and-replace of database IDs.
- Importing secrets, OAuth tokens, config values, or table rows by default.
- Silently resolving content collisions.
- Replacing the manifest format with a workspace-import-specific format.

## Product Model

### Two destination modes

The existing install entry point asks the user where the package should go:

- **Managed Solution** installs into an isolated install scope, creates or
  updates a Solution record, and retains Git/update/uninstall lifecycle.
- **Workspace import** applies the bundle to `_repo`, creates ordinary entities
  with `solution_id = null`, and has no relationship to the package afterward.

The package and manifest remain the transport format in both cases. Destination
mode changes ownership, identity resolution, collision behavior, and lifecycle;
it does not introduce a second package schema.

### Workspace import and Git

Workspace import supports Git through the existing workspace repository. The
job updates `_repo` and the database together, then marks the workspace dirty.
It does not commit or push. The developer reviews the result, runs compatibility
checks, and commits or discards it using the ordinary workspace Git workflow.

This works whether the workspace repository is already connected or detached.
If it is detached, the imported state remains local until the developer chooses
and completes a first-connect reconciliation.

## Import Experience

### Step 1: destination

After selecting a package, the user chooses **Managed Solution** or **Workspace
import**. The managed path retains its current organization/scope and setup
fields. The workspace path explains that the result is unattached, editable,
and appears as uncommitted workspace changes.

### Step 2: review

The preview classifies every package item as:

- `create`: no destination match; no decision required.
- `unchanged`: destination content is equivalent; no decision required.
- `conflict`: an entity natural key or file path already exists and differs.

Conflicts appear as compact rows with type, name/path, match key, and a
two-state **Keep / Replace** control. **Keep all** and **Replace all** apply only
to conflicts. Selecting a row opens one shared inspector with its structured
definition diff or text-file diff.

The warning is explicit:

> Package contents are designed together. Mixing kept and replaced definitions
> can break references. Review the resulting workspace and run compatibility
> checks before committing it.

All conflicts must have a decision before the import can start.

### Modal behavior

The dialog is capped at `90dvh`. Its title/step header and final action footer
remain visible. The review body is the only vertical scroller. The desktop body
uses a compact list plus one inspector; on narrow screens the inspector stacks
below the list inside the same scroller. Rows remain content-sized rather than
stretching to fill unused height.

### Step 3: durable import

Starting the import enqueues a `workspace.bundle_import` PlatformJob. Progress
and terminal status use the shared PlatformJob notification transport. The
dialog may close once accepted; the completed job links back to the workspace
Git changes.

## Collision and Identity Rules

### Natural-key matching

Each supported entity uses the same natural key already used by manifest
resolution, for example workflow `(path, function_name)`, app `slug`, table
`(name, organization_id)`, integration `name`, form name/identity, and file
path. The preview contract returns the match key used so the decision is
explainable.

### Destination IDs win

When a conflicting entity is replaced, its existing destination database ID is
preserved. Incoming structured references to the package entity ID are mapped
to that destination ID before writes. New entities receive destination IDs and
the same map rewrites references among created entities.

Rewriting is limited to typed manifest fields and other registered structured
references. Bifrost does not scan arbitrary database values or source text for
UUID-shaped strings. A package that embeds opaque IDs in unsupported content is
reported as a compatibility warning rather than mutated speculatively.

### Runtime state

Replace means replace the portable definition, not environment-owned runtime
state. Existing table rows, secret/config values, OAuth credentials, integration
tokens, access assignments, creator/timestamp fields, and other nonportable
state remain intact. Restoring package data or secrets remains a separate,
explicit backup operation and is not part of workspace import.

### Partial import

Keep decisions remove those incoming items from the apply set, but their target
IDs remain available to the reference map. The importer is additive: it never
runs manifest stale-entity cleanup and never deletes destination entities merely
because they are absent from the package.

## Backend Architecture

### Preview service

A focused `WorkspaceBundlePlanner` parses the existing Solution package and
normalizes its manifest/files. It prefetches destination natural keys, builds
the package-ID-to-target-ID map, compares portable definitions, and returns a
stable preview token plus classified items.

The preview artifact is staged in object storage under a short-lived,
job-independent key. The token binds the exact package hash, destination,
caller, and preview result. Starting an import submits decisions against that
token; the server refuses missing, expired, duplicated, or unknown item IDs.
The package is not uploaded a second time.

### Apply service

`WorkspaceBundleImporter` consumes the staged package and validated decisions.
It reuses manifest codecs and entity resolvers, but operates through an explicit
partial-import adapter:

- only selected creates/replacements are applied;
- target IDs are supplied by the preview plan;
- all resulting rows are unscoped from Solutions;
- stale-entity deletion is unreachable;
- portable definition writes preserve runtime-owned fields;
- file writes target `_repo`.

The importer must not call `SolutionDeployer`: that service intentionally
creates install-scoped ownership, `_solutions/{install_id}` storage, and
install-derived UUID mappings.

### Consistency boundary

The job stages changed files under its own object-storage prefix and builds the
database operations without exposing partial results. It applies database
changes in one transaction, promotes staged files using an idempotent finalize
record, regenerates `.bifrost/*`, refreshes indexes/caches, and marks Git dirty.

Because PostgreSQL and object storage cannot share a transaction, the job stores
enough finalize state to retry safely. A retry detects already-promoted content
by hash. If promotion fails after the database commit, the job remains failed
and retryable rather than reporting success with divergent stores.

### PlatformJob

`workspace.bundle_import` has:

- one workspace resource lock shared with workspace Git mutation;
- encrypted payloads when a staged package may contain protected backup data;
- bounded concurrency and a memory-headroom policy;
- progress phases for loading, validating, applying entities, promoting files,
  regenerating manifests, and finalizing indexes;
- cancellation only before the commit/finalize boundary;
- deduplication by preview token and decision-set hash.

The API returns the ordinary PlatformJob public contract. The CLI polls
`/api/platform-jobs/{job_id}`; the browser uses the notification WebSocket.

### Memory requirements

Upload spooling, staged-object copies, ZIP entry extraction, hashing, and file
promotion use bounded chunks. The planner may retain manifest metadata and
compact hashes in memory, but never all package file bytes or the full `_repo`
tree. Tests measure peak RSS for a large synthetic bundle and enforce a fixed
overhead rather than permitting growth proportional to payload size.

## CLI Contract

The CLI supports the same two-phase protocol:

1. `bifrost solution import-workspace <zip> --preview` prints creates,
   unchanged items, conflicts, match keys, and the compatibility warning.
2. Interactive terminals collect per-item Keep/Replace decisions and offer
   keep-all/replace-all.
3. Automation supplies a decision file or an explicit `--keep-all` or
   `--replace-all`; unresolved conflicts fail closed.
4. The accepted PlatformJob is followed through the shared job endpoint.
5. Completion prints: imported content is uncommitted; review the diff, verify
   references and compatibility, run tests, then commit or discard it.

Machine-readable output contains stable item IDs, classifications, decisions,
target IDs, warnings, job ID, and terminal result. An agent must be able to
review compatibility rather than infer success from an exit code alone.

## Git Sync Audit and Required Hardening

The current Git path has good manifest round-trip coverage, but its write order
is unsafe for reuse:

- `desktop_sync()` pushes remote and uploads `_repo` before entity import can
  fail.
- deletion confirmation happens after push, storage upload, and import.
- a confirmation-needed result can still be treated as a successful sync and
  clear dirty state.
- status calculation stages files and may mutate conflict state.
- full-tree helpers buffer file bytes before filtering.
- `workspace.git` is a PlatformJob wrapper around a legacy scheduler handler
  and parallel Redis/WebSocket result protocol.
- repository storage and locking are effectively global while configuration
  accepts organization context.

Before bundle import ships, Git mutation becomes a direct PlatformJob handler
with one phase model and result contract. Sync performs fetch/merge and a full
dry-run entity/deletion plan before any irreversible remote push. Confirmation
is a terminal `requires_action` outcome that does not clear dirty state. Status
is read-only. File traversal hashes/streams bounded chunks. Repository scope is
made explicit and enforced; the first implementation remains one workspace
repository rather than pretending to support independent organization repos.

## Git Connection and Developer Workflow

### Workspace repository

The CLI names operations by what they do:

- `bifrost git status`: read-only working tree, ahead/behind, and conflicts.
- `bifrost git fetch`: update remote refs without applying them.
- `bifrost git sync`: fetch, reconcile, validate, commit/push if requested, and
  apply the resulting manifest. The current combined `push` behavior moves here.
- `bifrost git resolve`: choose ours/theirs for a listed conflict.
- `bifrost git abort-merge`: restore the pre-sync merge state.
- `bifrost git connect`: preview and execute first-time reconciliation.

First connection never silently overwrites either side. It classifies local
only, remote only, identical, and conflicting content, then asks whether to
start from remote, publish local, or reconcile. A detached workspace with dirty
changes is normal: those changes are shown as local-only/different and remain
reviewable. Connecting does not imply accepting remote replacements.

`git push` may remain temporarily as a documented alias for `git sync` only if
the compatibility decision is made explicitly during implementation; no hidden
fallback path is added.

### Managed Solutions

Managed Solution Git remains independent from workspace Git and preserves the
one-writer invariant. The missing operator surfaces are added:

- install a managed Solution directly from a repository;
- connect or disconnect an existing locally installed Solution;
- preview the first pull from the configured main/ref;
- update from the configured ref with explicit conflict/refusal output.

The existing `solution pull` command, which materializes captured manifests,
is renamed to describe that action; it is not overloaded with Git update
semantics. A compatibility alias is a separate product decision.

Loose workspace imports never gain their own Solution Git connection. They use
workspace Git after import.

## Testing and Acceptance

- Unit tests cover classification, natural-key matching, destination-ID
  preservation, typed reference rewriting, decision validation, and runtime
  state preservation.
- E2E tests cover create/keep/replace combinations, no stale deletions,
  `_repo` files plus manifest regeneration, dirty Git state, job retry/finalize,
  and concurrency with workspace Git mutation.
- Memory tests exercise a large ZIP with large files and assert bounded peak
  RSS.
- Component tests cover dense rows, bulk decisions, inspector selection,
  unresolved gating, keyboard operation, and scroll ownership.
- Playwright covers one complete workspace import through PlatformJob
  completion and resulting Git diff.
- Git tests cover preflight-before-push, deletion confirmation without dirty
  clearing, read-only status, abort, first-connect reconciliation, and CLI
  conflict messages.

## Delivery Sequence

1. Git synchronization consistency and PlatformJob consolidation.
2. Workspace bundle planner, import job, API, CLI, and compact review UI.
3. Workspace/Solution Git connection commands and first-connect UX.

Each project has its own implementation plan and produces independently
testable behavior. The workspace import project may be developed in parallel
with Git UX, but it does not ship until the Git mutation boundary from project
1 is in place.
