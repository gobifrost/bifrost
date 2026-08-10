# Managed Files

Use this reference for application/user data such as uploads, reports, attachments, and generated documents. Do not confuse managed runtime files with source stored in instance `_repo`.

## Choose the correct file kind

| File kind | Owner and authoring path |
|---|---|
| Solution app/workflow source | Local workspace; deploy with the Solution |
| Loose workflow/module/v1 app source | Instance `_repo`; edit with unqualified `bifrost files` commands |
| Solution runtime/user data | A location declared in `.bifrost/files.yaml`; access with Solution context or supported `--solution` CLI options |
| Loose runtime/user data | An instance location plus organization/file-policy scope |

Never edit deployed Solution source through runtime file APIs. Never store user uploads alongside local app source.

## Declare Solution locations

Declare portable location names in `.bifrost/files.yaml`:

```yaml
locations:
  - documents
  - finance
```

Use business/domain names, not internal storage prefixes. `workspace` is reserved and is not a Solution runtime location. Deploy owns the declarations; runtime bytes remain environment data and survive ordinary source edits.

Deploy creates an admin policy at a declared Solution root so platform admins can seed it. This does not grant ordinary users access. Add explicit file policies for the operations and principals the feature requires.

## Access from a v2 app

Use text methods for small text and signed upload/download paths for large or binary browser payloads:

```tsx
import { files, useFiles } from "bifrost";

const listing = useFiles("invoices/", {
  location: "finance",
  includeMetadata: true,
});

await files.write("notes/today.txt", "ready", { location: "finance" });
const note = await files.read("notes/today.txt", { location: "finance" });
await files.upload("photos/job-123.jpg", photo, {
  location: "job-photos",
  contentType: photo.type,
});
const blob = await files.download("exports/report.pdf", { location: "finance" });
```

`useFiles` supplies loading, error, denied, empty, metadata, and refresh state. Render those states deliberately. The app provider carries its app/Solution/org context; do not embed environment IDs in source. Use the browser SDK directly for ordinary policy-checked uploads; add a workflow only when the feature needs server-side validation or orchestration beyond file access.

## Access from a workflow

```python
from bifrost import files

await files.write("reports/status.txt", "ready", location="documents")
content = await files.read("reports/status.txt", location="documents")
pdf = await files.read_bytes("reports/status.pdf", location="documents")
signed = await files.get_signed_url(
    "uploads/input.csv",
    method="PUT",
    location="documents",
)
```

Use `write_bytes`/`read_bytes` for small binary values and signed URLs for large transfers. Consult `../generated/python-sdk-signatures.md` for the exact available options and return types.

## CLI boundaries

Unqualified `bifrost files` commands target instance `_repo`/`workspace`. On commands that accept it, `--solution <install>` targets installed Solution runtime files and defaults to the Solution location rules. Check `../generated/cli-reference.md` because not every file verb supports every scope.

The CLI read/write surface is text-only. `files write` replaces the complete file; it is not a patch operation.

## Policies and Solution fallback

File policy checks apply independently at every tier. Grant only the actions required for the path prefix: read, list, write, delete, or signed access as supported by the policy model.

With `global_repo_access: false`, a Solution can access only its declared owned location. With it enabled, reads/list/exists/signed-read can fall back through eligible install-org and global file tiers. Writes and deletes still target the Solution-owned tier; the flag does not authorize modification of shared files.

Read `solution-resource-access.md` for the exact runtime matrix.

## Verification

- Verify the location is declared where required.
- Test an allowed caller and a denied caller when policies matter.
- Exercise empty, missing-file, denied, large/binary, and replacement behavior as relevant.
- Confirm the app does not expose a token, secret, or unrestricted signed URL.
- Treat local-preview file writes as real instance mutations and report seeded/deleted data in the handoff.
