# Object storage provider boundary

Application consumers use the typed contracts in `src.core.storage.contracts`.
The factory in `src.core.storage` selects S3 by default or the opt-in Azure
Blob adapter. Existing S3 configuration and ambient AWS credentials remain
supported. Provider selection does not migrate existing objects or introduce
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
this is a shared deployment boundary, including S3 installations using a
host IAM role. Only enable host storage identities where workflow processes
cannot obtain their credentials, or where all workflow authors are trusted
with the identity's full storage authority. Removing environment variables
alone does not restrict an identity endpoint. Stronger isolation requires an
OS/container network boundary between credential-bearing parents and workflow
processes; Bifrost's authorized SDK connection remains the workflow storage path.

Configure the existing account/container through environment variables on the
API and executor services (the supplied production Compose file forwards them):

```dotenv
BIFROST_OBJECT_STORAGE_PROVIDER=azure_blob
BIFROST_AZURE_BLOB_ACCOUNT_URL=https://youraccount.blob.core.windows.net
BIFROST_AZURE_BLOB_CONTAINER=bifrost
BIFROST_AZURE_BLOB_AUTH=managed_identity
# Optional for a user-assigned identity:
# BIFROST_AZURE_BLOB_MANAGED_IDENTITY_CLIENT_ID=<client-id>
```

For account-key authentication, select `BIFROST_AZURE_BLOB_AUTH=account_key`
and inject `BIFROST_AZURE_BLOB_ACCOUNT_KEY` through the installation's secret
mechanism. Leave that key unset in managed-identity mode. Standard Azure public,
US Government and China endpoints derive the account name; custom endpoints
and emulators require `BIFROST_AZURE_BLOB_ACCOUNT_NAME`. Restart the services
after changing deployment configuration. Account/container creation, identity
permissions and browser CORS remain installation setup; see the
[migration runbook](../runbooks/object-storage-migration.md) before switching an
existing installation.

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
