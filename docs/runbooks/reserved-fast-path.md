# Reserved Fast Path Operations

Use Settings → Kubernetes to set Reserved Fast Path Workers. The value is an
exclusive managed-worker count within Max Workers; Normal warm workers are
preserved, so `warm_workers + reserved_fast_path_workers <= max_workers`.
At least one managed-worker slot remains available to Normal work. Reducing a
reservation is allowed; removing its last worker is refused while Fast Path
runs are queued or scheduled unless a fixed Fast Path worker remains. Settings
shows the ready managed-worker count at its last refresh, so startup or
replacement gaps are visible. New Fast Path runs are refused when no dedicated capacity is configured. They never run
on a Normal worker.

Workflows use `auto`, `normal`, or `fast_path`. `auto` is Normal by default.
Optional automatic App promotion affects only server-verified App execution
contexts. Set long-running App workflows explicitly to Normal.

For a static Fast Path executor, deploy a normal worker image with
`BIFROST_POOL_WORKER_EXECUTION_LANE=fast_path`. Do not run PlatformJob,
service, or agent-summary capacity only on that worker: those consumers are
intentionally Normal-only. Runtime package updates still apply to both lanes.
Resource requests and limits still
matter: this feature prevents queue borrowing, not node CPU contention or a
response-time guarantee.

External capacity providers are unsupported for Fast Path reservations in this
initial release. They may continue serving Normal work, but Settings rejects a
nonzero reservation until the provider can advertise and enforce the signed
lane contract.
