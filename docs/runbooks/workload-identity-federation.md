# Workload Identity Federation

Federated credentials let an external workload exchange a signed JWT for a short-lived Bifrost access token acting as an existing non-human Identity. They do not create a human session or a refresh token.

The RFC 8693 grant issues tokens for this Bifrost API only. Explicit target
selection (`resource` or `audience`), delegated actors, and requested OAuth
scopes are rejected; the mapped Identity's existing roles define access.

Create the mapping under that Identity with its exact issuer, token audience, subject, and any additional exact scalar claims. Select the assertion type (`id_token` or `access_token`), and set a 60–3600 second Bifrost token lifetime. The external assertion must contain `iss`, `aud`, `sub`, `exp`, and `iat`, be signed by the configured issuer's discovered asymmetric JWKS, and match every configured string exactly. GitHub immutable subjects and other provider-specific formats are ordinary exact strings; Bifrost does not parse provider formats.

The optional discovery URL is for standards-compliant issuers whose issuer URL does not itself host discovery. It must be public HTTPS, and its metadata `issuer` must exactly equal the configured issuer. This is useful for Entra v1 `sts.windows.net/{tenant}/` issuers, whose discovery document is hosted elsewhere. Discovery/JWKS URLs are public-URL guarded and redirects are refused.

Overlapping mappings that match one assertion are denied rather than choosing an identity. Avoid duplicate or overlapping exact profiles across identities.

## CLI

### GitHub Actions

Grant the workflow an OIDC token and read-only source access when it needs a
checkout. Install the CLI served by the target API so its contract matches the
API, then obtain and exchange the assertion without writing it to disk.

```yaml
permissions:
  id-token: write
  contents: read

steps:
  - uses: actions/checkout@v4
  - run: python -m pip install "$BIFROST_API_URL/api/cli/download/bifrost-cli.tar.gz"
  - run: bifrost login --oidc --url "$BIFROST_API_URL" --audience "$BIFROST_ASSERTION_AUDIENCE"
```

Configure the mapping from an observed signed token: copy exact `iss`, `aud`,
and `sub` values. Do not assume a provider-specific subject format. GitHub can
use immutable custom subjects that encode repository/workflow identity instead
of a repository name. Restrict observed `repository_id`, `ref`, `environment`,
or `workflow_ref` with additional exact claims when they form part of the
intended trust boundary.

### Azure managed identity

For Entra v2 access tokens, create a single-tenant resource/API app, set
`api.requestedAccessTokenVersion` to `2`, and set its Application ID URI to
`api://<resource-app-id>`. Expose the required application permission and
grant it to the client or managed identity. Request that resource URI from
managed identity, but configure the mapping with the actual signed `aud`: it
can be the app's bare GUID even when the requested resource is
`api://<app-id>`. Configure the tenant v2 issuer
`https://login.microsoftonline.com/<tenant-id>/v2.0`, and exactly restrict
observed `tid`, `oid`, and (when known) `azp`. Restrict `idtyp=app` only if it
is present in the issued token.

```sh
bifrost login --managed-identity --url "$BIFROST_API_URL" \
  --audience "api://<resource-app-id>" \
  --managed-identity-client-id "$USER_ASSIGNED_IDENTITY_CLIENT_ID"
```

The helper supports IMDS and App Service/Automation `IDENTITY_ENDPOINT` plus
`IDENTITY_HEADER`. `--audience` is only the Azure resource requested from
metadata; it is not an authorization target or a substitute for the configured
assertion `aud`.

An Entra federated credential is a different topology. Create a client app
federated identity credential with the external issuer, the exact external
subject, and its expected external audience (commonly
`api://AzureADTokenExchange` for Entra token exchange). Give that client app
the resource API application permission. The external workload then uses its
projected token file with Azure `WorkloadIdentityCredential` to obtain an
`api://<resource-app-id>/.default` token, and exchanges that resulting Entra
access token with Bifrost:

Install Azure's `azure-identity` SDK in the workload and configure
`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_FEDERATED_TOKEN_FILE`, and
`AZURE_RESOURCE_SCOPE=api://<resource-app-id>/.default`. Pipe the acquired
access token straight to the CLI; do not enable shell tracing or log the pipe.

```sh
python - <<'PYTOKEN' | bifrost login --url "$BIFROST_API_URL" --subject-token-file - --subject-token-type access_token
import os
from azure.identity import WorkloadIdentityCredential

credential = WorkloadIdentityCredential()
try:
    print(credential.get_token(os.environ["AZURE_RESOURCE_SCOPE"]).token)
finally:
    credential.close()
PYTOKEN
```

The Azure credential reads its configured projected token file rather than
placing the assertion in a command argument. Configure Bifrost against the resulting Entra token's verified
issuer and claims. Direct managed identity sends the managed identity access
token to Bifrost instead.

### Other standards-compliant workload issuers

Use `--subject-token-file <path>` (or `-` for stdin) with
`--subject-token-type id_token|access_token`:

```sh
provider-token-command | bifrost login --url "$BIFROST_API_URL" \
  --subject-token-file - --subject-token-type access_token
```

This is appropriate for workload-identity credential tooling that already
obtains a provider access token. Never put a JWT in command arguments, shell
history, or a checked-in file.

`--audience` is only the audience/resource requested from GitHub or Azure. It is not compared to Bifrost's configured assertion `aud` field: providers may return a different normalized audience, and the configured mapping is the authority for exchange validation.

Workload credentials are access-token-only. They expire and callers must acquire a new assertion; they never fall back to human refresh behavior.

## Operations

Disable, edit, or delete a mapping to revoke existing Bifrost workload tokens immediately. Mapping revision and authorization epoch checks are enforced on each API and MCP request. Identity disable/delete and role/permission changes retain their existing authorization invalidation behavior.

Deploy all API replicas that understand workload federation before enabling a mapping. During an old/new mixed fleet, requests fail closed intermittently because old replicas do not support the grant or workload claim format. Rolling an image back leaves the additive mapping table in place but makes workload login unavailable. Do not run the schema downgrade as routine rollback: it deletes mappings.

### Upgrade and rollback

The migration is additive and ordered after the current head. It creates an
empty mapping table and authorization-epoch triggers, needs no backfill, and
has a schema rehearsal from both published v2.0.0 and the prior head, preserving
an existing Identity without running any intermediate application release.
Skipping releases does not require an intermediate runtime or federation
backfill: Alembic applies the ordered migrations directly. Existing human-auth
cutover and rollback restrictions in [the stable upgrade guide](stable-2-upgrade.md) are unchanged:
do not downgrade this schema as a routine image rollback, and enable mappings
only once every API replica understands workload tokens.


## References

- [OAuth 2.0 Token Exchange (RFC 8693)](https://www.rfc-editor.org/rfc/rfc8693.html)
- [GitHub OIDC claims](https://docs.github.com/en/actions/reference/security/oidc)
- [Azure WorkloadIdentityCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.workloadidentitycredential)
- [Entra API token version](https://learn.microsoft.com/en-us/graph/api/resources/apiapplication)
