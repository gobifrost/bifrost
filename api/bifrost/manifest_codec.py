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


# Per-destination FieldClass -> whether view() EMITS the field. This is the
# single source of truth the install view derives from (was 4 hand-maintained
# frozenset allowlists that drifted from the field classes — bug B2). GIT_SYNC
# is a whole-model dump (handled directly in view()); only INSTALL filters.
#
# INSTALL (cross-install): IDENTITY/CONTENT/REFERENCE travel; ENVIRONMENT is
# stamped from the target install (so NOT carried in the entry); SECRET never
# rides the plaintext manifest. A field may override its class default for the
# install view via classify(install_view="keep"|"drop").
_INSTALL_EMITS: dict[str, bool] = {
    "identity": True,
    "content": True,
    "reference": True,
    "environment": False,
    "secret": False,
}


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
        """Install view derived from each field's FieldClass via ``_INSTALL_EMITS``,
        overridable per field with ``classify(install_view=...)``. Drop-none.

        This is the default install serializer, used by every entity EXCEPT
        ``ManifestEventSource``, which overrides ``_install_view`` to return the
        full git_sync dump (its install view intentionally keeps nulls — a shape a
        drop-none policy cannot express; see that class for the rationale). No
        entity uses a hand-maintained allowlist any more. A field is emitted when
        its class (or its per-field ``install_view`` override) says keep AND its
        value is not None; transport extras the caller passes (repo_path, logo_b64,
        role_names, …) are merged drop-none.
        """
        from typing import cast

        from pydantic import BaseModel

        from bifrost.field_classes import field_class_of, install_view_override

        model = cast(BaseModel, self)
        cls = type(model)
        data = model.model_dump(mode="json", by_alias=True)
        # alias -> python field name, so we can look up each emitted key's class.
        alias_to_name: dict[str, str] = {
            str(f.alias or name): name for name, f in cls.model_fields.items()
        }
        out: dict[str, Any] = {}
        for key, value in data.items():
            name = alias_to_name.get(key, key)
            override = install_view_override(cls, name)
            if override == "keep_empty_list":
                # Emit even when empty/None, as [] — capture sends `x or []`,
                # never drops it (e.g. tags, role_names, knowledge_sources).
                out[key] = value if value is not None else []
                continue
            if value is None:
                continue
            if override is not None:
                emit = override == "keep"
            else:
                emit = _INSTALL_EMITS.get(field_class_of(cls, name).value, True)
            if emit:
                out[key] = value
        out.update({k: v for k, v in extras.items() if v is not None})
        return out

    def to_orm_values(self, dest: Destination) -> ImportFields:  # pragma: no cover - overridden
        raise NotImplementedError
