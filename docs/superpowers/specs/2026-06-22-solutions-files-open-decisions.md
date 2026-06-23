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

`resolve_s3_key(location, scope, path)` (`api/shared/file_paths.py`) already composes
`{location}/{scope}/{path}`. A solution's files use **`scope = str(install_id)`** instead of
`scope = str(org_id)`. No new top-level `solution/` prefix, no change to `resolve_s3_key`.

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
- Re-import writes via `backend.write(path, content, location=…, scope=str(install_id))`.
- The field-class `classify()` machinery does **not** apply — files aren't DB columns.

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

## Genuinely open decisions

### O1 (biggest): shipped files vs. runtime/user files on update

Deploy is **full-replace** for entities (`DELETE … WHERE solution_id == sid AND id NOT IN bundle`).
Applied naively to S3, a solution **update would wipe customer-generated files**.

Solution files therefore split into two classes:
- **Shipped** — part of the bundle (seed data, templates). Replaced on update, like entities.
- **Runtime/user** — created post-install by the solution's users/workflows. Must **survive** an
  update untouched.

Open: how to distinguish them. Options —
1. **Sub-prefix split:** `{location}/{install_id}/_shipped/…` vs `{location}/{install_id}/…`.
   Update full-replaces only under `_shipped/`. Simple, visible, no metadata needed.
2. **Metadata flag:** `FileMetadata.source = shipped|runtime`. Update sweeps only `shipped`.
   Flexible, but relies on the guard/Core-write discipline and a correct flag on every write.
3. **Manifest-listed shipped set:** the bundle names its shipped paths; update replaces exactly
   that set, never touches anything else. Most precise; bundle carries the authority.

Recommendation to explore first: **(1) sub-prefix** — it makes the invariant structural (you can't
accidentally full-replace user data because it's not under the swept prefix) and needs no flag
plumbed through every write. Decide before any implementation.

### O2: presigned-URL scope (correctness hole if wrong)

The Files SDK mints presigned S3 URLs that **bypass the policy layer** (raw S3). A solution's
presign must be minted against `{location}/{install_id}/…` and must **not** be mintable for the org
pool. The install scope has to be baked into the presign at mint time, or a solution can presign a
cross-scope read the cascade never sees. Treat as a security-review item, not a nicety.

### O3: S3 orphan sweep on uninstall

Entity uninstall is DB-cascaded; S3 is not. Uninstall must explicitly sweep
`{location}/{install_id}/` across every location the install used (and the `FileMetadata` rows, via
Core delete). Without it: leaked, still-billed objects + dangling metadata. Table data avoids this
(DB cascade); files cannot.

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
   solution pattern). Core-write only.
2. Own-first file resolver: file endpoints accept a solution scope; `WHERE solution_id == scope OR
   IS NULL`; precedence per D4.
3. Policy cascade precedence own-solution→org→global; thread solution scope into
   `resolve_policy_refs` (D5).
4. Decide O1 (shipped vs runtime) → bundle capture/install of file bytes under `include_data` (D3),
   modeled on `_table_data`.
5. Uninstall S3 sweep (O3) + presign scope hardening (O2).
6. App SDK auto-scoping (O4).

Each is a real slice; O1 gates 4. None block the named-policy-rules plan, which ships independently.

---

## Carry-over invariants (don't relearn these)

- Solution-managed rows: **Core writes only**; install the read-only guard in tests to be
  prod-faithful. [[project_solution_managed_guard_deploy_core]]
- Bytes ≠ rows: no DB cascade, no field-class export — files need their own enumerate/serialize/
  reimport pair and their own uninstall sweep.
- `install_id` is `Solution.id` is `solution_id`; it is the file scope.
- Content isolation is structural (prefix); only policy cascades. [[project-file-policies-cascade]]
