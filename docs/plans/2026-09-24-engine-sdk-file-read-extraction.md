# Engine SDK file read service extraction

Executor: OpenCode `opencode-go/muse-spark-1.3-contributor`. Owner/reviewer:
Codex. Worktree: `/home/jack/GitHub/bifrost/.claude/worktrees/sdk-engine-files`,
branch `codex/sdk-engine-files`, baseline `906754447`. This is independent
preparation for the file stage in the aggregate SDK plan. Do not modify the
aggregate worktree or primary checkout; do not commit, push, or merge.

## Bounded task

Extract the cloud-mode SDK read behavior for `files.read`/`read_bytes`,
`files.list` (without `include_metadata`), `files.exists`, and `files.stat`
from `api/src/routers/files.py` into a transport-neutral service in
`api/shared/sdk_files.py` (and a small shared policy/context module only if
necessary). Make those HTTP route branches call the shared service. Preserve
the other route variants and all responses. This task is **service extraction
only**: no local transport, SDK facade, dispatcher, binary framing, or parent
principal work. The full fixed file facade remains unfinished until later.

The service accepts a trusted FileCaller containing the full `UserPrincipal`,
DB session, org ID, Solution target/caller IDs, and app ID. The HTTP adapter
builds it from its authenticated Context. `LocalDispatchPrincipal` does not
carry enough role/claim state for file policies and must not be used here.
The future parent caller will construct a token-equivalent principal from
trusted parent metadata. Never derive authority from child-supplied fields.

Preserve the existing `file_read_tiers` order and inbound Solution gates,
declared Solution file locations, workspace-only authority, mode filtering,
path policy checks and list filtering, policy-deny audit, 404 versus 403/400
status, binary/base64 response, metadata and storage behavior. In particular,
a path missing across allowed tiers remains 404, while a path denied in all
tiers retains the current deny audit. Keep the workspace
`include_metadata=True` branch in the router for now; the Python SDK never
requests it. Do not duplicate core policy logic between router and service:
move the required helper functions into a shared module if necessary and
import them in both places.

Allowed production files: `api/src/routers/files.py`, new
`api/shared/sdk_files.py` and a small new `api/shared/file_access.py` if
necessary. Focused tests under `api/tests/` and this handoff doc. If another
production file must change, report why before editing it. No configuration,
dependencies, unrelated cleanup, credentials, or compatibility re-exports.

Add focused service tests for own/Solution/org/global tier order, policy
allow/deny and audit, declared location and workspace gate, binary content,
list filtering, existence/stat semantics. Run affected file policy and
Solution-scope tests plus `./test.sh quality api`. Report exact commands,
failures, and broader suites not run. Stop at a documented blocker if a safe
transport-neutral seam needs more context than this task permits.
