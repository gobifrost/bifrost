"""First-class agent finding contracts (compatibility re-export).

The canonical DTO definitions live in :mod:`shared.models` per the
repository shared-model rule for public DTO work. This module re-exports the
exact names so existing imports keep working.
"""

from shared.models import (
    FindingCreate,
    FindingPublic,
    FindingSourceKind,
    FindingStatus,
    FindingUpdate,
)

__all__ = [
    "FindingCreate",
    "FindingPublic",
    "FindingSourceKind",
    "FindingStatus",
    "FindingUpdate",
]
