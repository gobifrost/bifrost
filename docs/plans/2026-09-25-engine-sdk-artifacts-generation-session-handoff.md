# Release DB connections during artifact generation

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

The new shared artifact generation service is the HTTP and future local
boundary. Make its database usage safe for worker-parent dispatch:

- `sdk_generate_image_artifact` currently calls `generate_image(db, ...)`,
  which reads provider config and then holds the same checked-out DB
  connection during a provider HTTP call that may take 180 seconds. Split
  config resolution from the provider call using the existing media
  generation service: resolve config in a short transaction, release its
  connection, run provider HTTP without a DB connection, then use a DB
  session for artifact storage and usage recording. Keep exact provider
  request, response, error, model, usage, and artifact behavior.
- `sdk_render_document_artifact` may read workspace images through the DB
  before CPU rendering. Release its connection after image resolution and
  before `asyncio.to_thread` rendering. Spreadsheet/text rendering has no
  pre-render DB query; confirm it does not check out a connection early.

Keep the HTTP route and shared service public contracts unchanged. Reuse
`get_media_provider_config` and the existing provider implementation;
refactor that provider implementation only as needed to accept a previously
resolved config, preserving existing callers of `generate_image`. Do not
introduce a new provider, job, retry, fallback, or connection pool. The
final artifact store and usage rows must remain in the same transaction
until the HTTP dependency (or later local dispatcher) commits. Do not touch
video jobs, AI, public SDK, or local dispatcher.

Add a focused test that proves the provider call begins after the config
read transaction has released its connection, and one for document render
after image reads. Keep provider response/error tests green. Run targeted
`./test.sh` unit/E2E coverage and `./test.sh quality api`; report exact
commands/results. Diagnose failures without blind reruns, retries, skips,
or timeout increases.
