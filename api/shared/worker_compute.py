"""Versioned compute-stage identifiers shared by launchers and API hosts."""

# The version-matched Bifrost worker image has one pre-enrollment capability
# contract. Capacity providers supply physical bounds only; signed Hello is
# still the authoritative proof after a process starts.
LEGACY_WORKER_IMAGE_EXECUTION_HANDLERS = (
    ("workflow.execute", 1),
    ("agent.run", 1),
)
WORKER_IMAGE_EXECUTION_HANDLERS = (
    *LEGACY_WORKER_IMAGE_EXECUTION_HANDLERS,
    ("workflow.execute", 2),
    ("agent.summarize", 1),
    ("agent.summarize_backfill", 1),
)
WORKER_IMAGE_HANDLER_NAMES = tuple(
    dict.fromkeys(handler for handler, _version in WORKER_IMAGE_EXECUTION_HANDLERS)
)

# v2 inventory includes the immutable input digest required for strict
# reconnect reconciliation. API and worker images must negotiate this version.
APP_COMPILE_CAPABILITY = ("app.compile", 2)
APP_COMPILE_CAPABILITY_NAME = "app.compile.v2"
APP_BUNDLE_CAPABILITY = ("app.bundle", 1)
APP_BUNDLE_CAPABILITY_NAME = "app.bundle.v1"
AGENT_COMPUTE_CAPABILITY = ("agent.loop", 1)
AGENT_COMPUTE_CAPABILITY_NAME = "agent.loop.v1"
SUMMARY_COMPUTE_CAPABILITY = ("summary.compute", 1)
SUMMARY_COMPUTE_CAPABILITY_NAME = "summary.compute.v1"
AUDIT_ARCHIVE_CAPABILITY = ("audit.archive", 1)
AUDIT_ARCHIVE_CAPABILITY_NAME = "audit.archive.v1"
AUDIT_EXPORT_CAPABILITY = ("audit.export", 1)
AUDIT_EXPORT_CAPABILITY_NAME = "audit.export.v1"
SOLUTION_ARCHIVE_INSPECT_CAPABILITY = ("solution.archive.inspect", 1)
SOLUTION_ARCHIVE_INSPECT_CAPABILITY_NAME = "solution.archive.inspect.v1"
APP_PREVIEW_CAPABILITY = ("app.preview.compile", 1)
APP_PREVIEW_CAPABILITY_NAME = "app.preview.compile.v1"
WORKFLOW_GRAPH_CAPABILITY = ("workflow.graph.analyze", 1)
WORKFLOW_GRAPH_CAPABILITY_NAME = "workflow.graph.analyze.v1"
COMPUTE_CAPABILITIES = (
    WORKFLOW_GRAPH_CAPABILITY,
    APP_PREVIEW_CAPABILITY,
    APP_COMPILE_CAPABILITY,
    APP_BUNDLE_CAPABILITY,
    AGENT_COMPUTE_CAPABILITY,
    SUMMARY_COMPUTE_CAPABILITY,
    AUDIT_ARCHIVE_CAPABILITY,
    AUDIT_EXPORT_CAPABILITY,
    SOLUTION_ARCHIVE_INSPECT_CAPABILITY,
)
WORKER_IMAGE_COMPUTE_CAPABILITY_NAMES = tuple(
    f"{name}.v{version}" for name, version in COMPUTE_CAPABILITIES
)
