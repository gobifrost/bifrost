# Object storage provider boundary

Application consumers use the typed contracts in `src.core.storage.contracts`.
The factory in `src.core.storage` currently selects the built-in S3 adapter.
Existing S3 configuration and ambient AWS credentials remain supported. This
change does not activate another backend, perform a migration or introduce
runtime plugin discovery.

Consumers retain authorization, organization/Solution scope, key layouts, audit
and cache policy. Providers own native requests and errors, credentials, signing,
response-body lifetime, pagination and multipart mechanics. `SyncObjectStore`
provides narrow parent-process reads; `WorkspaceMirror` owns bulk Git workspace
transfer. S3 keeps its native CLI acceleration behind that mirror interface.

Versions are opaque concurrency witnesses, separate from content digests. The
S3 codec preserves quoted persisted ETags, including multipart `-N` values, in
existing managed-file move markers and Git receipts. Expected-version writes,
reads and deletes transmit conditions on the actual operation. Copies pin the
source and distinguish source mutation from a destination collision. The returned
version belongs to the commit response; a later HEAD cannot identify the winner.

`BIFROST_S3_MULTIPART_COPY_MODE=stream` remains explicit. Large copies in that mode
use version-pinned ranged GETs and ordinary multipart uploads, not server-side
part copy or a silent retry fallback. Range status, exact Content-Range, length
and source version are checked. Failed/cancelled transfers drain their owned
abort; a second cancellation cannot release ownership before cleanup finishes.

Content properties include HTTP Expires as distinct from lifecycle retention.
Signed access is a complete request: method, URL, required headers and expiry.
The file API, Python SDK, App SDK and form uploader preserve the returned headers;
callers must not attach platform authorization to the storage origin. Object
absence, missing namespace, permission, transport, conflict and unsupported
operations remain distinct error categories.

Local tests qualify client/consumer behavior, not every S3-compatible backend.
The optional network-isolated fixture under `api/qualification/` exercises the
real provider, transport and signer with synthetic HTTP responses. It does not
prove R2 backend conditional DELETE, actual competing completion winner state,
browser CORS, CLI acceleration or physical transfers above 5 GiB. Live backend
acceptance requires its own exact source/configuration evidence.

## Azure Blob provider (opt-in)

`BIFROST_OBJECT_STORAGE_PROVIDER=azure_blob` selects the native Blob adapter in
`src/core/storage/azure.py`, with bulk Git transfer in `azure_mirror.py`. S3
remains the default. Selection is per installation; changing providers does not
migrate data, and cross-container operations stay outside the
configured-container contract.

Authentication explicitly selects a managed identity or an account key. The
adapter never discovers developer credentials, provisions resources, grants
roles or migrates objects. Signed PUT responses carry
`x-ms-blob-type: BlockBlob`; browser, App SDK and manual PUT callers must forward
every returned header. Blob CORS must allow the actual browser origin, methods
and request headers. Workflow environment scrubbing keeps Blob configuration
parent-only, but an ambient host identity or metadata endpoint is not isolated;
treat workflow-code trust as a deployment security concern.

Multipart upload state and block receipts live in the uploading context only;
cross-context or cross-process resumption is unsupported. Current upload and
copy consumers complete each sequence inside one context. The workspace mirror
rejects symbolic links; the S3 CLI mirror can follow them.

Blob has no native HTTP Expires object property. Such writes fail explicitly
instead of silently dropping the property; this non-parity requires maintainer
acceptance. Unknown write options fail explicitly rather than being ignored.

Unit coverage uses fakes. The provider-neutral
`tests/e2e/platform/test_object_storage.py` suite also runs against pinned
Azurite with `BIFROST_TEST_OBJECT_STORAGE_PROVIDER=azure_blob` and
`BIFROST_TEST_AZURE_BLOB_RESET=1` on a disposable container. The
`api/tests/azure_qualification/` lane covers explicit real-Azure identity,
boundaries and probes; its results are source-bound and never transfer across
candidates. Hosted CI and manual maintainer/security review remain gates.

The Blob adapter is adapted from the AGPL-3.0
[MTG fork at 162d3f477](https://github.com/Midtown-Technology-Group/bifrost/tree/162d3f47722baf2992e60d11a6026cb95b9dbf04),
retaining its operation ownership, credential lifecycle, listing and signing
lessons behind the object-store contract.
