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
