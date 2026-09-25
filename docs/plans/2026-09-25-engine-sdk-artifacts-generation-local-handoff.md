# Artifact generation SDK engine-local transport

Executor: OpenCode. Reviewer: Codex. Work only in this worktree. Do not
commit, push, merge, or touch the primary checkout. The reviewer will
integrate after the active dispatcher stage completes.

Implement local parent dispatch for the four fixed artifact generation
methods: `artifacts.create_document`, `create_spreadsheet`, `create_text`,
and `create_image`. External SDK calls keep HTTP. Reuse the existing
`api/shared/sdk_artifact_generation.py` functions that the HTTP routes
call. Preserve the input DTOs, `ArtifactRef` output, owner/workspace
scope, renderer/provider errors, usage attribution, and content storage.
The child sends only data; the parent derives actor, org, and execution
identity from `LocalDispatchPrincipal`.

Use named allowlisted ops in `api/bifrost/_local_transport.py`, facade
selection in `api/bifrost/artifacts.py`, and parent handlers in
`api/src/services/execution/sdk_local_dispatch.py`. The raw parent
session factory does not auto-commit: explicitly commit after each
successful shared service call so the generated artifact and AI usage
survive the operation. Do not commit after errors. The shared service
now releases config/image-read connections before provider HTTP and
CPU rendering; preserve that property. `create_image` can spend up to
180 seconds in provider HTTP, so align the local child deadline and
parent operation timeout with the existing HTTP behavior and cleanly
cancel on child/parent shutdown. Render operations also need bounded
time appropriate to their CPU work. No HTTP fallback or retry after a
failed local write.

Add focused unit parity and real forked-worker tests. Disable fixed
artifact-generation HTTP in the child, then verify committed output via
external HTTP/DB; test errors, scope, provider failure, and long-running
timeout handling without sleeps that mask a race. Run the changed tests,
known artifact consumers, and `./test.sh quality api`. Follow the repo
testing protocol (Docker `./test.sh`, diagnose failures, no blind reruns,
retry, skip, or xfail). No main merge.
