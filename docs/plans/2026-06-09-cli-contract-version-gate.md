# CLI Contract-Version Gate

**Date:** 2026-06-09
**Branch:** `worktree-cli-contract-version-gate`
**Status:** Plan — not yet implemented (revised after Codex adversarial review, see "Review findings folded in")

## Problem

The CLI version gate (`api/bifrost/cli.py::_check_cli_version`) hard-blocks **every**
command when the installed CLI's build version differs from the deployed server's
build version (exact string equality against `GET /api/version`). Two issues:

1. **Over-blocking.** A server release for an unrelated reason (docs, client-only
   change, infra) bumps the server version string and force-reinstalls every
   user's CLI, even when nothing the CLI actually talks to changed. The gate
   should fire on **contract** changes, not on any version drift.

2. **Silent pass-through on un-reachable verdict.** The check swallows *all*
   exceptions to `logger.debug` (cli.py:260-264). If `/api/version` times out,
   gets 403'd by a WAF/CDN, or returns malformed JSON, the gate silently passes
   and a stale CLI proceeds against an incompatible server — failing downstream
   in a confusing way. This is the "fails silently" symptom seen with
   `bifrost workflows execute` (which IS gated — every command routes through
   `main()` → `_check_cli_version()` at cli.py:594 before dispatch — but the gate
   can give up without saying so).

## Design overview

Split into **two independent gates**:

### Gate 1 — Contract compatibility (HARD, always, every command)

A hand-maintained integer `CONTRACT_VERSION`, defined on both server and CLI.
- Server returns it at `GET /api/version` as `contract_version`.
- CLI bakes its own `CONTRACT_VERSION`.
- **Mismatch → hard-fail every command** (no read-only carve-out — user chose
  "keep blocking everything") with the required-update message.
- **Old-server fallback (REVISED — soften, don't hard-block):** if
  `/api/version` lacks `contract_version` (server predates this feature) we
  **cannot** know contract compatibility. Hard-blocking on exact build-version
  equality here is a rollout footgun — a freshly-installed new CLI hitting a
  not-yet-upgraded server will almost always differ in build version and get
  blocked even when compatible. So the old-server branch emits a **soft stderr
  warning** ("can't verify contract compatibility — server predates contract
  versioning") and **does not block**. The hard block is reserved for a
  *confirmed* contract-integer mismatch. (Changed from the original plan, which
  kept today's hard version-string exit — Codex review #2/#4.)

### Gate 2 — Build-version drift (SOFT, deduped)

Contract matches but build version differs → a one-line **stderr** notice
("update available, still compatible"), **never blocks**.
- Deduped via a marker file in the OS temp dir (`tempfile.gettempdir()`),
  keyed by `crc32(normalized_url)` (NOT built-in `hash()` — see files table /
  Codex #3) + holding the last-notified server version. CRC32 (not a crypto
  hash) keeps CodeQL's sensitive-data-hashing taint rule from firing on the
  credential-derived URL.
- Cross-platform: `tempfile.gettempdir()` resolves on Linux (`/tmp`), macOS
  (`/var/folders/...`), Windows (`%TEMP%`) with one code path.
- Fires **once per new server version per URL**, then goes quiet. (We rejected
  a PPID-keyed "once per shell" marker — no portable parent-shell identity on
  Windows cmd/PowerShell.)

### Q2 fix (folded in)

When the version check **cannot reach a verdict** (network error, 403, malformed
JSON, missing `version` field), emit a **visible one-line stderr warning**
instead of `logger.debug`. The "can't tell" state must not be silent. (It still
does NOT block — only a confirmed contract mismatch blocks.)

## Why `CONTRACT_VERSION` can't be silently missed on a breaking change

This is the crux. We do **not** rely on a human/agent remembering to bump an
integer.

**Correction (Codex review #1c):** an earlier draft claimed
`COVERED_DTOS` in `test_dto_flags.py` "already imports both DTO sides." That is
**false** — `COVERED_DTOS` imports almost all DTOs from the **server canonical**
side (`src.models.contracts.*`), only `CustomClaim*` from the CLI mirror. The
*separate* `test_contracts_parity.py` pairs both sides but compares only
`model_fields` **names, not types**. So neither existing test gives us a
both-sides, type-aware fingerprint for free. The tripwire must build its **own**
fingerprint set.

**Fingerprint set (`CONTRACT_FINGERPRINT_MODELS`)** — defined explicitly in the
tripwire test, covering everything the CLI actually depends on:
- All CRUD DTOs the CLI sends (the current `COVERED_DTOS` set).
- **`WorkflowExecutionRequest` / `WorkflowExecutionResponse`**
  (`src/models/contracts/executions.py`) — the `bifrost workflows execute` body
  and response shape. This is the command with the "fails silently" symptom, so
  it is the *most* important to cover and was the biggest hole in the first draft
  (Codex review #1a).
- The **CLI route list** — the literal paths the CLI calls (`/api/workflows`,
  `/api/workflows/register`, `/api/workflows/execute`, `/api/executions/{id}`,
  `/ws/execution/{id}`, etc.). A route rename is a breaking change the DTO
  schema alone won't catch (Codex review #6). Maintain this as an explicit
  `CLI_ROUTES` tuple folded into the fingerprint; a route rename trips it.

We add a **tripwire test** that fingerprints that set:

```python
# In/near test_dto_flags.py
EXPECTED_DTO_FINGERPRINT = "sha256:..."   # committed in the test
CONTRACT_VERSION = <imported from the shared constant>

def test_contract_version_tripwire():
    current = _fingerprint(CONTRACT_FINGERPRINT_MODELS, CLI_ROUTES)  # live, in-process
    assert current == EXPECTED_DTO_FINGERPRINT, (
        "A CLI-consumed DTO changed.\n"
        "  - If this is a BREAKING change (field removed/renamed/retyped, "
        "    route/ws-shape change the CLI relies on): bump CONTRACT_VERSION in "
        "    BOTH api/shared/contract_version.py AND api/bifrost/contract_version.py, "
        "    then update EXPECTED_DTO_FINGERPRINT below.\n"
        "  - If this is COSMETIC (description, field reorder, additive optional "
        "    field): just update EXPECTED_DTO_FINGERPRINT, leave CONTRACT_VERSION.\n"
        f"  current fingerprint: {current}"
    )
```

**Guarantee:** any change to a CLI-consumed DTO turns this test red. The PR
cannot merge without touching the test, and the failure message forces an
explicit breaking-or-cosmetic classification. The fingerprint is computed
**in-process, same Pydantic** — so we avoid the cross-machine byte-identical
schema problem that killed the runtime-hash approach. The fingerprint is a
*tripwire only*; it is never shipped or compared across machines.

**Residual gaps (documented, accepted):**
- **Mis-classification.** If someone marks a breaking change cosmetic (updates
  the fingerprint without bumping `CONTRACT_VERSION`), the gate won't catch it.
  The test flags *that a change happened*; judging *whether it's breaking* is
  human — but it's a forced, one-line, documented decision at PR time, not a
  silent omission.
- **Validator-only semantics (Codex #1b).** `model_json_schema()` catches field
  add/remove **and** type changes (`str -> int` alters the schema), but not a
  pure validator change that leaves the schema identical (e.g. tightening a
  regex in a `@field_validator`). Accepted — these are rare and rarely break the
  CLI's serialization contract.
- **Websocket payload shape (Codex #1a, partial).** `/ws/execution/{id}` emits
  **raw dicts** (`src/core/pubsub.py`), not a DTO, so the `type`/`level`/
  `message`/`status` keys the CLI reads aren't fingerprintable as a model. We
  cover the *REST* execute request/response (the high-value part) but the ws
  message keys remain uncovered. Accepted for v1; a follow-up could promote the
  ws payload to a Pydantic model and add it to the set.
- **`bifrost watch` long-running (Codex #5).** `watch` checks the gate once at
  startup, then `_watch_loop` runs indefinitely (`cli.py:2586`). A server
  contract change *after* watch starts won't be caught until restart. Accepted —
  a periodic in-loop re-check is a separate feature, out of scope here.

## Files to change

| File | Change |
|------|--------|
| `api/shared/contract_version.py` (new) | `CONTRACT_VERSION: int = 1` — server-side source of truth. |
| `api/bifrost/contract_version.py` (new) | `CONTRACT_VERSION: int = 1` — CLI-side mirror (baked into the wheel). Must equal the server constant; the tripwire test asserts equality. |
| `api/src/routers/version.py` | Add `contract_version: int` to `VersionResponse`; populate from `shared.contract_version.CONTRACT_VERSION`. |
| `api/bifrost/cli.py` (`_check_cli_version`) | Read `contract_version` from response. **If present:** compare to baked `CONTRACT_VERSION` → mismatch = hard-fail (Gate 1); equal but build version differs → soft notice via temp-dir dedupe (Gate 2). **If absent (old server):** soft stderr warning, **no block** (REVISED — Codex #2/#4). **On un-reachable verdict** (network/403/malformed): visible stderr warning, no block (Q2). |
| `api/bifrost/_version_notice.py` (new, or inline in cli.py) | Temp-dir marker helper: `tempfile.gettempdir()/bifrost-vnotice-<key>` where `<key> = crc32(normalized_url)` (8-hex) — **NOT** Python's built-in `hash()` (per-process randomized via PYTHONHASHSEED, would break cross-process dedupe — Codex review #3), and **NOT** a crypto hash like sha256 (the URL is credential-derived, so hashing it with SHA trips CodeQL's `py/weak-sensitive-data-hashing` taint rule; CRC32 is a non-security checksum and sidesteps it). Holds last-notified version; `should_notify(url, server_version) -> bool` + write-on-show. Notice I/O is fully isolated from the hard-gate decision so a temp-dir permission/collision/stale-marker problem can only cause a missed-or-extra notice, never a false block. |
| `api/tests/unit/test_dto_flags.py` (or new `test_contract_version.py`) | Tripwire test (fingerprint over `COVERED_DTOS`) + assert CLI `CONTRACT_VERSION` == server `CONTRACT_VERSION`. |
| `api/tests/unit/test_cli_version_gate.py` (new) | Unit tests for the gate: contract mismatch → SystemExit; contract match + version drift → notice, no exit; old server (no `contract_version`) → version-string fallback; un-reachable → stderr warning + no exit; notice dedupe within/across "versions". |
| `CLAUDE.md` | Blurb: "Changing an API-contract DTO? Run `./test.sh tests/unit/test_dto_flags.py` (or the contract-version test) proactively — it tells you whether to bump `CONTRACT_VERSION`." |
| `AGENTS.md` | Same blurb (mirror). |

## Fingerprint definition

`_fingerprint(models, routes)`:
- For each model in `models`, **sorted by `__name__`**, compute
  `model.model_json_schema()` then `json.dumps(schema, sort_keys=True)`.
- For routes, `json.dumps(sorted(routes))`.
- Concatenate `name + canonical_schema` for all models, then the canonical
  route blob; `sha256`; hex digest.

Deterministic within one interpreter/Pydantic version — all we need, since it's
only ever compared to a sibling constant in the *same* test process (never
shipped or compared cross-machine — that's what sidesteps the byte-identical
problem that killed the runtime-hash idea).

## Review findings folded in (Codex, 2026-06-09)

Adversarial review of the first draft. Resolutions:

- **#1a Execute/ws shapes uncovered** → fingerprint now includes
  `WorkflowExecutionRequest/Response`; ws raw-dict payload documented as an
  accepted v1 limitation.
- **#1b Validator-only semantics** → documented accepted limitation.
- **#1c "imports both sides" was false** → corrected; tripwire builds its own
  explicit `CONTRACT_FINGERPRINT_MODELS` set, type-aware via `model_json_schema()`.
- **#2/#4 Old-server hard-block footgun** → old-server branch now warns, doesn't
  block. Hard block reserved for confirmed contract-integer mismatch.
- **#3 `hash(api_url)` randomized** → use `sha256(normalized_url)`; notice I/O
  isolated from the gate decision.
- **#5 `watch` checks once** → documented accepted limitation.
- **#6 Route renames uncovered** → `CLI_ROUTES` folded into the fingerprint.
- **CONFIRMED safe:** adding `contract_version` to `VersionResponse` doesn't
  break `useVersionCheck.ts` — it reads only `data.version` and ignores extra
  fields.
- **CONFIRMED:** no production entry point bypasses `main()` (console_scripts →
  `bifrost.cli:main`; `python -m bifrost` → `main()`); only `--version/-V` is an
  intentional early return.

### Second Codex pass — on the implementation diff (2026-06-09)

- **#1 (P2) Non-dict JSON crash** → real regression: `resp.json()` can return a
  list/string (proxy error body); `data.get` then raised `AttributeError`
  outside the best-effort try, crashing every command. Fixed: validate
  `isinstance(data, dict)` inside the try → treated as unreadable verdict
  (warn, continue). Regression test:
  `test_cli_contract_gate.py::test_non_dict_json_warns_not_crashes`.
- **#2 (P2) Fingerprint under-scoped** → real: the in-workflow SDK DTOs
  (`src.models.contracts.cli`) weren't covered. Fixed: **all** SDK DTOs pulled in
  **programmatically** from the `cli` contract module, so new ones are
  auto-covered. Fingerprint rebaselined.

  **Route coverage — decision (after a 3rd Codex pass flagged `/api/files/*`
  also missing):** hand-listing ~100 `/api/*` routes is perpetually incomplete
  (each omission a false-negative) and a route rename 404s loudly rather than
  corrupting silently — which the DTOs already catch. So we **dropped route
  fingerprinting** except `/api/version` (the gate's own handshake). The DTO
  set is the real, complete-by-construction wire contract; pure route renames
  are an accepted, documented non-gate.

## Verification (per CLAUDE.md pre-completion sequence)

- `./test.sh tests/unit/test_dto_flags.py` + new gate/tripwire tests green.
- `cd api && pyright && ruff check .`
- `cd client && npm run generate:types && npm run tsc && npm run lint`
  (VersionResponse changed → regenerate types; check SPA still compiles).
- Manual: install the worktree CLI against a running stack, confirm:
  match → no block; simulated contract mismatch → hard block w/ message;
  old-server (strip `contract_version`) → version-string fallback;
  network-cut → visible warning, command still runs.
- `/codex` review of the diff before declaring done (touches the gate every
  command hits).
