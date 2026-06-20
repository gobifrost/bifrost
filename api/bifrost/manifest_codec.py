"""Unified per-entity serialization surface for Manifest* models.

Each Manifest* model mixes in EntityCodec to own its serialization across two
destinations (git_sync: same-env whole-model dump; install: cross-env drop-none
subset). This replaces the four hand-written field-by-field writers
(manifest_generator.serialize_*, capture._*_entries, manifest_import._resolve_*,
deploy._upsert_*) with one source of truth per model. Output is byte-identical
to the legacy writers (proven per-entity in test_manifest_codec.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Destination(str, Enum):
    GIT_SYNC = "git_sync"
    INSTALL = "install"


@dataclass
class ImportFields:
    """The three-way import partition (spike finding 3).

    indexer_content: dict fed to the shared Form/Agent indexer (else {}).
    direct:          fields the resolver sets on the ORM row directly.
    restamp:         fields re-applied AFTER the indexer (org/access/limits).
    """
    indexer_content: dict = field(default_factory=dict)
    direct: dict = field(default_factory=dict)
    restamp: dict = field(default_factory=dict)


class EntityCodec:
    """Mixin adding view()/to_orm_values() to a Manifest* model.

    GIT_SYNC view is generic (whole-model dump). INSTALL view + to_orm_values
    are per-model: each model overrides _install_view() / to_orm_values().
    """

    def view(self, dest: Destination, *, extras: dict[str, Any] | None = None) -> dict:
        if dest is Destination.GIT_SYNC:
            # Whole-model verbatim, by alias, None included — matches
            # serialize_X(...).model_dump(). NOT a curated subset.
            return self.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
        if dest is Destination.INSTALL:
            return self._install_view(extras or {})
        raise ValueError(dest)

    def _install_view(self, extras: dict[str, Any]) -> dict:
        # Default install view: drop-none over the model's own fields + extras.
        # Models with forced-[] fields or alias quirks override this.
        data = self.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
        out = {k: v for k, v in data.items() if v is not None}
        out.update({k: v for k, v in extras.items() if v is not None})
        return out

    def to_orm_values(self, dest: Destination) -> ImportFields:  # pragma: no cover - overridden
        raise NotImplementedError
