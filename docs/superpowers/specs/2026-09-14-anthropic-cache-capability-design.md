# Anthropic Prompt-Cache Capability Design

## Goal

Keep prompt caching enabled for Anthropic-compatible endpoints that support it,
while allowing an endpoint that explicitly rejects `cache_control` to succeed
without caching and remembering that result on the provider connection.

## Behavior

Provider connections hold a nullable `anthropic_prompt_cache_supported` value.
`NULL` means untested, `true` means a cached request succeeded, and `false`
means the endpoint explicitly rejected cache control. Unknown and supported
connections send cache settings. An explicit 400/422 cache-control rejection is
retried once without those settings. Authentication, rate-limit, transport,
timeout, generic validation, and server failures are never converted into an
uncached retry.

The runtime updates the in-memory decision immediately and persists it through
an isolated best-effort database transaction. Persistence failure cannot turn a
successful model request into a failure. Changing the provider, endpoint, or
credential resets the stored decision to unknown so the next request probes the
new configuration.

## Integration

The adaptive behavior wraps the native Pydantic AI Anthropic model's final
request method, so agent runs and direct completion/streaming calls share one
decision point. The connection API exposes the nullable state for diagnosis.

