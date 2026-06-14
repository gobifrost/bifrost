# Solution Export/Import Portability — design

**Date:** 2026-06-14
**Branch:** `worktree-solutions-success-criteria` (draft PR #347 — stays draft, no merge)
**Status:** approved (brainstorm with Jack, 2026-06-14)

## Why

Jack's release framing: *"Once export/import is fixed, we've solved what we set out
to solve, then probably tune the GitHub integration."* Migration mechanics are built and
battle-tested; the remaining release-critical work is making a Solution genuinely
**portable** — export EVERYTHING the solution currently owns, import puts EVERYTHING back —
plus a clear secrets/config story.

Three coupled problems, designed here as one arc:

1. **Core round-trip** — export is a stale cached zip today (the "export missing apps"
   bug); fix it to rebuild live, and verify import materializes the whole solution in a
   fresh org.
2. **Secrets / config on import** — how an admin supplies required secret/config values,
   plus a gated way to carry values *in* the export.
3. **Include data** — a gated opt-in to carry table rows, not just schema.

**Explicitly out of scope (Jack, 2026-06-14):** per-solution file storage ("scope creep").
Storage stays org-scoped. GitHub-integration reconciliation is a *later, separate*
brainstorm.

## Verified starting facts (checked against code, not assumed)

- **Export is a stale cached snapshot.** `GET /api/solutions/{id}/export`
  (`api/src/routers/solutions.py:146`) reads a stored zip via `SolutionExportStore().read`.
  That zip is only (re)written on `capture`/`deploy`. So export reflects solution state *as
  of the last capture/deploy*, not what it currently owns — hence apps captured later go
  missing. **The bundler is fine** (`build_workspace_zip` already handles apps —
  `export.py:138-155`); only the endpoint is stale.
- **The bundle is already complete for code/schema.** `capture._bundle_for(solution)` →
  `SolutionBundle` already gathers workflows, tables (schema), apps, forms, agents, config
  *declarations* + the module import closure. `build_workspace_zip` serializes it.
- **Install already does the atomic apply.** `zip_install.install_zip` runs the proven
  `lock → deploy (full-replace) → apply config values → commit` sequence and writes provided
  `config_values` after finalize but before lock release, so an install never exists without
  its just-entered secrets. It does NOT reinvent deploy.
- **Config values are instance-owned.** `Config.value` (JSONB), org-scoped. Capture records
  only a `SolutionConfigSchema` *declaration* (key/type/required) — never the value. Config
  values are excluded from the destructive deploy sweep (they survive re-deploy).
- **Encryption primitives already exist** in `api/src/core/security.py`: `derive_fernet_key`
  (HKDF from an explicit secret) and `decrypt_with_key` ("Used during import to decrypt
  values encrypted by a different instance"). **`decrypt_with_key` is currently dead code** —
  a leftover from the REMOVED old `export`/`import`. This design revives and uses it
  legitimately (so it stops being dead code); we clean up / repurpose as we go.
- **Storage is org-scoped**, `{location}/{scope}/{path}` with `scope = org_id`
  (`api/shared/file_paths.py`). `solution_id` is NOT on the file-op path. (Relevant only to
  confirm per-solution storage is genuinely out of scope.)

## Decisions (locked in the brainstorm)

| # | Decision |
|---|----------|
| D1 | Export rebuilds **live** from currently-owned entities at request time. Drop the stored-zip read. |
| D2 | UI button **"Export Workspace" → "Export Solution"** = "everything in this solution, right now." |
| D3 | **Two named export modes:** **Shareable** (code + schema + declarations, no password, publicly safe) and **Full backup** (Shareable + secret values + table data, password-required). |
| D4 | Full backup puts ALL sensitive content in **one Fernet-encrypted blob** in the zip, key derived from the user's password via existing `derive_fernet_key` (HKDF). Code stays plaintext. |
| D5 | **Unified import contract:** code/schema **always full-replaces**; content (secrets+data) only exists in a Full backup and is **prompt-to-replace per content-type**, only when present AND colliding. Empty slots fill silently. Shareable never prompts about values. |
| D6 | **Set-on-install** for Shareable bundles: prompt for required-unset configs at install (UI form / CLI `--set`), skippable; unset-required surface as a **Setup checklist** in the management UI and editable later; solution carries an **`incomplete`** status until filled; runtime fails loudly (`set config <key>`) on a missing required value. |
| D7 | Architecture **A**: extend the existing bundle/`install_zip` path. No parallel backup/restore service, no second import path. |
| D8 | Include-data is **per-table, all-or-nothing within a table** (wholesale row replace), matching how deploy treats schema. No row-level merge (YAGNI). Table **schema** is already full-replaced by the deploy step *before* data is applied, so data always lands against the imported schema; rows are written to the just-deployed table definition (a column the export's rows don't fill is left at its default/empty). |
| D9 | Per-solution storage and GitHub-integration reconciliation are **out of scope** for this arc. |

## Architecture

```
EXPORT  GET /api/solutions/{id}/export?mode=shareable|full[&password=…]
  └─ capture._bundle_for(solution)                # live, currently-owned entities
       └─ build_workspace_zip(bundle, *, secrets=None|EncryptedContent)
            ├─ plaintext: apps source, workflows, modules, tables schema,
            │             forms, agents, .bifrost/configs.yaml (declarations)
            └─ mode=full only: .bifrost/secrets.enc   # Fernet(password-derived key)
                 ├─ config values   { key: value, … }
                 └─ table data      tables/{name}.jsonl rows

IMPORT  bifrost solution install <zip> [--password] [--set k=v] [--replace-secrets] [--replace-data]
        (UI: import button + setup form + per-type replace prompts)
  └─ zip_install.install_zip(...)                 # proven: lock → deploy → apply → commit
       ├─ deploy: full-replace code/schema        # unchanged contract
       ├─ if .bifrost/secrets.enc present:
       │    ├─ decrypt with password (clear error on wrong pw / undecryptable)
       │    ├─ config values → fill empty slots; collide → prompt/--replace-secrets
       │    └─ table data    → fill empty tables; collide → prompt/--replace-data
       └─ required-unset configs → solution.status = incomplete + Setup checklist
```

### Components & responsibilities

- **`solutions.py::export_solution`** — replace stale-store read with live rebuild; accept
  `mode` + `password`; stream the zip. (Touch: ~the endpoint body only.)
- **`capture._bundle_for` / `export.build_workspace_zip`** — gain an optional encrypted
  content section. `SolutionBundle` grows optional `config_values` + `table_data` carriers
  that are populated only for `mode=full`. Serialization writes `.bifrost/secrets.enc`.
- **`security.py`** — reuse `derive_fernet_key`; add the symmetric `encrypt_with_key` if
  missing (mirror of `decrypt_with_key`); both now live (no dead code).
- **`zip_install.install_zip`** — after deploy/finalize, if the bundle carries an encrypted
  blob, decrypt and apply: config values + table data, with per-type collision handling.
  Extends the existing `_apply_config_values` step; adds `_apply_table_data`.
- **Contracts** (`src/models/contracts/solutions.py`) — export query params; install request
  gains `password`, `replace_secrets`, `replace_data`; a `SolutionStatus` incomplete value
  and a "required-config setup" response shape for the management UI.
- **Management UI** — rename button to "Export Solution"; export mode dialog (the
  two-radio + password layout); import flow (password prompt → per-type replace prompts);
  Setup checklist tab driven by required-unset configs + the `incomplete` status badge.
- **CLI** (`commands/solution.py`) — `export --mode`/`--password`; `install --password`
  `--replace-secrets` `--replace-data` (non-interactive: colliding-without-flag refuses).
- **Cleanup** — remove `SolutionExportStore` write-on-deploy and the store read path once
  export is live and nothing else consumes it (verify first). Repurpose the revived
  encryption helpers.

### Data flow — the encrypted blob

`.bifrost/secrets.enc` is `base64(Fernet(key).encrypt(json))` where `key =
derive_fernet_key(password)` and the JSON is:

```json
{
  "version": 1,
  "config_values": { "provisioning_api_key": "…", "default_region": "us-east" },
  "table_data": { "widgets": "<jsonl-or-inline rows>" }
}
```

The manifest (`.bifrost/solution.yaml` or equivalent) records `mode: full` and the present
content types so import can decide what to prompt about *before* asking for the password
(better UX: "this bundle contains secret values and table data — password required").

### Error handling

- **Wrong / missing password on a Full bundle** → clear 400 / CLI error, no partial apply
  (decrypt happens before any content write; deploy of code/schema can proceed independently
  only if that's desired — default: refuse the whole import until decrypt succeeds, since a
  Full backup's intent is to restore content).
- **Collision without a decision** (CLI, no `--replace-*`) → refuse with the exact keys/tables
  that would be clobbered. Never silently overwrite a set secret or existing rows.
- **Missing required config at runtime** → loud error naming the key and how to set it.
- **Undecryptable on this instance** — since the key is password-derived (not instance-key
  derived), any instance with the password can decrypt; the cross-instance case Just Works.
  (Contrast the old removed system which keyed off instance secret — that's why
  `decrypt_with_key` took an explicit key.)

## Testing

- **Unit** — `_bundle_for` includes apps/workflows/tables/forms/agents/declarations for a
  live solution (regression for the stale-export bug); encrypt→decrypt round-trip of the
  content blob with a password; wrong-password fails cleanly; bundle manifest records mode +
  content types; collision logic (empty fills, collide refuses/prompts) as pure functions.
- **E2E** — **the round-trip**: build covi-csp-shaped solution in debug → `export --mode full
  --password …` → install into a FRESH org → app + workflows + modules land, config values +
  table rows restored, app renders. Plus: Shareable export → install → Setup checklist lists
  required-unset configs, `--set` fills them, status flips off `incomplete`. Plus: re-import
  collision → `--replace-secrets`/`--replace-data` honored, absence refuses.
- **Client** — export dialog (mode radio + password gating), import setup form, per-type
  replace prompts, `incomplete` status badge + Setup tab. Vitest for any new `lib`/`services`
  modules; Playwright happy-path for export→import.
- Run via `./test.sh` (never two concurrent in this worktree); full pre-completion
  verification (pyright/ruff/tsc/lint + tests) before claiming done.

## Build order (phases)

1. **Core round-trip (the floor):** live export rebuild + "Export Solution" rename + verify
   import materializes a Shareable bundle end-to-end (round-trip E2E). Remove stale store.
2. **Secrets/config on import:** set-on-install (UI form + CLI `--set`), Setup checklist +
   `incomplete` status, loud runtime error.
3. **Full backup:** encrypted content blob (config values), export mode + password, import
   decrypt + per-type replace.
4. **Include data:** table rows into the blob, per-table wholesale replace.

Each phase is independently shippable and testable; phase 1 is the release-critical bug fix.

## Out of scope / deferred

- Per-solution file storage (org-scoped storage stays as-is).
- GitHub-integration ↔ portability reconciliation (separate brainstorm; what's the source of
  truth, does export round-trip through git, how does a git-connected install relate).
- Row-level data merge (only wholesale per-table replace).
- Streaming/pagination protocol for very large table exports (cap + row-count preview in v1).
- Import button on the solutions list + per-solution "Update" change-preview (adjacent inbox
  item; can fold in opportunistically but not required here).
```
