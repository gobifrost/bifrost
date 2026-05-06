# Embeddings: Endpoint Override + Real Test + Dynamic Model List

**Date:** 2026-05-05
**Status:** Approved (Phase 1)
**Author:** Jack
**Scope:** `api/src/services/embeddings/`, `api/src/routers/llm_config.py`, `api/src/models/contracts/llm.py`, `client/src/pages/settings/LLMConfig.tsx`

## Problem

The embedding configuration only works against `api.openai.com`. The LLM config already supports custom OpenAI-compatible endpoints (Azure, OpenRouter, Ollama), so a user with OpenRouter as their LLM provider gets a confusing experience: the LLM card lets them point at OpenRouter, but the embedding card silently falls back to OpenAI proper (and fails if the user only has an OpenRouter key).

Three concrete gaps:

1. **No endpoint override.** Embedding config has no `endpoint` field. The fallback path inherits the LLM key but not the LLM endpoint.
2. **Test doesn't gate save.** The embedding test endpoint exists and actually calls the API, but the save button doesn't require a successful test. Users save broken configs and discover at first knowledge-store query.
3. **Model list is hardcoded.** The dropdown offers `text-embedding-3-small` / `-large` as a `<Select>`. OpenAI has more (e.g. `text-embedding-ada-002`) and OpenRouter exposes Cohere/Voyage/etc.

A separate, larger redesign ("multi-provider config, OpenRouter as first-class citizen") is tracked separately as Phase 2 and is **out of scope** here.

## Non-goals

- Multiple simultaneous provider configs.
- Per-use-case model selection (image, summarization, tuning beyond what already exists).
- Migrating existing data — JSONB tolerates new keys.
- Matryoshka dimension truncation. (See Decisions.)

## Decisions

### Drop the `dimensions` input from the UI

`dimensions` is OpenAI's Matryoshka truncation knob — it's not portable to other providers, and changing it on a populated knowledge store would invalidate every existing pgvector embedding (the column is fixed-width). The backend will continue to accept the field for back-compat with stored configs, but the UI no longer exposes it. The dimension is **derived** from the test response (`len(embedding)`) and shown read-only.

### Endpoint surface: "Using default OpenAI endpoint" vs explicit override

Every user with embeddings working today is implicitly on `https://api.openai.com/v1` — the dedicated config has no `endpoint` field stored, and the fallback path hardcodes OpenAI even when the LLM is on a custom endpoint. That implicit default is the migration baseline.

UI rules:

- **Stored endpoint is null OR equal to `https://api.openai.com/v1`** (dedicated config) → show a status row: **"Using default OpenAI endpoint"** with an **Override** button. No raw URL textbox.
- **Override clicked** → reveal the endpoint `<Input>` prefilled with `https://api.openai.com/v1`. User can change it.
- **Fallback path (uses_llm_key)** → if the LLM has a custom endpoint, show **"Inheriting LLM endpoint: `<url>`"** with an **Override** button. If the LLM is on stock OpenAI, show "Using default OpenAI endpoint" instead.
- **Save behavior**: if the textbox value is empty or matches `https://api.openai.com/v1` exactly, persist `endpoint=null` in `value_json`. Keeps stored configs portable and lets future default changes propagate automatically.

This makes "what am I currently using?" answerable at a glance, instead of forcing the user to recognize a URL.

### Save requires a successful test (when key/endpoint is dirty)

Mirror the LLM card's `canSave = isVerified || hasValidConfig` rule. If the user has typed a new key or changed the endpoint, the Save button is disabled until a test passes. Saving an unchanged existing config is still allowed.

### Model list: capability-aware where exposed, full list otherwise, test as the gate

After a successful test the backend hits `GET {endpoint}/models` and returns model ids. Filtering rules:

1. **OpenRouter (and any endpoint that exposes capability metadata):** `/api/v1/models` returns each model with `architecture.input_modalities` / `architecture.output_modalities`. When those fields are present, filter to entries that advertise embedding capability. This is the explicit OpenRouter win.
2. **OpenAI / Azure / Ollama / anything else without capability fields:** return the **full** model list as-is. **No substring filtering.** The UI shows the standard models dropdown; the user picks one.
3. **Endpoint doesn't support `/models` at all (or call fails):** return `models=None` and the UI shows a free-text `<Input>`.

The test is the gate in every case. If the user picks a non-embedding model (or types a wrong id manually), the embedding test fails and the Save button stays disabled.

Implementation: do the capability check via the raw HTTP response (not the typed OpenAI SDK, which strips non-OpenAI fields). One small helper in the router that takes the endpoint + key and returns `list[str] | None`.

## Backend changes

### `api/src/services/embeddings/base.py`

```python
@dataclass
class EmbeddingConfig:
    api_key: str
    model: str = DEFAULT_EMBEDDING_MODEL
    dimensions: int = EMBEDDING_DIMENSIONS
    endpoint: str | None = None
```

### `api/src/services/embeddings/openai_client.py`

```python
self._client = AsyncOpenAI(api_key=config.api_key, base_url=config.endpoint or None)
```

### `api/src/services/embeddings/factory.py`

`get_embedding_config()`:

- Read `endpoint` from the dedicated config's `value_json`.
- On the LLM-key fallback path, also read `endpoint` from the LLM `provider_config` and propagate it. (Currently dropped on the floor.)

### `api/src/models/contracts/llm.py`

```python
class EmbeddingConfigRequest(BaseModel):
    api_key: str | None = None
    model: str = "text-embedding-3-small"
    endpoint: str | None = None
    # dimensions removed — derived server-side

class EmbeddingConfigResponse(BaseModel):
    model: str = "text-embedding-3-small"
    dimensions: int = 1536  # last-tested value, read-only in UI
    endpoint: str | None = None
    is_configured: bool = True
    api_key_set: bool = False
    uses_llm_key: bool = False

class EmbeddingTestResponse(BaseModel):
    success: bool
    message: str
    dimensions: int | None = None
    models: list[str] | None = None  # new — embedding-capable model ids
```

### `api/src/routers/llm_config.py`

`GET /embedding-config`:

- Include `endpoint` in the response. On the fallback path, populate it from the LLM config.

`POST /embedding-config`:

- Accept and persist `endpoint` in `value_json`.
- On a successful save, also persist the dimensions returned by a test call (so the response shows the right number). The save endpoint will internally call `embed_single` to validate and capture dimensions; if it fails, return 400 and don't persist.

`POST /embedding-test`:

- Honor a new optional `endpoint` field in the request body.
- After the single-embedding test call succeeds, list models. Strategy:
  - Make a raw HTTP `GET {endpoint}/models` (httpx, with the same `Authorization: Bearer` header).
  - If any model in the response has `architecture.input_modalities` / `architecture.output_modalities` fields, treat the response as capability-aware and filter to entries that advertise embedding capability.
  - Otherwise return the **full** id list as-is — no substring filter. The user picks; the test is the gate.
  - On any error (404, malformed JSON, network), set `models=None` and let the UI fall back to free-text input.
- Return `models: list[str] | None` in the response.

`DELETE /embedding-config`:

- Unchanged.

## Frontend changes (`client/src/pages/settings/LLMConfig.tsx`)

In `EmbeddingConfigCard`:

1. **Endpoint surface.** Replace the absent endpoint field with a dynamic surface:
   - When the effective endpoint is null OR `https://api.openai.com/v1`: render a status row "Using default OpenAI endpoint" + **Override** button.
   - When inheriting an LLM endpoint that is NOT stock OpenAI: render "Inheriting LLM endpoint: `<url>`" + **Override** button.
   - When the user clicks **Override** OR the stored endpoint is a custom value: render a textbox prefilled with the resolved URL.
   - On save: trim and normalize trailing slash; if the value matches `https://api.openai.com/v1` exactly or is empty, send `endpoint=null` to the API.
2. Replace the model `<Select>` with a `<Combobox>` populated from `testResult.models` after a successful test. If `models` is null/empty, render a free-text `<Input>` with a small hint ("endpoint didn't return a model list — enter the model id manually").
3. Remove the `dimensions` `<Select>`. Show the last-tested dimension count read-only as a small note below the model field ("Returns 1536-dim vectors").
4. `canSave` becomes:
   ```
   const dirty = apiKey !== "" || endpointOverridden || model !== config?.model;
   const canSave = !saving && (dirty ? isVerified : hasValidConfig) && model;
   ```
5. The form is now visible whenever the user wants to set a dedicated config — not just when `needsDedicatedKey || !uses_llm_key`. When `uses_llm_key` is true, show a small "Override LLM key" toggle that reveals the form. (Keeps the simple "I'm fine inheriting" UX while making override discoverable.)

## Tests

- `api/tests/unit/test_embeddings_factory.py`: add cases for endpoint propagation through the fallback path (LLM endpoint → embedding client).
- `api/tests/e2e/platform/test_llm_config.py` (or wherever embedding endpoints are tested): add cases for set/get with endpoint, and that test-with-bad-endpoint returns `success=False`.
- Vitest sibling for `LLMConfig.tsx` is large and pre-existing; not extending it here unless an existing test breaks.

## Verification before completion

- `cd api && pyright && ruff check .`
- `cd client && npm run generate:types && npm run tsc && npm run lint`
- `./test.sh tests/unit/test_embeddings_factory.py` and `./test.sh tests/e2e/platform/test_llm_config.py`
- Manual smoke: configure embedding with OpenRouter endpoint + key, test, save, run a knowledge-store query.

## Phase 1.5: knowledge_store dim flexibility + reindex (in progress)

Smoke-testing Phase 1 against OpenRouter exposed a downstream constraint: `knowledge_store.embedding` is declared `vector(1536)`, hardcoded for `text-embedding-3-small`. Picking a 3072-dim model (Gemini, OpenAI -3-large) or a 1024-dim model (Cohere, Voyage) fails at INSERT time, which means Phase 1's "any embedding-capable model" promise is hollow.

### Resume context (where things stand mid-implementation)

**Done:**
- Phase 1 entirely (Test/Save split, inheritance, override toggle, encoding_format=float, capability-aware OpenRouter listing). All tests pass (29/29). Live-verified on the worktree's debug stack with deepseek + text-embedding-3-small via OpenRouter.
- Backend `_list_embedding_models` helper, `_normalize_endpoint`, `EmbeddingTestRequest`/`Response` contracts, `verify_completion` split out from `test_connection`.

**In progress (revert if you don't like this direction):**
- `api/src/models/orm/knowledge.py` — switched `Vector(1536)` → `Vector()` (unconstrained).
- `api/alembic/versions/20260506_knowledge_unconstrained_dim.py` — new migration: drops `ix_knowledge_embedding` (IVFFlat), alters column to plain `vector`. Downgrade refuses if any row has a non-1536-dim vector to avoid silent truncation.
- `api/src/routers/llm_config.py` — removed the dim-mismatch guard in `set_embedding_config` (any dim now stores fine).

**Not yet done (this is the part Jack interrupted to redirect):**
- The new "if the user changes embedding model and there are existing rows, offer to reindex" flow.

### Decisions (Phase 1.5)

#### Drop fixed-dim column

`knowledge_store.embedding` becomes plain `vector` (no width). The migration (`20260506_knowledge_unconstrained_dim.py`) does:

1. `DROP INDEX IF EXISTS ix_knowledge_embedding` — the IVFFlat ANN index is tied to a specific dim.
2. `ALTER COLUMN embedding TYPE vector USING embedding::vector` — relaxes the column from `vector(1536)` to plain `vector`.

**What happens to existing rows:** they're untouched. pgvector's `vector` type is internally length-prefixed; the `(1536)` in `vector(1536)` is a CHECK-style constraint, not a storage format change. After the ALTER, every existing row still holds a valid 1536-dim vector and queries against it work identically. **No reindex required just to convert the column.** As long as nothing inserts a different-sized vector, you'd never know the column changed.

The only thing that's actually lost is the IVFFlat ANN index. We're going from index-assisted to sequential scan for similarity queries. At our current scale (thousands to low tens of thousands of docs) this is fine — sequential scan over a few thousand 1536-floats is single-digit ms. Revisit when someone hits a real perf wall, at which point the path is "rebuild a per-dim index after reindex completes" rather than "re-add a fixed-width constraint."

**The dim mismatch failure mode is at *query* time, not column time.** If a user swaps to a 3072-dim model without reindexing, fresh writes go in at 3072, old rows stay at 1536, and `embedding <=> :query` errors out the moment a 3072-dim query vector hits a 1536-dim row (or vice versa). The reindex flow above prevents this by gating the model swap on confirmation when dims differ.

#### Reindex on model change (replaces the old "1536 guard")

The real failure mode isn't "store rejects mismatched dim" — it's "user changes embedding model, old rows are now in a different vector space than new queries, search results silently degrade." Two distinct cases need to be separated, because they have very different consequences:

1. **Dim changes** (e.g. 1536 → 3072). Old rows literally can't be queried against new queries — pgvector's distance ops require matching widths. The store is broken until reindex completes. **Reindex is required.**
2. **Dim is identical, model differs** (e.g. text-embedding-3-small → cohere-embed-v3, both 1536). Queries don't error, but the two vector spaces aren't comparable, so similarity scores are meaningless until reindex. **Reindex is required here too** — same confirmation flow as case 1. The "switch without reindex" option is gone; if the user really wants degraded search they can cancel mid-reindex (state ends up split, same outcome) but it's not a discoverable choice.

Operationally we need to know the *current saved model's* dimension to make the call. We already do — `dimensions` is persisted in `value_json` on every successful save (`api/src/routers/llm_config.py:465`). Compare that against the new test's response dimension.

**Backfilling existing configs:** the field has been written for a while, so most installs already have `dimensions` set. For configs that don't (very old saves, or fallback-via-LLM-key configs that never went through `set_embedding_config`), default to `1536` when reading — that's the value every existing knowledge_store row was embedded at (the column was `vector(1536)` until this migration). No data migration needed; the default is correct for every row that exists in the unconstrained column today.

**Where reindex runs:** the scheduler container (`api/src/scheduler/main.py`). It's already the home for long-running on-demand jobs (`bifrost:scheduler:reimport` is the closest precedent — Redis pub/sub trigger, scheduler does the work). The trigger pattern stays the same; the *progress reporting* uses the existing notification + WebSocket pipeline rather than introducing polling.

**Trigger:**
- New Redis channel `bifrost:scheduler:embedding-reindex`. Payload: `{ "notification_id": "...", "user_id": "...", "model": "...", "endpoint": "..." }`. The actual API key is read from saved config by the scheduler — don't ship secrets through Redis.

**Progress reporting (WebSocket, not polling):**
- The scheduler creates a notification via `NotificationService` at the start of the reindex job (category `embedding_reindex`, status `running`, with `progress: { processed: 0, total: N, cost_so_far_usd: 0 }`).
- Per-batch, it calls `update_notification(notification_id, NotificationUpdate(progress=...))`. The notification service publishes to `notification:{user_id}` over WebSocket — the existing pipeline that the client already listens on.
- Final batch flips status to `completed` (or `failed` with error). Same channel.
- Cancellation: client posts to `DELETE /api/notifications/{notification_id}` (or a dedicated `cancel` action on the notification — TBD when wiring) → scheduler checks a Redis flag `bifrost:notification:{id}:cancelled` between batches and bails cleanly.

**On-demand trigger (independent of model change, admin-only):**
- New endpoint `POST /api/admin/llm/embedding-reindex` — kicks off a reindex against the *currently saved* embedding config. Returns `{ notification_id }`. The client subscribes to `notification:{user_id}` (already does) and renders progress from the notification stream.
- Auth: same `CurrentSuperuser` guard as the rest of `/api/admin/llm/*`. The reindex notification is delivered to the admin who triggered it — not broadcast to all users (the existing `NotificationService` already scopes by `user_id`, so this is automatic).
- UI surface: button on the embedding config card, labeled "Reindex knowledge store" with the row count. Disabled while a notification with `category=embedding_reindex, status=running` exists for this user (look up via the existing `GET /api/notifications` list).
- Cancellable mid-stream — same path as the save-triggered reindex.

**Why not polling?** The platform already runs a WebSocket connection per session (`/ws/connect`) with channel-based subscriptions. Notifications/progress for long-running ops are an established pattern (`NotificationService._publish_notification`). Adding a polling endpoint just for reindex would duplicate that infrastructure and mean the UI has two ways to track in-flight work.

**Save-time flow (always confirm when there are existing rows):**

1. POST `/api/admin/llm/embedding-config` runs the live embed test (existing). The test response carries `dimensions`.
2. Before persisting, compare:
   - **No saved config yet, or no rows in knowledge_store:** persist directly, no reindex.
   - **(model, endpoint) unchanged:** persist directly, no reindex.
   - **Dim matches saved dimension:** persist directly, **skip reindex** (no confirmation needed — the existing vectors and new ones share a space close enough that re-embedding adds no value). This is the only "auto" branch.
   - **Dim differs and rows > 0:** respond with a **needs-confirmation** payload instead of persisting:
     ```json
     {
       "needs_reindex_confirmation": true,
       "reason": "dim_change",
       "old_dim": 1536,
       "new_dim": 3072,
       "row_count": 12453,
       "estimated_cost_usd": 0.249,
       "estimated_duration_seconds": 130,
       "new_model": "openai/text-embedding-3-large",
       "old_model": "openai/text-embedding-3-small"
     }
     ```
3. Client shows a confirmation dialog: "Switching to a 3072-dim model. 12,453 existing docs must be re-embedded before search will work again. Est. $0.25, ~2m."
4. On confirm, client POSTs `/api/admin/llm/embedding-config` again with `confirm_reindex: true`. Server persists the new config, creates a notification, publishes to `bifrost:scheduler:embedding-reindex`, and returns `{ saved: true, notification_id }`.
5. UI shows the in-flight reindex via the existing notification WebSocket subscription. Reindex is **cancellable mid-stream** through the notification's cancel action — same path as the on-demand reindex (see Failure handling below).

> Note on "skip when dim matches": this is a deliberate trade-off. Two different 1536-dim models *do* live in different vector spaces, so similarity scores after a same-dim model swap are technically not comparable. We're betting that the cost (re-embedding everything every time someone tweaks model name) outweighs the search-quality hit, especially since users almost never swap between two same-dim providers. If a user does want to reindex anyway, the persistent "Reindex knowledge store" button is right there.

**Cost estimation:**
- Token count: `len(content) // 4` for a rough estimate (or `tiktoken` if available). Sum per row → total tokens.
- Per-1M-token price: lookup from existing `model_pricing` table (`category=embedding` rows — needs to exist). If unknown, surface "cost unknown" rather than guessing.
- Walk-forward: `total_tokens × price_per_token`.

**Duration estimate:** Use a conservative `~50ms per row` baseline (batched calls average that range against OpenAI/OpenRouter for short documents). Show a range (`60–180s` for 12K rows).

**Failure handling:**
- Reindex is cancellable from the notification UI (existing notification dismiss/cancel action) → sets a Redis flag the scheduler checks between batches. Partial state stays as-is.
- On partial failure mid-job, leave the rows that succeeded as-is (now in the new model's space) and the rows that didn't as-is (still in the old model's space). Log + push a final notification update with counts of each. Don't roll back — the user clicked through, knowing the cost.
- Re-running the same reindex is safe: rows that already have the new embedding just get embedded again at the same cost. Future improvement: track per-row "last embedded by model X" and skip on re-runs.

**Surface in the UI:**
- Embedding config card has a persistent "Reindex knowledge store" button (with row count) regardless of save state — this is the on-demand trigger.
- Save button label is unchanged (`Save`) when the dim matches; changes to `Save and re-embed…` only when dim differs and rows exist.
- The dim-matches notice (after save) has an inline "Reindex now" action.

**Out of scope for first cut:**
- Per-row "last embedded by" tracking (means a re-run would skip already-done rows).
- Reindex resume after scheduler restart.
- Reindex job UI on a separate page.

### Tasks remaining

1. Drop the dim guard, finish the migration (in progress). `dimensions` is already persisted in `value_json` on every save, so no schema change there — just default to 1536 when reading older configs that lack it.
2. Add `model_pricing` rows for embedding models — at least `text-embedding-3-small`, `-3-large`, `-ada-002`. (Or skip cost estimate v1 and just say "cost unknown".)
3. Scheduler reindex handler in `api/src/scheduler/main.py`:
   - Subscribe to `bifrost:scheduler:embedding-reindex` (add to channel list in `_start_pubsub_listener`, dispatch in `_handle_pubsub_message`).
   - Read current saved embedding config (model + endpoint + key).
   - Stream `knowledge_store` rows, batch-embed, UPDATE in chunks, push progress through `NotificationService.update_notification` after each batch.
   - Honor cancellation flag (`bifrost:notification:{id}:cancelled`) — checked between batches.
4. Add `embedding_reindex` to `NotificationCategory` enum (`api/src/models/contracts/notifications.py`) so the client renders a progress bar / cost line for it.
5. Wire cancellation into `DELETE /api/notifications/{notification_id}`: when category is `embedding_reindex`, set the cancellation flag in Redis before/instead of deleting.
6. New / changed backend endpoints (all `CurrentSuperuser`-guarded — admin only):
   - `POST /api/admin/llm/embedding-reindex` — on-demand trigger. Creates the notification, publishes to scheduler, returns `{ notification_id }`.
   - `POST /api/admin/llm/embedding-config` — extend with the dim-comparison branching: passthrough (no rows / unchanged / dim matches) / needs-confirmation (dim differs, rows > 0). Add `confirm_reindex: bool` to the request. On `confirm_reindex=true` save AND trigger reindex; respond with `{ saved: true, notification_id }`.
7. Frontend (admin LLMConfig page):
   - Persistent "Reindex knowledge store (N docs)" button on embedding config card → `POST /api/admin/llm/embedding-reindex` → progress renders via the existing notification WebSocket subscription.
   - Save flow: handle `needs_reindex_confirmation` response → confirm dialog (row count + cost + duration estimate) → re-POST with `confirm_reindex: true`.
   - Disable "Reindex knowledge store" button while a `category=embedding_reindex, status=running` notification is active.
   - In-flight notification renders a Cancel control that hits `DELETE /api/notifications/{id}`.
7. Live smoke against the worktree's stack:
   - Dim swap (1536 → 3072): confirms required reindex path, scheduler picks up the job.
   - Same-dim swap (e.g. 3-small → cohere-embed-v3 if available): confirms the "save without reindex, surface the warning" path and the on-demand button.

## Phase 2 (deferred — not implemented here)

User wants to discuss: making OpenRouter a first-class provider option (use OpenRouter SDK directly, expose autorouter, etc.) rather than treating it as "OpenAI-compatible with custom endpoint." Tracked separately. This Phase 1 work doesn't preclude that direction — it only widens the existing custom-endpoint path.
