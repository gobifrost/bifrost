# S3 workspace image dependency removal

Issue: #1380. Related layer measurement: #1379. Reporter: MTG-Thomas.

The S3 workspace mirror now uses the existing `S3ObjectStore` and HTTPX/CRT
transport instead of starting AWS CLI processes. Production and development
Dockerfiles no longer install Debian's `awscli` package. Git remains installed.
No storage settings, provider selection, Git repository ownership, or workflow
permissions change. Azure's mirror remains unchanged.

The mirror streams uploads through the existing bounded multipart writer, stages
downloads on the workspace filesystem, and limits concurrent transfers to eight.
It retains upload snapshot validation, recursive deletion, symlink following,
encoded/non-ASCII keys, and incremental single-part and standard 8 MiB multipart
ETag checks. An opaque ETag cannot establish local content identity, so that file
is transferred. Matching size and timestamp alone do not suppress a changed file.
Local inventory, hashes, reads and writes run off the event loop; cancellation
drains their threads before files close. Cancellation drains transfers and their body cleanup before repository ownership
can be released; a second cancellation cannot interrupt that drain.

Existing file paths are preserved through the provider-owned `WorkspaceMirror`
interface used by `GitRepoManager`, including Git internals, uncommitted files,
and checkpoints. Download staging adds temporary disk use proportional to the
changed files, and local hash checks add disk reads for unchanged files. These
costs replace the CLI's subprocess and dependency closure; no end-to-end worker
startup latency improvement is claimed.

## Verification

The mirror unit tests exercise incremental transfer, equal-size changes,
single/multipart identity, deletion, directory markers, symlinks, unsafe names,
failed-download preservation, snapshot rejection, and repeated cancellation.
Existing GitRepoManager unit tests retain their repository and checkpoint
contracts. A live storage test launches an upload in a fresh Python subprocess
with no executable search path, then checks multipart bytes, Git internals,
encoded keys, update/delete synchronization and cleanup. It creates no platform
jobs; the subprocess boundary itself is the contract under test.

Focused commands and measured results are recorded below before merge.

## Image attribution

The baseline is main `7be95b942c055a0ba0e52981a845435598a9cdec`, built from
`git archive HEAD` into a clean temporary context. Both builds use the repository's
pinned production Dockerfile bases and `BIFROST_VERSION=unknown`. The only removed
package request is `awscli`; the Python requirements lock is unchanged.

The baseline's installed Debian `awscli` is **2.23.6-1**, rather than the v1
assumed by the issue. Its installed package alone accounts for 121,127 KiB.
Removing the apt request also removes the separately installed distro Python and
its dependency closure. The candidate has no `aws` executable and no
`/usr/bin/python*`. The supported interpreter under `/usr/local/bin` remains.

The final matched local cumulative image sizes are 2,017,855,911 bytes before
and 1,761,792,126 bytes after, a 256,063,785-byte (244.20 MiB) reduction. This is
local image size, not compressed registry transfer size. Both production builds
succeeded; no image-pull latency claim is inferred.

## Focused results

- `./test.sh tests/unit/core/test_s3_workspace_mirror.py tests/unit/services/test_git_repo_manager.py -v` — 53 passed.
- `./test.sh tests/unit/core/test_s3_workspace_mirror.py tests/unit/core/test_s3_transport.py tests/e2e/platform/test_s3_workspace_mirror.py tests/e2e/platform/test_object_storage.py::test_provider_checkpoint_restores_git_identity_and_uncommitted_bytes tests/e2e/platform/test_git_sync_local.py::TestRoundTrip::test_workflow_survives_round_trip tests/e2e/platform/test_git_sync_local.py::TestIncrementalPush -v --durations=0` — 71 passed on the final source, including cancellation during blocked local I/O.
- `./test.sh quality api` — pyright and ruff passed.

The new subprocess storage journey took 1.277 seconds. Existing checkpoint
restoration took 0.164 seconds; the workflow round trip took 0.797 seconds and
the two incremental push cases took 0.428 and 0.404 seconds. Stack build/boot and
teardown dominate command wall time. No additional platform job was added.

The full backend/client/browser suites were not run locally; the protected
merge queue remains the complete-suite gate. External S3 acceptance is pending;
local SeaweedFS coverage and historic DigitalOcean Spaces transport acceptance
do not establish this candidate's live external-provider acceptance. Live AWS
deployment credential chains remain a separate unverified environment unless
explicitly exercised in the acceptance record.
