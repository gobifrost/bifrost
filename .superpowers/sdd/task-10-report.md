# Task 10 Report: Unify EventSource Serialization onto the Model (Slice 4)

## Summary

Added `EntityCodec` to `ManifestEventSubscription` and `ManifestEventSource` in `api/bifrost/manifest.py`, swapped all 4 call sites, added golden tests, and confirmed green roundtrip.

## Phase A: Model Changes (`api/bifrost/manifest.py`)

### ManifestEventSubscription
- Added `EntityCodec` mixin to class declaration
- Added `from_row(sub)` — mirrors the inline construction from `serialize_event_source` (lines 257–266): `id`, `target_type or "workflow"`, `workflow_id`, `agent_id`, `event_type`, `filter_expression`, `input_mapping`, `is_active`
- `to_orm_values` raises `NotImplementedError` — child rows are built by the parent resolver/deploy, no standalone path

### ManifestEventSource
- Added `EntityCodec` mixin to class declaration
- Added `from_row(es, *, schedule=None, webhook=None, subscriptions=None)` — mirrors `serialize_event_source` exactly:
  - schedule-derived: `cron_expression`, `timezone`, `schedule_enabled`, `overlap_policy.value` (all None if no schedule)
  - webhook-derived: `adapter_name`, `webhook_integration_id`, `webhook_config`, `rate_limit_per_minute` (default 60), `rate_limit_window_seconds` (default 60), `rate_limit_enabled` (default True)
  - subscriptions: `[ManifestEventSubscription.from_row(s) for s in (subscriptions or [])]`
- `_install_view(extras)` OVERRIDE: returns `self.model_dump(mode="json", by_alias=True)` — keeps all Nones. This is the critical correctness point: capture previously emitted `serialize_event_source(...).model_dump(mode="json")` verbatim (Nones included). The default `EntityCodec._install_view` drops None and would diverge. The override ensures `view(INSTALL) == view(GIT_SYNC)` for EventSource.
- `to_orm_values(dest)` returns parent scalar fields only: `{name, source_type, organization_id, is_active}` — child rows remain in resolver/deploy

## Phase B: Call Site Swaps

### 1. `manifest_generator.py:serialize_event_source` (lines 221–269)
- Replaced 40-line inline construction with single delegation: `return ManifestEventSource.from_row(es, schedule=schedule, webhook=webhook, subscriptions=subscriptions)`
- Removed unused `ManifestEventSubscription` import (no longer directly constructed here)

### 2. `capture.py:_event_entries` (lines 404–447)
- Changed import from `from src.services.manifest_generator import serialize_event_source` to `from bifrost.manifest import ManifestEventSource` + `from bifrost.manifest_codec import Destination`
- Changed `serialize_event_source(es, schedule, webhook, list(subs)).model_dump(mode="json")` to `ManifestEventSource.from_row(es, schedule=..., webhook=..., subscriptions=list(subs)).view(Destination.INSTALL)`
- Updated docstring to reference `ManifestEventSource.from_row` instead of `serialize_event_source`

### 3. `manifest_import.py:_resolve_event_source` (lines 2239–2258)
- Sources parent field dict via `mes.to_orm_values(Destination.GIT_SYNC).direct`
- Resolver still handles UUID conversion (`organization_id` → UUID) and supplies `name=es_name` (the manifest dict key)
- All child-row building, workflow-ref resolution, and imported-wf gate unchanged

### 4. `deploy.py:_upsert_events` (lines 1563–1588)
- Sources parent field dict via `ManifestEventSource.model_validate(mevent).to_orm_values(Destination.INSTALL).direct`
- Install stamps `organization_id=solution.organization_id`, `solution_id`, `created_by` over the direct dict (as before)
- Child schedule/webhook/subscription logic unchanged (still uses `mevent.get(...)`)
- Note: removed `event_type` from `source_values` — it was always `None` in the old code too (not serialized by capture/git-sync); DB default handles it

## Golden Captures

Captured via `UPDATE_GOLDEN=1` against project `bifrost-test-e7d765f2`:

**`event_git_sync.json`**: Full model dump with Nones (adapter_name: null, webhook_integration_id: null, etc.), schedule fields populated, subscription nested with input_mapping, ids masked as `<volatile>`.

**`event_install.json`**: Identical to `event_git_sync.json` — confirms `_install_view` override keeps Nones. The install/git-sync parity is the distinguishing property of EventSource vs all other entities.

Inspection confirms:
- `adapter_name: null` and `webhook_integration_id: null` ARE present in install view (not dropped)
- `organization_id: null` IS present
- `subscriptions` array is nested inline
- Both goldens are byte-identical (the override works)

## Test Results

- `test_manifest_codec.py`: 17/17 passed (15 existing + 2 new)
- `tests/e2e/roundtrip/`: 25/25 passed (repo + solution paths both green)
- pyright: 0 errors
- ruff: all checks passed

## Files Changed

- `api/bifrost/manifest.py` — added EntityCodec, from_row, _install_view, to_orm_values to both EventSubscription and EventSource
- `api/src/services/manifest_generator.py` — serialize_event_source delegates to from_row; removed unused import
- `api/src/services/solutions/capture.py` — _event_entries uses from_row + view(INSTALL)
- `api/src/services/manifest_import.py` — _resolve_event_source sources parent fields from to_orm_values
- `api/src/services/solutions/deploy.py` — _upsert_events sources parent fields from to_orm_values
- `api/tests/unit/test_manifest_codec.py` — added test_event_git_sync_parity, test_event_install_parity
- `api/tests/unit/golden/manifest_codec/event_git_sync.json` — new golden
- `api/tests/unit/golden/manifest_codec/event_install.json` — new golden

## Self-Review

- The `_install_view` override is the key correctness invariant: INSTALL == GIT_SYNC for EventSource, Nones kept. The golden test explicitly asserts this parity and checks `adapter_name is None` (present, not absent).
- `to_orm_values` returns only the 4 parent-row scalars. Child-row orchestration (schedule/webhook/subscription writes, workflow-ref resolution, FK guards) stayed entirely in resolver/deploy — exactly as specified.
- deploy.py `event_type` drop: pre-existing behavior (capture never serialized it; old code passed None; not including it in `_direct` lets DB default handle it — no behavioral change).
- No dead code left: unused `ManifestEventSubscription` import removed from manifest_generator.

## Concerns

None. The entity was already half-unified (capture already delegated to serialize_event_source); this completes the unification cleanly.
