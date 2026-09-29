# Model catalog (models.dev)

Bifrost's provider list, model lists, context windows, fallback prices, chat
capabilities, and per-profile reasoning choices come from the
[models.dev](https://models.dev) catalog (MIT, community-maintained). Code:
`api/src/services/model_catalog.py`.

## Where the data comes from

1. **Bundled snapshot** — `api/src/services/model_catalog_snapshot.json.gz`,
   a trimmed copy shipped with each release. Used until the first refresh and
   whenever models.dev is unreachable, so the catalog is never empty.
2. **Scheduled refresh** — the `model_catalog.refresh` platform job runs every
   4 hours and once at scheduler startup. It sends the stored ETag, so an
   unchanged catalog costs a 304. The last good copy lives in the
   `ai_model_catalog` table.
3. **Safety check** — a response with fewer than 20 providers, or under half
   the models of the last good copy, is rejected (`catalog_rejected`) and the
   stored copy stays in use. An unreachable source fails retryably
   (`catalog_unreachable`).

Administrators can refresh immediately from **Settings → AI → Models →
Refresh catalog** (`POST /api/admin/ai/catalog/refresh`). Runs appear under
Diagnostics → Scheduler.

## Providers

- **Native** providers (OpenAI, Anthropic, Google, OpenRouter, OpenCode Go)
  use their own adapters and are exercised directly by Bifrost.
- **Community** providers reuse one of those adapters, chosen from the
  provider's SDK in the catalog (`@ai-sdk/openai-compatible` → the
  OpenAI-compatible adapter, and so on). They are labelled "Community" in the
  UI; Bifrost does not test them individually.
- models.dev publishes no base URL for providers whose AI SDK package has one
  built in (Groq, xAI, Mistral, Together, DeepInfra, Cerebras, Perplexity,
  Cohere). Bifrost supplies their OpenAI-compatible URLs from
  `OPENAI_COMPATIBLE_ENDPOINTS`; a URL the catalog later publishes wins. Any
  other catalog provider without an OpenAI-style endpoint is not offered.
- A connection pointed at an endpoint other than the catalog's is a custom
  connection: its models are listed by the endpoint's own `/models`.

## What the catalog feeds

| Surface | Behavior |
| --- | --- |
| Model picker | Catalog models with context, price, and reasoning; any ID can still be typed. |
| Pricing | A model with no price gets the catalog price on first use. Existing prices (including hand-entered ones) are never overwritten. A cost the provider reports on the response still wins. |
| Chat capabilities | Catalog image/PDF input and tool support, unless an administrator record exists. |
| Reasoning | A profile may choose only values in the model's `reasoning_options`. The choice is sent through each adapter's own field so levels such as `xhigh` and `max` are not rounded; each call records the reasoning tokens the provider reports. |

## Release tasks

Refresh the bundled snapshot:

```bash
python api/scripts/update_model_catalog_snapshot.py   # needs the API deps
```

Check that reasoning choices reach real providers (spends a few cents; needs
keys):

```bash
op run --env-file=<refs> -- docker exec -i -e OPENROUTER_API_KEY \
  -e ANTHROPIC_API_KEY -w /app <api-container> python - \
  < api/scripts/check_live_reasoning.py
```

Each case compares the lowest and highest reasoning choice; "off"/"none" must
report zero reasoning tokens and the highest choice must report more than the
lowest.
