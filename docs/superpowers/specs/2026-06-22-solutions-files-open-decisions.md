# Solutions + Files — Open Decisions (design sketch)

**Date:** 2026-06-22
**Status:** Sketch / not a spec. Captures the design surface for hooking the Files SDK + file
policies into Solutions, the decisions already implied by existing machinery, and the genuinely
open questions. **Not scheduled** — the named-policy-rules plan
(`docs/superpowers/plans/2026-06-22-named-policy-rules.md`) remains the next executable work.
This doc exists so the Solutions+Files thinking isn't lost.

**Branch:** `codex/files-sdk-policies`. Companion to
`docs/superpowers/specs/2026-06-22-named-policy-rules-design.md`.

---

## Why this is bigger than it looks

Files are the first Solution-managed asset whose **content does not live in the DB** (S3 only).
That breaks two assumptions the rest of Solutions relies on:

- Isolation is enforced by a `solution_id` **column** + the org-scoped cascade. S3 has no column
  and no cascade — isolation must be encoded in the **key prefix**.
- Uninstall is a DB `DELETE WHERE solution_id == sid` (cascades to children). S3 objects have no
  cascade — they must be swept explicitly.

The closest existing precedent is **table row data** (`Document` rows), which also don't travel in
the entity shape and ride the bundle only under `include_data`. Files should mirror that precedent,
not the field-class export model (which is for DB columns only and never touches files).

---

## What the existing machinery already decides

These are not open — the current code forces them.

### D1. Prefix = `{location}/{install_id}/{path}` — install_id IS the scope

`resolve_s3_key(location, scope, path)` (`api/shared/file_paths.py`) composes
`{location}/{scope}/{path}` **for freeform locations only** — the reserved locations differ
(`workspace`→`_repo/{path}` unscoped, `uploads`→`uploads/{scope}/…`, `temp`→`_tmp/{scope}/…`,
`file_paths.py:72`). Solution files therefore live under a **freeform** location, where a solution's
files use **`scope = str(install_id)`** instead of `scope = str(org_id)`. No new top-level
`solution/` prefix, no change to `resolve_s3_key`. (Solution files must NOT use `workspace`, which
is unscoped and maps straight to `_repo/` — there'd be no install isolation.)

- `install_id` is the `Solution.id` (`api/src/models/orm/solutions.py`) — the same UUID stamped as
  `solution_id` on every managed entity. There is no separate install_id concept.
- Because a solution is unique per scope (partial unique indexes on `(slug, org)` / `(slug, global)`),
  `install_id` collapses org-scope + solution-isolation into one value. Confirms the original
  instinct: "no scope needed — solutions are always in a specific scope."

### D2. `solution_id` on `FileMetadata`/`FilePolicy` → forces Core writes

The moment these rows carry `solution_id`, the always-on `before_flush` read-only guard
(`api/src/services/solutions/guard.py:63`, installed at `core/database.py:134`) rejects any ORM
mutation/delete of them outside deploy.

- Deploy/install must write file metadata + policy rows via **Core `insert()/update()`** (the
  `deploy.py` pattern), never ORM `add`/dirty/delete. See [[project_solution_managed_guard_deploy_core]].
- The runtime file-write path must not let a solution mutate its own *managed policy* rows through
  the ORM (it would 500 in prod, false-green in isolated unit tests — install the guard in tests).

### D3. File content export = the `table_data` analog, `include_data`-gated

`capture.py::_table_data` is the template: enumerate by `solution_id`, key by name/relative-path,
cap with a **loud** warning (`TABLE_ROW_CAP`), omit-empty, ride the bundle **only under
`include_data`**. File **bytes** follow the identical rule:

- A new `enumerate_solution_files(install_id, location)` lists `{location}/{install_id}/` via the
  FileBackend `list()` and reads each via `read()`.
- A `SolutionBundle.solution_files: dict[str, bytes]` (or `{location: {path: bytes}}`) field, keyed
  by relative path, present only when `include_data=True`.
- Re-import writes via `backend.write(path, content, location=…, scope=str(install_id))`, honoring
  the **replace/skip** choice (D6) — **no mirror-delete on import or update** (O1).
- The field-class `classify()` machinery does **not** apply — files aren't DB columns.

### D6 (DECIDED). Mass file operations run in a background job, never inline

Any operation that touches **many** files — restoring files from a bundle (install/update with
`include_data`), deleting a folder above a threshold (`> N` files), an uninstall S3 sweep (O3) —
**must run as a background job**, not synchronously in an HTTP request (browser or CLI). A single
request may not fan out into hundreds/thousands of S3 calls.

- The request **enqueues** a job and returns a job/operation id; the client polls/streams progress
  (mirror the existing async-deploy UX — see [[project_cli_solution_ux_plan]] for the
  observable-deploy pattern already shipped for solution deploy).
- Threshold for "mass": a folder delete (or any bulk op) over a fixed `FILE_BULK_INLINE_CAP` runs
  as a job; under it may run inline. Bundle restore is **always** a job regardless of count.
- This is consistent with how solution **deploy** already works (async, observable). File bulk ops
  are the same class of operation and get the same treatment.
- Implication for O1/O3: replace-vs-skip merge and the uninstall prefix sweep are **job bodies**,
  not request handlers. The replace/skip *choice* is captured at enqueue time and carried into the
  job.

### D4. Policy resolution gains a second cascade axis — precedence must be declared

File policies already cascade **org→global** (`FilePolicyService.load_policy`). Solutions add a
**solution→non-solution** axis (the workflow own-first model). The combined precedence is **not
implicit** — declare it:

> **own-solution policy → org policy → global policy.**

A solution ships its own prefix policies (solution-managed, locked); for a path it does not cover,
resolution falls back to the org/global cascade. Mirrors `workflows.py:124-136`
(`WHERE solution_id == scope OR solution_id IS NULL`).

### D5. Named `PolicyRule` (the planned work) composes for free — at the cost of a 4th axis

If `PolicyRule` follows the standard pattern it gains `solution_id`, resolves own-first, and a
solution's file policy `{"$ref": "ops"}` resolves to the **solution's** rule before the global one.
Clean — but `resolve_policy_refs` must then take the solution scope (a fourth resolution axis:
file/table domain × org/global × solution/non-solution). Real integration cost; not free in code
even though it's free in concept.

---

## Decisions taken here + still-open questions

O1 (update semantics) is **decided** below — kept in this section for context. O2–O4 remain open;
O5 is a deliberate "no." (D6, the mass-ops-as-jobs rule, is also decided — recorded up in the
"already decides" block since it's forced by existing infra.)

### O1 (DECIDED): file update is copy-paste merge, not full-replace

**Decision (2026-06-22).** Files do **not** follow the entity full-replace/mirror-delete model.
On a solution update, shipped files apply like a **copy-paste into a folder**:

- The installer chooses **once, for the whole bundle**: **Replace existing** (overwrite files at
  colliding paths) or **Skip existing** (only write files whose path doesn't already exist). No
  per-file prompting; it's an "apply to all" choice.
- **No mirror-delete, ever.** If bundle v2 omits a file v1 shipped, the v1 copy **stays** in the
  install. Update never deletes. Cleaning up a dropped file is the installer's manual task if they
  want it.
- **No shipped-vs-runtime tracking needed.** Because update never deletes, runtime/user-created
  files are never at risk regardless of prefix — the distinction that motivated the original
  sub-prefix/flag options is **moot**. The only real collision (a shipped file landing on an
  existing path) is handled by the replace/skip choice. This is strictly simpler than any
  tracking scheme.
- **No mirror on *import* either.** Same rule for a fresh install from a bundle: write the bundle's
  files; if something already occupies a path, honor the replace/skip choice. Import does not
  reconcile-delete.

Consequence to accept: a long-lived install accumulates orphaned shipped files across versions
(dropped-but-never-removed). That's the deliberate trade for safety + simplicity — never risking
user data beats automatic tidiness. Uninstall (O3) still sweeps the whole install prefix, so
orphans don't outlive the install.

> Contrast with entities: entity deploy **does** mirror-delete (`id NOT IN bundle`) because entity
> identity is a UUID the bundle owns end-to-end. File identity is a user-meaningful *path* that the
> install's own users also write to — so the safe default flips to never-delete-on-update.

### O2: presigned-URL scope (the highest-leverage correctness item)

**The mechanism.** For large files the SDK doesn't stream bytes through the API — it asks the API
to **presign** an S3 URL: a URL carrying an S3 signature that grants the *bearer* direct
`GET`/`PUT` on **one specific S3 key** for a few minutes. The client then talks straight to
SeaweedFS. The decisive property: **once minted, the presigned URL is the authority.** S3 honors
the signature and consults **no** `FilePolicy`, `organization_id`, or `solution_id` — the entire
policy layer lives in the API, and a presigned URL routes *around* it by design.

This is already true for non-solution files and already fine, because the API checks the policy
**before** signing and signs a key **inside the caller's own scope**. The presign is safe *only
because the key it points at was scope-checked at mint time.*

**Why Solutions breaks it.** `{location}/{install_id}/{path}` creates sibling scopes that differ
only by the UUID in the middle:

```
shared/{org_id}/financials.xlsx        ← org pool
shared/{installA}/financials.xlsx      ← solution A
shared/{installB}/financials.xlsx      ← solution B
```

The hole opens whenever the **signed key derives from client input** instead of the
**server-resolved** scope. Three failure modes:

1. **Client picks the scope.** If the presign endpoint accepts `scope` (or a raw `path` containing
   the scope segment) and signs what it's handed, a caller in solution A requests `scope=<org_id>`
   or `scope=<installB>` and gets a valid presigned URL into a pool it must never touch. The
   cascade never runs on the signed key — it's in the API; the bytes come from S3. Silent
   cross-scope access.
2. **Traversal across the boundary.** Even with `scope` server-pinned, a `path` of
   `../../{org_id}/financials.xlsx` re-targets the key out of the install prefix. `resolve_s3_key`
   already rejects traversal (`_validate_path`) — **but only if the presign path routes through
   that same resolver.** A string-concat key-builder re-opens it.
3. **PUT presigns are worse than GET.** A mis-scoped write-presign lets a solution *plant* a file
   in the org pool or a sibling install, which then resolves for other readers. Cross-scope
   **write**, not just read.

**The fix — server-resolved scope, baked in at mint time:**

- The presign endpoint **never accepts a scope / install_id from the client.** It derives the scope
  from the authenticated context: a solution app's context carries `solution_id` (the install),
  exactly as `sdk.tables` derives `?solution=` today (see O4). Server computes
  `scope = str(install_id)` (or the user's `org_id` for non-solution callers). The client cannot
  name the scope.
- The key is built by the **same `resolve_s3_key(location, scope, path)`** used by read/write/list,
  so traversal validation + prefix composition are identical. **No second key-builder.**
- The policy check (`is_allowed` — `read` for GET, `write` for PUT) runs **before** signing,
  against the resolved key's `(location, scope, path)`. Signing is the **last** step, only on
  success.
- Result: a presigned URL is only ever valid for a key inside the caller's resolved scope. A
  solution **physically cannot** obtain a signature for `{org_id}` or a sibling install — not
  because a policy denies it, but because the API never signs anything outside the scope it
  resolved from the token.

**Why it's a security-review item, not a nicety.** Every other Solutions isolation is enforced in
the API after the fact (guard, cascade, `solution_id` filters). The presign is the **one** path
where bytes leave through a door the API doesn't stand in front of afterward. Get the mint-time
scope wrong and the FilePolicy / cascade / guard work is **bypassed by construction** for presigned
access. Highest-leverage correctness item in the files-in-solutions surface — must be on the
security-review checklist with explicit tests for all three failure modes (client-scope override,
traversal, PUT into a foreign scope).

### O3: S3 orphan sweep on uninstall (runs as a job — D6)

Entity uninstall is DB-cascaded; S3 is not. Uninstall must explicitly sweep
`{location}/{install_id}/` across every location the install used (and the `FileMetadata` rows, via
Core delete). Without it: leaked, still-billed objects + dangling metadata. Table data avoids this
(DB cascade); files cannot.

Because the sweep is unbounded in size, it **runs as a background job** (D6), enqueued by the
uninstall flow — not inline. The whole-prefix sweep is what makes O1's never-delete-on-update
orphans safe: they don't outlive the install.

### O4: app-relative auto-scoping (ergonomics)

v2 apps already carry `solution_id` in context and `sdk.tables` auto-appends `?solution=`. The
Files SDK from inside a solution app should auto-scope to the install so an author writes
`files.write("reports/x")` and it lands in `{location}/{install_id}/reports/x` without passing a
scope. Without this, every solution app hand-threads the install_id and gets it wrong. Mirror the
tables `?solution=` mechanism + the own-first resolver on the file read/write/list/exists/signed-url
endpoints.

### O5: default content-read fallback — explicitly NO

A solution reading the **org's** `shared/` content pool by cascade is a capability to grant
explicitly, not a default. Content isolation stays hard (prefix-keyed); only **policy** cascades.
Flagged so a future implementer doesn't "helpfully" add content fallback to match the policy
cascade.

---

## Rough shape if/when scheduled (not a plan)

1. Add `solution_id` to `FileMetadata` + `FilePolicy` (+ migration, partial unique indexes per the
   solution pattern). Core-write only (D2).
2. Own-first file resolver: file endpoints derive solution scope from context; `WHERE solution_id
   == scope OR IS NULL`; precedence per D4.
3. Policy cascade precedence own-solution→org→global; thread solution scope into
   `resolve_policy_refs` (D5).
4. Bundle capture/install of file bytes under `include_data` (D3), modeled on `_table_data`, with
   **replace/skip merge, no mirror** (O1) — restore runs as a **job** (D6).
5. Uninstall S3 sweep as a **job** (O3, D6) + presign scope hardening + the three presign tests
   (O2).
6. App SDK auto-scoping (O4).
7. Background-job plumbing for mass file ops (D6): enqueue + observable progress (reuse the
   async-deploy pattern); `FILE_BULK_INLINE_CAP` threshold for folder deletes.

Each is a real slice. None block the named-policy-rules plan, which ships independently.

---

## Carry-over invariants (don't relearn these)

- Solution-managed rows: **Core writes only**; install the read-only guard in tests to be
  prod-faithful. [[project_solution_managed_guard_deploy_core]]
- Bytes ≠ rows: no DB cascade, no field-class export — files need their own enumerate/serialize/
  reimport pair and their own uninstall sweep.
- `install_id` is `Solution.id` is `solution_id`; it is the file scope.
- Content isolation is structural (prefix); only policy cascades. [[project-file-policies-cascade]]
- File update is **copy-paste merge (replace/skip), never mirror-delete** — entities mirror, files
  don't, because a file's identity is a user-writable path. Same rule on import.
- **Mass file ops are jobs, never inline requests** (bundle restore always; folder delete above a
  cap; uninstall sweep). Reuse the observable async-deploy pattern. [[project_cli_solution_ux_plan]]
