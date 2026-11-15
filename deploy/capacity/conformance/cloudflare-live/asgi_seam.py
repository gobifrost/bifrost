"""Import-order seam for the disposable Cloudflare conformance API."""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Callable
from types import ModuleType
from typing import Any


SCOPE_RE = re.compile(r"^[a-zA-Z0-9._:-]{1,128}$")


class ConformanceAsgiError(RuntimeError):
    pass


def conformance_scope(environ: dict[str, str] | None = None) -> str:
    value = (environ if environ is not None else os.environ).get("BIFROST_CONFORMANCE_SCOPE")
    if not isinstance(value, str) or SCOPE_RE.fullmatch(value) is None:
        raise ConformanceAsgiError("BIFROST_CONFORMANCE_SCOPE is invalid or missing")
    return value


async def no_capacity_loops(_stop_event: Any, _settings: Any) -> list[Any]:
    """Test-only replacement: retain API control, suppress physical planning."""
    return []


def disable_capacity_loops(
    *,
    environ: dict[str, str] | None = None,
    importer: Callable[[str], ModuleType] = importlib.import_module,
) -> str:
    """Guard and replace exactly the scheduler's capacity-loop factory.

    This must run before ``src.main`` is imported: SchedulerControl imports the
    factory lazily at startup, leaving all API dispatch and remote-owner paths
    intact while preventing the external worker reconciler from being created.
    """
    scope = conformance_scope(environ)
    control = importer("src.services.kubernetes_control")
    control.start_kubernetes_loops = no_capacity_loops
    return scope
