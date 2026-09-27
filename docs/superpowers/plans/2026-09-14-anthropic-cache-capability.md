# Anthropic Prompt-Cache Capability Implementation Plan

1. Add a nullable provider-connection column and API contract field, then carry
   the connection id and cached decision into `LLMConfig`.
2. Add unit tests for cache-specific error classification, one-time fallback,
   successful detection, known-unsupported bypass, config resolution, and reset.
3. Implement a small adaptive request helper plus a native Anthropic model
   subclass used by both agent and direct client paths.
4. Add the Alembic upgrade/downgrade and validate the migration in the test
   stack.
5. Run focused tests, the complete pre-PR gate, and a credential-injected live
   request when it can be done without exposing secrets.

