"""Back-compat re-export of the shared-dependency vendoring helpers.

The canonical implementation lives in ``bifrost.solution_vendoring`` because it
runs **client-side** during ``bifrost deploy`` (the installed CLI has no ``src``
package on its path). Server-side code and tests may keep importing it from
here. See ``bifrost/solution_vendoring.py`` for the implementation.
"""
from __future__ import annotations

from bifrost.solution_vendoring import (
    scan_imported_modules,
    vendor_shared_deps,
)

__all__ = [
    "scan_imported_modules",
    "vendor_shared_deps",
]
