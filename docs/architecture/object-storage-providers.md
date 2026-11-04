# Object storage providers

S3 remains the default. Native Azure Blob is an explicit installation option;
it does not translate requests through an S3 proxy or provision Azure resources.
High-level storage services retain their existing policies and key layouts.
Changing providers does not migrate existing objects. For an existing
installation, review and verify the data-migration plan before changing its
selected provider.

## Configuration

| Variable | Azure Blob configuration |
| --- | --- |
| `BIFROST_OBJECT_STORAGE_PROVIDER` | `azure_blob`; default is `s3` |
| `BIFROST_AZURE_BLOB_ACCOUNT_URL` | Account Blob endpoint |
| `BIFROST_AZURE_BLOB_CONTAINER` | Existing private container |
| `BIFROST_AZURE_BLOB_AUTH` | `managed_identity` (default) or `account_key` |
| `BIFROST_AZURE_BLOB_MANAGED_IDENTITY_CLIENT_ID` | Optional explicit user-assigned identity client ID |
| `BIFROST_AZURE_BLOB_ACCOUNT_KEY` | Required only for account-key authentication; keep out of source and logs |
| `BIFROST_AZURE_BLOB_ACCOUNT_NAME` | Optional explicit account name for custom/local endpoints |

Blob mode does not require S3 endpoint, bucket or credentials. Configuration
rejects contradictory credential choices. Managed identity uses
ManagedIdentityCredential explicitly, never a developer-login/default credential
chain. The identity needs data access to the selected container and user
delegation-key permission for delegation SAS requests. Qualify actual RBAC and
SAS behavior before enabling an installation; this feature creates no grants.

The prior MTG fork used DefaultAzureCredential and was deployed on AKS. That is
deployment evidence for its credential chain, not proof of this implementation's
explicit managed-identity path. Federated Kubernetes workload identity is not
qualified here. AKS installations must select a supported credential mode and
verify it; do not silently substitute a different identity chain.

## Consumers and contracts

The selected transport covers workspace files, managed-file locations, Apps,
Solutions, deploy/source artifacts, SDK packages, audit retention/moves, health,
signed uploads, and parent-only synchronous import/requirements fallback. Git
uses native Blob mirroring/checkpoints in Blob mode and the existing AWS CLI
mirror in S3 mode. See [Git workspace handoff](git-workspace-handoff.md) for
private-state publication, credential lifecycle and the existing lock lease.

ETags remain opaque conditional tokens, not content hashes. Reads stream stored
bytes without automatic content decompression. Native conditions guard writes,
multipart completion and source-version copy. Listing preserves pagination and
prefix semantics. SDK clients, credentials and response bodies have explicit
ownership/closure; failed operations retain native error distinctions.

Signed upload responses include required headers. Browser, App and manual Python
PUT callers must send them. Current hosted SDK/form callers forward them; older
bundled Apps and manually implemented clients require installation-specific
verification. This does not change SDK binding-major selection or automatically
migrate legacy clients.

Configure Blob CORS for the actual browser origin, including its port, rather
than an internal container hostname. Permit the methods and request headers the
installation uses (including `Content-Type` and `x-ms-blob-type` for signed PUT),
and expose ETag when required. Verify an OPTIONS request with that exact origin
before testing the browser upload. CORS is not authorization; keep the container
private and preserve SAS expiry and permissions.

## Explicit limits

- Blob has no native HTTP Expires object property. This adapter rejects that
  write option instead of silently dropping it. This is a disclosed non-parity
  limitation requiring maintainer acceptance, not successful S3 equivalence.
- Bucket arguments identify the configured container; cross-container copy is
  outside the application's current single-container contract. Unknown write
  options fail explicitly.
- Workflow environment scrubbing preserves the parent/child storage boundary,
  but it does not isolate an ambient host identity or metadata endpoint. Treat
  workflow-code trust and identity reachability as deployment security concerns.
- Azurite cannot qualify Azure identity/RBAC, service limits, or its unsupported
  native nonempty block-copy API. A guarded refusal is not copy parity.
- Large repository performance, the existing nonrenewing Git lock lease and
  checkpoint retention remain operating constraints. No universal limits or
  throughput certification is implied by bounded tests.

## Qualification and provenance

The mandatory local contract matrix selects real SeaweedFS/S3 and pinned Azurite
explicitly. Additional Azure tests under `api/tests/azure_qualification/` require
an explicitly selected disposable endpoint/container; the normal suite never
discovers them implicitly. Real-Azure managed-identity, account-key and browser
receipts are tied to their exact source. They do not transfer automatically to
a modified candidate or establish a complete Azure platform matrix.

The implementation is adapted from the AGPL-3.0
[MTG fork at 162d3f477](https://github.com/Midtown-Technology-Group/bifrost/tree/162d3f47722baf2992e60d11a6026cb95b9dbf04).
It retains ownership, credential closure, listing and signing lessons while
adapting to upstream's CRT transport shape, streaming and native conditional
block-copy/completion requirements. Fork deployment configuration, credentials
and environment identifiers are not copied.
