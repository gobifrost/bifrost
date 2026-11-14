"""Provider bootstrap; authorization stays in the upstream SDK/API."""
import importlib

from runtime_guard import require_runtime

# Reject runtime drift before importing upstream modules or accepting credentials.
require_runtime()

mark_execution_child = importlib.import_module("src.core.module_cache_sync").mark_execution_child

# Audit-only workspace import recording for the declared-capability contract.
# Appends the loader-relative path of every module actually executed through
# the virtual loader. Never blocks, never alters loading. A persistent tenant
# child serves many attempts, so this is cleared at each attempt boundary.
recorded_workspace_paths: list[str] = []


def _install_import_recorder() -> None:
    from src.services.execution import virtual_import as _virtual_import

    loader_cls = _virtual_import.VirtualModuleLoader
    original_exec = loader_cls.exec_module

    def recording_exec(self, module):
        path = getattr(self, "storage_path", None) or self.path
        recorded_workspace_paths.append(path)
        return original_exec(self, module)

    loader_cls.exec_module = recording_exec


_install_import_recorder()

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


async def run_authorized_attempt(execution_id, context, api_url, *, public_url=None):
    import os

    # This module runs only in the fresh child, never in the trusted supervisor.
    # No SECRET_KEY, database or Cloudflare management credential is supplied.
    os.environ["BIFROST_API_URL"] = api_url
    # Attempt-scoped audit state: a persistent tenant child would otherwise
    # report every module loaded by every earlier attempt.
    recorded_workspace_paths.clear()
    return await run_execution(execution_id, context,
        resource_observer=UnknownResources(), public_url=api_url if public_url is None else public_url)
