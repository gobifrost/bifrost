import gzip
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from src.services.audit_retention.format import (
    ArchiveRow,
    ArchiveVerifyError,
    build_segments,
    encode_rows,
    line_size,
    segment_key,
    verify_segment,
)

ORG = UUID("11111111-1111-1111-1111-111111111111")
_DEFAULT_DETAILS: Any = object()


def _row(n: int, *, org: UUID | None = ORG, at: datetime | None = None, details: Any = _DEFAULT_DETAILS) -> ArchiveRow:
    return ArchiveRow(
        id=UUID(int=n), created_at=at or datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        organization_id=org, user_id=None, action="access.check", resource_type="table",
        resource_id=None, outcome="success", source="workflow", operation_id=None, surface="workflow",
        execution_id=None, ip_address=None, user_agent=None,
        details={"enforced": False} if details is _DEFAULT_DETAILS else details,
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


def test_unicode_line_separators_in_details_survive_verify() -> None:
    row = _row(1, details={"text": "a\u2028b\u2029c\u0085d"})
    segment = build_segments([row])[0]
    assert verify_segment(segment.body, sha256=segment.sha256, ids=segment.ids) == [row]


def _good_line() -> dict[str, Any]:
    return _row(1).to_line()


@pytest.mark.parametrize(
    "body",
    [
        gzip.compress(b"[]\n"),
        gzip.compress(b"5\n"),
        gzip.compress((json.dumps({**_good_line(), "id": 5}) + "\n").encode()),
        gzip.compress(b"{not json}\n"),
        gzip.compress((json.dumps({**_good_line(), "schema": "audit.v2"}) + "\n").encode()),
        gzip.compress((json.dumps(_good_line()) + "\n\n").encode()),
        b"not gzip at all",
    ],
    ids=["array-line", "scalar-line", "wrong-typed-id", "invalid-json", "wrong-schema", "blank-line", "not-gzip"],
)
def test_verify_rejects_malformed_content_with_matching_checksum(body: bytes) -> None:
    with pytest.raises(ArchiveVerifyError):
        verify_segment(body, sha256=hashlib.sha256(body).hexdigest(), ids=[UUID(int=1)])


def test_every_field_round_trips_and_non_utc_rows_land_on_their_utc_day() -> None:
    est = timezone(timedelta(hours=-5))
    full = ArchiveRow(
        id=UUID(int=1), created_at=datetime(2026, 1, 2, 23, 30, 0, 123456, tzinfo=est),
        organization_id=ORG, user_id=UUID(int=2), action="user.update", resource_type="user",
        resource_id=UUID(int=3), outcome="denied", source="http", operation_id="users.update",
        surface="web", execution_id=UUID(int=4), ip_address="203.0.113.7", user_agent="curl/8",
        details={"nested": {"list": [1, 2.5, None, "x"]}}, actor_email="a@example.com",
        actor_name="Ada", organization_name="Example Org",
    )
    bare = _row(5, details=None, at=datetime(2026, 1, 3, 4, 30, 0, tzinfo=timezone.utc))
    assert ArchiveRow.from_line(full.to_line()) == full
    assert set(full.to_line()) == {"schema", *ArchiveRow.__dataclass_fields__}

    segments = build_segments([full, bare])
    assert [s.day for s in segments] == [date(2026, 1, 3)]
    segment = segments[0]
    assert segment.ids == (UUID(int=5), UUID(int=1))
    assert verify_segment(segment.body, sha256=segment.sha256, ids=segment.ids) == [bare, full]


def test_line_size_is_the_encoded_line_length() -> None:
    row = _row(1, details={"ü": "x"})
    assert line_size(row) == len(gzip.decompress(encode_rows([row])))
