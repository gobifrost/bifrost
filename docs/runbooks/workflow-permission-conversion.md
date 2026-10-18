# Convert workflows to Declared Permissions

Platform administrators can open the same conversion wizard from a workflow's
Access panel, the Workflows list (selected or filtered workflows), or Settings →
Zero Trust Automation. Settings starts a complete platform readiness review.

Analysis runs as a durable PlatformJob. Leaving the page does not stop it. Open
its notification or Platform Jobs history entry to return to the saved report.
The browser receives progress over the existing notification WebSocket; the
shared job controls provide cancellation and recovery after runner loss.

## Review and apply

The analyzer follows the entry function and reachable Python helpers, imports,
aliases, package initialization, and import-time effects without executing user
code. Content-addressed compact file summaries are reused across workflows and
runs. It does not hold source or ASTs for an entire workflow inventory in memory.
Traversal and source-size limits produce visible review requirements.

Permission candidates combine SDK transport mappings from the canonical access
list with recorded workflow operations from the preceding 30 days. Observations
cannot prove that unobserved branches are safe. Dynamic calls, unresolved
imports, resource policies, ownership checks, and secret context need review.

Review each proposal before selecting it. The default identity strategy creates
a dedicated identity with the User base role, the workflow's organization, and
no additional roles or organization reach. Recorded requirements for additional
reach, policy roles, or personal claims are shown for review; conversion does
not grant them. Keeping an existing broad identity requires acknowledgement.
Conversion never reduces the privileges of a shared identity.

Apply is atomic per workflow. It sets Declared Permissions and the reviewed
grant set. Solution workflows require explicit acknowledgement of the exact
reviewed declaration, which is saved and approved with its canonical digest in
the same transaction. Review decisions and mutations are audited.

Changed source, transitive helpers, permissions, identity authority, policy
metadata, or Solution approval invalidate a proposal. Analyze it again. Partial
Apply progress remains visible, and an already committed selection is not
applied twice after runner loss. Verification rechecks the declaration and
authorization inputs; it does not execute workflows or prove every runtime path.

## Platform default

The Settings flow has a separate, explicit final cutover. It requires a
completed review of the current active inventory and no workflows effectively
using Full Access. Cutover rechecks source and authorization inputs. A newly
added workflow or stale proposal blocks it. Applying individual proposals does
not change the platform default.

## CLI and MCP

The CLI and MCP enqueue the same jobs and read the same paginated report:

```bash
bifrost workflows convert analyze --workflow example
bifrost workflows convert report JOB_ID --offset 0 --limit 50
bifrost workflows convert apply JOB_ID --review-file reviewed-selections.json
bifrost workflows convert analyze --scope all --platform-cutover
bifrost workflows convert cutover JOB_ID --acknowledge-platform-cutover
```

Use the shared Platform Jobs commands to inspect progress. The apply file contains the
exact workflow IDs, source fingerprints, identity strategies, and review
acknowledgements from the report. For example:

```json
{
  "selections": [{
    "workflow_id": "00000000-0000-0000-0000-000000000001",
    "source_fingerprint": "<64-character fingerprint from the report>",
    "identity_strategy": "dedicated",
    "acknowledged_unknowns": false,
    "acknowledged_broad_identity": false
  }],
  "acknowledged_solution_requests": false
}
```

No workflow source or credentials belong in this file.

## Capacity

Conversion jobs serialize under a shared resource lock, process workflows in
bounded batches, and checkpoint after each workflow. They require 384 MiB of
available scheduler memory headroom and use the shared 85% admission / 95% hard
memory limits. Configure a real scheduler container memory limit for these
guards to operate. Analysis and Apply have a one-hour timeout; cutover has a
15-minute timeout. Only runner loss is automatically retried, once; validation
failures require review. Inspect memory samples and queue waits in Scheduler
Diagnostics when sizing the deployment.
