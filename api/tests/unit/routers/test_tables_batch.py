"""Unit tests for tables batch endpoint contract models."""
from __future__ import annotations

import pytest

from src.models.contracts.tables import (
    DocumentBatchCreate,
    DocumentBatchDeleteRequest,
    DocumentCreate,
)


def test_document_create_defaults():
    """DocumentCreate now supports optional id and upsert flag."""
    doc = DocumentCreate(data={"key": "value"})
    assert doc.id is None
    assert doc.upsert is False
    assert doc.data == {"key": "value"}


def test_document_create_with_id_and_upsert():
    doc = DocumentCreate(id="my-id", data={"x": 1}, upsert=True)
    assert doc.id == "my-id"
    assert doc.upsert is True


def test_document_batch_create_insert_mode():
    payload = DocumentBatchCreate(
        documents=[
            {"data": {"x": 1}},
            {"id": "row-2", "data": {"x": 2}},
        ]
    )
    assert len(payload.documents) == 2
    assert payload.upsert is False
    assert payload.documents[0].id is None
    assert payload.documents[1].id == "row-2"


def test_document_batch_create_upsert_mode():
    payload = DocumentBatchCreate(
        documents=[{"id": "row-1", "data": {"y": 42}}],
        upsert=True,
    )
    assert payload.upsert is True
    assert payload.documents[0].id == "row-1"


def test_document_batch_delete_request():
    req = DocumentBatchDeleteRequest(ids=["a", "b", "c"])
    assert req.ids == ["a", "b", "c"]


def test_document_batch_delete_request_requires_ids():
    with pytest.raises(Exception):
        DocumentBatchDeleteRequest()  # type: ignore[call-arg]
