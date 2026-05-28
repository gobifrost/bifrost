from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.routers.nuclei_scans import (
    BulkStateUpdateRequest,
    FindingInput,
    ScanIngestRequest,
    _occurrence_key,
    _stable_id,
    router,
)


def test_finding_input_normalizes_severity() -> None:
    finding = FindingInput(
        template_id="cve-2026-test",
        host="https://example.test",
        severity="HIGH",
        matched_at=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert finding.severity == "high"


def test_finding_input_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError):
        FindingInput(
            template_id="cve-2026-test",
            host="https://example.test",
            severity="urgent",
            matched_at=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
        )


def test_occurrence_key_and_stable_id_are_deterministic() -> None:
    org_id = UUID("11111111-1111-1111-1111-111111111111")
    finding = FindingInput(
        template_id="cve-2026-test",
        host="https://example.test",
        severity="medium",
        matched_at=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
    )

    key = _occurrence_key(org_id, finding)

    assert key == (
        "11111111-1111-1111-1111-111111111111:"
        "cve-2026-test:https://example.test:2026-04-26T12:00:00+00:00"
    )
    assert _stable_id(key) == _stable_id(key)


def test_ingest_request_defaults_to_complete_empty_findings() -> None:
    request = ScanIngestRequest(scan_host_device_id="scanner-1")

    assert request.findings == []
    assert request.incomplete is False


def test_ingest_request_accepts_incomplete_flag() -> None:
    request = ScanIngestRequest(scan_host_device_id="scanner-1", incomplete=True)

    assert request.incomplete is True


def test_bulk_state_update_only_allows_lifecycle_states() -> None:
    assert BulkStateUpdateRequest(finding_ids=["finding-1"], state="resolved").state == "resolved"

    with pytest.raises(ValidationError):
        BulkStateUpdateRequest(finding_ids=["finding-1"], state="open")


def test_router_registers_scan_endpoints() -> None:
    paths = {route.path for route in router.routes}

    assert {
        "/api/scans/runs/{org_id}",
        "/api/scans/runs/{org_id}/{run_id}/ingest",
        "/api/scans/history/{org_id}",
        "/api/scans/findings/{org_id}",
        "/api/scans/findings/{org_id}/bulk-state",
    }.issubset(paths)
