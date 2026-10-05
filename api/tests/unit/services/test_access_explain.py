"""Re-judging stored access checks: resolving operations and restoring notes."""

from __future__ import annotations

from uuid import uuid4

from src.models.orm.audit import AuditLog
from src.services.access_explain import entry_for_key, stored_note
from src.services.access_list import ACCESS_LIST


def test_entry_for_key_resolves_catalog_id_and_route():
    by_route = entry_for_key("GET /api/audit")
    assert by_route is not None and by_route.permission == "roleassignments.read"
    catalogued = next(e for e in ACCESS_LIST if e.operation_id and e.mcp_tool is None)
    assert entry_for_key(catalogued.operation_id) == catalogued
    assert entry_for_key("nope.nothing") is None


def test_stored_note_restores_targets():
    row = AuditLog(
        action="access.check",
        resource_type="scope_switch",
        user_id=uuid4(),
        details={"inputs": {"operation": "GET /api/x", "target": "*"}},
    )
    assert stored_note(row).target == "*"
    row.details = {"inputs": {"operation": "GET /api/x", "target": None}}
    assert stored_note(row).target is None
    org = uuid4()
    row.details = {"inputs": {"operation": "GET /api/x", "target": str(org), "allowed": True}}
    note = stored_note(row)
    assert note.target == org and note.facts == {"allowed": True}
