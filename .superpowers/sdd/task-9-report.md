# Task 9 Report: Integration Serialization Unification (Slice 4)

## Summary

Added `EntityCodec` + `from_row` to 4 manifest models and swapped 2 call sites. 15/15 codec tests green, 25/25 round-trip detector green, 0 pyright errors, ruff clean.

## Phase A: from_row implementations

### ManifestIntegrationConfigSchema.from_row(cs)
Mirrors `serialize_integration` config_schema item construction (manifest_generator.py:214-222): maps `key`, `type`, `required`, `description`, `options`, `position` directly off the ORM row. `to_orm_values` raises `NotImplementedError` — no standalone ORM path, child reconciliation belongs to `_resolve_integration`.

### ManifestOAuthProvider.from_row(op)
Mirrors `serialize_integration` oauth_provider construction (manifest_generator.py:224-236): maps `provider_name`, `display_name`, `oauth_flow_type`, `client_id or "__NEEDS_SETUP__"`, `authorization_url`, `token_url`, `token_url_defaults or None`, `scopes or []`, `redirect_uri`. **client_secret is NEVER serialized** (security). `to_orm_values` raises `NotImplementedError`.

### ManifestIntegrationMapping.from_row(im)
Mirrors `serialize_integration` mappings item construction (manifest_generator.py:238-244): `organization_id→str-or-None`, `entity_id`, `entity_name`, `oauth_token_id→str-or-None`. `to_orm_values` raises `NotImplementedError`.

### ManifestIntegration.from_row(integ, *, config_schema=None, oauth_provider=None, mappings=None)
Mirrors the full `serialize_integration` function (manifest_generator.py:196-247): maps all parent scalar fields, delegates child list construction to each child model's `from_row`.

### ManifestIntegration.to_orm_values(GIT_SYNC)
Returns `ImportFields(direct={id, name, entity_id, entity_id_name, default_entity_id, list_entities_data_provider_id})` — exactly the parent scalar fields `_resolve_integration` sets on the Integration ORM row. Raises `NotImplementedError` for `INSTALL` with an explanatory comment about the connection_schema template path.

## Phase A: Golden capture and inspection

Seeded: Integration with `entity_id="tenant_id"`, 1 `IntegrationConfigSchema` (`api_key`, secret type), 1 `OAuthProvider` (`rt-golden-oauth`, real URLs), 1 `IntegrationMapping` (org-scoped).

Golden at `api/tests/unit/golden/manifest_codec/integration_git_sync.json`:
- **Children present and correctly shaped**: `config_schema[0]` has all 6 fields; `oauth_provider` has all 9 fields; `mappings[0]` has all 4 fields
- **client_secret NOT present** — only `client_id` appears under `oauth_provider`
- **Volatile keys chosen**: `{"id", "organization_id"}` — integration UUID (top-level `id`) and mapping `organization_id` (nested, masked by `_mask` traversal) are per-run; both correctly replaced with `"<volatile>"`

## Phase B: Call site swaps

### manifest_generator.py:196-247 (serialize_integration)
Inline `ManifestIntegration(...)` + 3 nested child list comprehensions replaced with single `ManifestIntegration.from_row(integ, config_schema=..., oauth_provider=..., mappings=...)` call. Removed now-unused imports: `ManifestIntegrationConfigSchema`, `ManifestIntegrationMapping`, `ManifestOAuthProvider`.

### manifest_import.py:1642-1663 (_resolve_integration)
Added `from bifrost.manifest_codec import Destination` import. `integ_values` dict now sources scalar fields from `fields = minteg.to_orm_values(Destination.GIT_SYNC).direct` instead of reading directly off `minteg`. All child reconciliation (config_schema upsert-by-natural-key, oauth_provider on-conflict-do-update, mappings upsert-by-natural-key, cache refreshes, oauth_token_id preservation) kept completely intact — only the parent scalar field source changed.

## Test results

- **Golden idempotent ×2**: 15/15 passed both runs
- **Round-trip detector**: 25/25 passed (covers Integration round-trip through git sync)
- **pyright**: 0 errors, 0 warnings
- **ruff**: All checks passed

## Files changed

- `api/bifrost/manifest.py` — EntityCodec added to 4 models; from_row + to_orm_values added to all 4
- `api/src/services/manifest_generator.py` — serialize_integration swapped to from_row; 3 unused imports removed
- `api/src/services/manifest_import.py` — _resolve_integration parent fields sourced from to_orm_values(GIT_SYNC).direct
- `api/tests/unit/test_manifest_codec.py` — test_integration_git_sync_parity added
- `api/tests/unit/golden/manifest_codec/integration_git_sync.json` — new golden file

## Self-review

- No dead code: all 3 NotImplementedError raises in child models include explanatory messages
- No install path added (YAGNI per brief)
- Child reconciliation in _resolve_integration is completely untouched (oauth_token_id preservation, cache refresh on id rewrite, upsert-by-natural-key for config_schema and mappings)
- client_secret is provably absent from the golden — only `client_id` appears
- Ruff caught 4 issues (3 unused imports, 1 unused sa_text alias) — all fixed before commit

## Concerns

None. The refactor is mechanical: the golden proves byte-identity, the round-trip detector proves the whole git-sync pipeline still works end-to-end.
