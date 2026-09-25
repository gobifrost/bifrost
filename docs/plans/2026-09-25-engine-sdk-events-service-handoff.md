# Event emission shared service

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout.

Extract the fixed `bifrost.events.emit` business behavior from
`api/src/routers/events.py::emit_topic_event` into a shared application
service under `api/shared/`, used by that HTTP handler and a future
engine-local dispatcher. Reuse existing `src.services.events.emit_event`
for durable emission and delivery. Move authorization, topic validation,
scope parsing, service-org confinement, target Solution resolution, inbound
gate and trustworthy caller resolution together. Preserve the HTTP route's
exact status/error precedence, response DTO, subscriber count, event actor,
and transaction/delivery behavior. The service must take a trusted explicit
principal, context, and validated request; never a raw child actor/caller
claim. For workflow engine tokens, preserve the existing signed Solution
caller behavior, including the constrained `caller_solution` case; document
the trust proof needed by a local dispatcher.

Keep the HTTP route thin. Do not add local transport yet, and do not touch
other event endpoints, files, tables, AI, identity, or import transport.
Add focused service tests for regular user denial, service own-org emission,
cross-org/GLOBAL denial, Solution inbound gating, validation, and success.
Run relevant `./test.sh` unit/E2E tests and `./test.sh quality api`.
Report exact commands/results and diagnose failures without blind reruns,
retries, skips, or timeout increases.
