# Task 7 Report: CustomClaim Serialization Unification (Slice 4)

## Query-dict parity resolution

`capture._claim_entries` emits `c.query` (raw JSONB dict from the DB) directly.
`ManifestCustomClaim.query` is a `ClaimQuery` Pydantic model — `model_dump(mode="json")`
produces `{"table": ..., "where": null, "select": ...}` always including the `where` key
(even if null). The raw JSONB from DB may or may not include a `where` key depending on
how the claim was originally stored.

Resolution: mirrored the `ManifestTable._raw_policies` pattern. Added `_raw_query: dict | None = None`
as a `PrivateAttr`-style class variable on `ManifestCustomClaim`. `from_row()` stores the
raw `claim.query` dict on `_raw_query`. `_install_view()` emits `_raw_query` if set,
falling back to `query.model_dump(mode="json")` only when not set via `from_row`.

For `GIT_SYNC`, `model_dump` produces the parsed `ClaimQuery` — matching the previous
`serialize_custom_claim` behavior which also called `ClaimQuery.model_validate(claim.query)`
then stored it on the model (so model_dump round-trips it the same way).

## Golden captures (key sets verified)

`claim_git_sync.json`:
```json
{"description": "...", "id": "<volatile>", "name": "rt_claim_golden",
 "organization_id": "<volatile>", "query": {"select": "id", "table": "users", "where": null},
 "type": "list"}
```
Keys: id, organization_id, name, description, type, query (with where: null). ✓

`claim_install.json`:
```json
{"description": "...", "id": "<volatile>", "name": "rt_claim_install_golden",
 "query": {"select": "device_id", "table": "assets", "where": null}, "type": "list"}
```
Keys: id, name, description, type, query — NO organization_id. ✓

Note: `where: null` is present because PostgreSQL stored the explicit null from the seed dict.
The `_drop_none` in `_install_view` only drops top-level None values, not nested dict keys.

## Phase B swaps

1. `manifest_generator.py:266-275` — `serialize_custom_claim` body replaced with `ManifestCustomClaim.from_row(claim)`. Removed unused `ClaimQuery` import.

2. `capture.py:458-473` — `_claim_entries` dict-building loop replaced with `ManifestCustomClaim.from_row(c).view(Destination.INSTALL)` list comprehension. Added local imports for `ManifestCustomClaim` and `Destination`.

3. `manifest_import.py:2142-2210` — `_resolve_custom_claim` now sources field dict from `mclaim.to_orm_values(Destination.GIT_SYNC).direct`. Natural-key upsert, NO-realign, and query JSONB orchestration kept intact. `claim_id`/`org_id` extracted via `UUID(fields["id"])` / `UUID(fields["organization_id"])`.

4. `deploy.py:976-1002` — `_upsert_claims` keeps ClaimQuery re-validate + org/solution stamp + ownership guard. The install dict (from capture's `_install_view`) is read directly as before since `ManifestCustomClaim` requires `organization_id` which is absent from the install view. Removed unused `ManifestCustomClaim`/`Destination` imports added during initial attempt.

## Test results

- Golden tests idempotent: 13/13 codec tests × 2 runs, both green.
- Roundtrip detector: 25/25 green (1 claim-specific test `test_solution_shareable_roundtrip_claim` confirmed green).
- Full unit suite: 4825 passed, 2 skipped.
- pyright: 0 errors. ruff: all checks passed.

## Files changed

- `api/bifrost/manifest.py` — `ManifestCustomClaim` gains `EntityCodec` mixin + `from_row`, `_install_view`, `to_orm_values`, `_raw_query` private attr.
- `api/src/services/manifest_generator.py` — `serialize_custom_claim` delegates to `from_row`; `ClaimQuery` import removed.
- `api/src/services/solutions/capture.py` — `_claim_entries` delegates to `from_row().view(INSTALL)`.
- `api/src/services/manifest_import.py` — `_resolve_custom_claim` sources fields from `to_orm_values(GIT_SYNC).direct`.
- `api/tests/unit/test_manifest_codec.py` — added `test_claim_git_sync_parity` + `test_claim_install_parity`.
- `api/tests/unit/golden/manifest_codec/claim_git_sync.json` — new golden fixture.
- `api/tests/unit/golden/manifest_codec/claim_install.json` — new golden fixture.

## Self-review

- No dead code, no fallbacks.
- Orchestration kept in family files (natural-key upsert in manifest_import, org/solution stamp in deploy).
- `_raw_query` pattern is consistent with `_raw_policies` — same rationale, same mechanism.
- deploy.py: the install view dict doesn't carry `organization_id`, so `ManifestCustomClaim(**mclaim)` would fail validation. Correct behavior is to read the dict fields directly — the dict IS the `to_orm_values(INSTALL).direct` output, just read at the consumer.

## Concerns

None. The deploy.py side is consistent with how `_upsert_tables` and `_upsert_workflows` work — they also read from raw dicts produced by capture, not from model instances.
