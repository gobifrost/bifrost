# AI provider HTTP recovery

Inventory for #658, checked against the November 5, 2026 source tree.
`shared/ai_http_retry.py` owns the shared six-attempt, 60-second retry window,
provider retry guidance, bounded exponential jitter, and privacy-safe retry logs.
Each request retains its existing HTTP timeout; this is a retry window rather
than a new total-operation deadline. Cancellation propagates during requests
and sleeps. There is no provider selection or fallback in this policy.

| Surface | Classification | Recovery |
| --- | --- | --- |
| Agent/chat/model requests through `agent_runtime/model_factory.py` and `llm/pydantic_client.py` | Existing model request contract | Existing `AIRetryTransport`; policy moved without changing attempts, delays or terminal responses |
| Provider SDK discovery in `provider_catalog_service.py` | Idempotent read | OpenAI/Anthropic use the existing shared transport; Google retains native SDK retry settings, consuming shared attempt/status constants |
| Compatibility probes in `openai_transport_detection.py` and `opencode_go_detection.py` | Existing model request contract | Existing shared transport; no media submission |
| Embeddings through `embeddings/factory.py` | Existing embedding request contract | Existing shared transport |
| `model_catalog.refresh_model_catalog`, models.dev GET | Idempotent read | Shared bounded GET retry; final HTTP exception remains available to the maintenance job; last good stored catalog remains intact |
| `model_pricing.py` and provider/model discovery | Local catalog lookup | No direct HTTP request; earlier OpenRouter catalog/pricing HTTP surfaces have been replaced by the shared catalog |
| `media_generation.generate_image_with_config`, image POST | Unsafe-to-replay write | Single submission, including on 429/timeout; no POST redirect replay |
| `media_generation.generate_video_with_config`, video POST | Async submission, unsafe to replay before job identity is known | Single submission; no speculative idempotency header |
| Video status and content GET | Idempotent read of an existing operation | Shared GET retry against the same status/content URL; never re-enters submission |
| Media upload POST | Absent from the current raw media service | No new upload path is introduced |

The [OpenAI Images reference](https://developers.openai.com/api/reference/python/resources/images)
does not establish an idempotency guarantee for the image generation path used
here. OpenRouter's video submission reference describes an ID and polling URL
returned after submission; it does not establish replay protection for a lost
submission response. The current documentation URL was unavailable during this
review, so no stronger guarantee is inferred. Until a documented guarantee can
be verified, these writes remain terminal. After a video ID is received, the
existing operation is observed through its read endpoints.

`MediaProviderError` retains provider, model, surface, HTTP status, root exception
type, an opaque operation reference, and whether submission acceptance is
uncertain. Its text retains diagnostic identity when existing Chat, SDK and
PlatformJob consumers persist/display an error string. Request URLs, prompts,
keys and raw provider error bodies are excluded from that text and from logs.
For known video jobs, the reference is a hash of the job ID; otherwise it is a
fresh local submission correlation ID. It is not a provider idempotency key.
Operators must check provider activity before resubmitting an uncertain write.

Both existing video PlatformJob policies retain `max_attempts=1` and
`retry_on_runner_loss=False`. No new job, transport endpoint, retry queue,
workflow, permission rule, configuration or customer migration is introduced.
The SDK and Chat continue to receive `MediaGenerationError` subclasses.
