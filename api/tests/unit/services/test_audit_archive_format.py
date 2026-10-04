import gzip
import hashlib
import json
from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from src.services.audit_retention.format import (
    ArchiveRow,
    ArchiveVerifyError,
    build_segments,
    encode_rows,
    segment_key,
    verify_segment,
)

ORG = UUID("11111111-1111-1111-1111-111111111111")


def _row(n: int, *, org: UUID | None = ORG, at: datetime | None = None, details=None) -> ArchiveRow:
    return ArchiveRow(
        id=UUID(int=n), created_at=at or datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        organization_id=org, user_id=None, action="access.check", resource_type="table",
        resource_id=None, outcome="success", source="workflow", operation_id=None, surface="workflow",
        execution_id=None, ip_address=None, user_agent=None,
        details=details if details is not None else {"enforced": False},
        actor_email=None, actor_name=None, organization_name="Example Org",
    )


def test_same_rows_give_same_bytes_and_key() -> None:
    rows = [_row(2, details={"z": 1.5, "a": {"ü": ["x", None]}}), _row(1)]
    first, second = build_segments(rows), build_segments(list(reversed(rows)))
    assert [s.body for s in first] == [s.body for s in second]
    assert first[0].key == segment_key(ORG, date(2026, 1, 2), first[0].sha256)
    assert first[0].key.startswith(f"_audit/v1/org={ORG}/day=2026-01-02/")
    assert [r.id for r in first[0].rows] == [UUID(int=1), UUID(int=2)]


def test_segments_split_by_org_and_utc_day() -> None:
    late = datetime(2026, 1, 2, 23, 59, 59, 999999, tzinfo=timezone.utc)
    next_day = datetime(2026, 1, 3, 0, 0, 0, tzinfo=timezone.utc)
    segments = build_segments([_row(1, at=late), _row(2, at=next_day), _row(3, org=None, at=late)])
    assert {(s.organization_id, s.day) for s in segments} == {(ORG, date(2026, 1, 2)), (ORG, date(2026, 1, 3)), (None, date(2026, 1, 2))}
    assert any(s.key.startswith("_audit/v1/org=global/day=2026-01-02/") for s in segments)


def test_lines_are_sorted_compact_and_carry_schema() -> None:
    blob = encode_rows([_row(1)])
    line = gzip.decompress(blob).decode().splitlines()[0]
    parsed = json.loads(line)
    assert parsed["schema"] == "audit.v1"
    assert parsed["created_at"] == "2026-01-02T03:04:05.000000+00:00"
    assert line == json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_verify_round_trips_rows() -> None:
    segment = build_segments([_row(1), _row(2)])[0]
    assert verify_segment(segment.body, sha256=segment.sha256, ids=segment.ids) == list(segment.rows)


@pytest.mark.parametrize("mutate", ["sha", "ids", "count", "gzip"])
def test_verify_rejects_any_mismatch(mutate: str) -> None:
    segment = build_segments([_row(1), _row(2)])[0]
    blob, sha, ids = segment.body, segment.sha256, list(segment.ids)
    if mutate == "sha":
        sha = "0" * 64
    elif mutate == "ids":
        ids = [UUID(int=2), UUID(int=1)]
    elif mutate == "count":
        ids = ids[:1]
    else:
        blob = blob[:-4]
        sha = hashlib.sha256(blob).hexdigest()  # checksum matches; content is truncated
    with pytest.raises(ArchiveVerifyError):
        verify_segment(blob, sha256=sha, ids=ids)
