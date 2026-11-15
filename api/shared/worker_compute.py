"""Versioned compute-stage identifiers shared by launchers and API hosts."""

# v2 inventory includes the immutable input digest required for strict
# reconnect reconciliation. API and worker images must negotiate this version.
APP_COMPILE_CAPABILITY = ("app.compile", 2)
APP_COMPILE_CAPABILITY_NAME = "app.compile.v2"
AGENT_COMPUTE_CAPABILITY = ("agent.loop", 1)
AGENT_COMPUTE_CAPABILITY_NAME = "agent.loop.v1"
SUMMARY_COMPUTE_CAPABILITY = ("summary.compute", 1)
SUMMARY_COMPUTE_CAPABILITY_NAME = "summary.compute.v1"
COMPUTE_CAPABILITIES = (APP_COMPILE_CAPABILITY, AGENT_COMPUTE_CAPABILITY, SUMMARY_COMPUTE_CAPABILITY)
