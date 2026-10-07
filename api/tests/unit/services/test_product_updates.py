"""Product Update bundles are immutable runtime input, never database content."""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.services.product_updates import load_product_updates_bundle, visible_entry_ids


def _bundle(entry_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "target_ref": "a" * 40,
        "content_ref": "b" * 40,
        "entries": [
            {
                "id": entry_id,
                "revision": 1,
                "published_at": "2026-10-06T22:01:18Z",
                "title": "A Product Update",
                "markdown": "Details",
                "area": "Platform",
                "type": "New",
                "action_required": False,
                "sources": [],
                "contributors": [],
                "assets": [],
            }
        ],
        "other_changes": [],
    }


def test_load_product_updates_bundle_preserves_the_approved_runtime_content(tmp_path) -> None:
    entry_id = "fd0c7319-fcc0-5d28-b89b-09ca20a59262"
    bundle_path = tmp_path / "product-updates.bundle.json"
    bundle_path.write_text(json.dumps(_bundle(entry_id)), encoding="utf-8")

    bundle = load_product_updates_bundle(bundle_path)

    assert bundle.content_ref == "b" * 40
    assert bundle.entries[0].id == UUID(entry_id)
    assert bundle.entries[0].markdown == "Details"


def test_load_product_updates_bundle_rejects_duplicate_entry_ids(tmp_path) -> None:
    entry_id = "fd0c7319-fcc0-5d28-b89b-09ca20a59262"
    bundle = _bundle(entry_id)
    entries = bundle["entries"]
    assert isinstance(entries, list)
    bundle["entries"] = [entries[0], entries[0]]
    bundle_path = tmp_path / "product-updates.bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(ValidationError, match="duplicate entry id"):
        load_product_updates_bundle(bundle_path)


def test_running_bundle_matches_the_backend_contract() -> None:
    bundle = load_product_updates_bundle(
        __import__("pathlib").Path("/app/product-updates.bundle.json")
    )

    assert bundle.schema_version == 1
    assert visible_entry_ids(bundle) == {
        entry.id for entry in bundle.entries if entry.in_app is not False
    }
