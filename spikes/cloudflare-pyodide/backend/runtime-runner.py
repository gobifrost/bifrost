"""Provider bootstrap; authorization stays in the upstream SDK/API."""
import importlib

from runtime_guard import require_runtime

# Reject runtime drift before importing upstream modules or accepting credentials.
require_runtime()

mark_execution_child = importlib.import_module("src.core.module_cache_sync").mark_execution_child

# Must precede installation of the virtual import hook. This forbids all parent
# Redis/S3 paths even during module initialization in an isolated runtime.
mark_execution_child()
importlib.import_module("pyodide_refresh").install()
execution_worker = importlib.import_module("src.services.execution.worker")
ResourceMetrics = execution_worker.ResourceMetrics
run_execution = execution_worker.run_execution


class UnknownResources:
    def start(self):
        pass

    def finish(self):
        return ResourceMetrics(None, None, None, None, None)


async def run_authorized_attempt(execution_id, context, api_url):
    import os

    # This module runs only in the fresh child, never in the trusted supervisor.
    # No SECRET_KEY, database or Cloudflare management credential is supplied.
    os.environ["BIFROST_API_URL"] = api_url
    return await run_execution(execution_id, context,
        resource_observer=UnknownResources(), public_url=api_url)
