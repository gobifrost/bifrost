"""Deterministic gzip-JSONL segment format for archived audit events.

Pure functions only: no database, no object storage. The same rows always
encode to the same bytes, so a segment's key (which embeds its checksum) is
stable across retries.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any
from uuid import UUID

SCHEMA = "audit.v1"
SCHEMA_VERSION = 1


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _uuid(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _parse_uuid(value: str | None) -> UUID | None:
    return UUID(value) if value is not None else None


@dataclass(frozen=True)
class ArchiveRow:
    id: UUID
    created_at: datetime
    organization_id: UUID | None
    user_id: UUID | None
    impersonated_by_user_id: UUID | None
    impersonation_session_id: UUID | None
    action: str
    resource_type: str | None
    resource_id: UUID | None
    outcome: str
    source: str
    operation_id: str | None
    surface: str | None
    execution_id: UUID | None
    ip_address: str | None
    user_agent: str | None
    details: dict[str, Any] | None
    # Snapshots taken at archive time; the live rows may be renamed or deleted later.
    actor_email: str | None
    actor_name: str | None
    impersonated_by_email: str | None
    impersonated_by_name: str | None
    organization_name: str | None

    def to_line(self) -> dict[str, Any]:
        line = asdict(self)
        line["schema"] = SCHEMA
        line["created_at"] = _iso(self.created_at)
        for name in (
            "id", "organization_id", "user_id", "impersonated_by_user_id",
            "impersonation_session_id", "resource_id", "execution_id",
        ):
            line[name] = _uuid(getattr(self, name))
        return line

    @classmethod
    def from_line(cls, line: dict[str, Any]) -> ArchiveRow:
        if not isinstance(line, dict):
            raise ValueError(f"audit archive line is not an object: {type(line).__name__}")
        if line["schema"] != SCHEMA:
            raise ValueError(f"unsupported audit archive schema: {line['schema']!r}")
        return cls(
            id=UUID(line["id"]),
            created_at=datetime.fromisoformat(line["created_at"]),
            organization_id=_parse_uuid(line["organization_id"]),
            user_id=_parse_uuid(line["user_id"]),
            # Version 1 segments written before browser impersonation had no
            # provenance keys. Missing values are intentionally treated as
            # absent so existing retained audit history stays exportable.
            impersonated_by_user_id=_parse_uuid(line.get("impersonated_by_user_id")),
            impersonation_session_id=_parse_uuid(line.get("impersonation_session_id")),
            action=line["action"],
            resource_type=line["resource_type"],
            resource_id=_parse_uuid(line["resource_id"]),
            outcome=line["outcome"],
            source=line["source"],
            operation_id=line["operation_id"],
            surface=line["surface"],
            execution_id=_parse_uuid(line["execution_id"]),
            ip_address=line["ip_address"],
            user_agent=line["user_agent"],
            details=line["details"],
            actor_email=line["actor_email"],
            actor_name=line["actor_name"],
            impersonated_by_email=line.get("impersonated_by_email"),
            impersonated_by_name=line.get("impersonated_by_name"),
            organization_name=line["organization_name"],
        )


@dataclass(frozen=True)
class Segment:
    organization_id: UUID | None
    day: date
    rows: tuple[ArchiveRow, ...]  # sorted (created_at, id)
    body: bytes  # gzip bytes
    sha256: str  # hex of body

    @property
    def key(self) -> str:
        return segment_key(self.organization_id, self.day, self.sha256)

    @property
    def ids(self) -> tuple[UUID, ...]:
        return tuple(row.id for row in self.rows)


class ArchiveVerifyError(Exception):
    """A stored segment does not match what was archived."""


def segment_key(organization_id: UUID | None, day: date, sha256: str) -> str:
    org = str(organization_id) if organization_id is not None else "global"
    return f"_audit/v1/org={org}/day={day.isoformat()}/{sha256}.jsonl.gz"


def encode_line(row: ArchiveRow) -> bytes:
    return (json.dumps(row.to_line(), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def line_size(row: ArchiveRow) -> int:
    """Bytes of the encoded line, including the trailing newline."""
    return len(encode_line(row))


def encode_rows(rows: Sequence[ArchiveRow]) -> bytes:
    ordered = sorted(rows, key=lambda r: (r.created_at, r.id))
    return gzip.compress(b"".join(encode_line(r) for r in ordered), compresslevel=6, mtime=0)


def build_segments(rows: Sequence[ArchiveRow]) -> list[Segment]:
    groups: dict[tuple[UUID | None, date], list[ArchiveRow]] = {}
    for row in rows:
        groups.setdefault((row.organization_id, row.created_at.astimezone(timezone.utc).date()), []).append(row)
    segments = []
    for (org, day), members in sorted(groups.items(), key=lambda item: (item[0][1], str(item[0][0]))):
        ordered = tuple(sorted(members, key=lambda r: (r.created_at, r.id)))
        body = encode_rows(ordered)
        segments.append(Segment(org, day, ordered, body, hashlib.sha256(body).hexdigest()))
    return segments


def verify_checksum(blob: bytes, sha256: str) -> None:
    if hashlib.sha256(blob).hexdigest() != sha256:
        raise ArchiveVerifyError("stored object checksum does not match")


def _decode_expanded_segment(expanded: bytes) -> list[ArchiveRow]:
    try:
        text = expanded.decode()
        # Split on "\n" only: str.splitlines() also splits on U+2028/U+2029/U+0085,
        # which ensure_ascii=False leaves unescaped inside JSON strings.
        lines = text.split("\n")
        if not lines[-1]:
            lines.pop()
        return [ArchiveRow.from_line(json.loads(line)) for line in lines]
    except (OSError, EOFError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ArchiveVerifyError(f"stored object is unreadable: {exc}") from exc


def decode_segment(blob: bytes) -> list[ArchiveRow]:
    try:
        expanded = gzip.decompress(blob)
    except (OSError, EOFError, ValueError, TypeError, AttributeError) as exc:
        raise ArchiveVerifyError(f"stored object is unreadable: {exc}") from exc
    return _decode_expanded_segment(expanded)


def verify_segment(
    blob: bytes,
    *,
    sha256: str,
    expected_rows: Sequence[ArchiveRow],
) -> list[ArchiveRow]:
    verify_checksum(blob, sha256)
    max_expanded_bytes = sum(line_size(row) for row in expected_rows)
    try:
        with gzip.GzipFile(fileobj=BytesIO(blob), mode="rb") as compressed:
            expanded = compressed.read(max_expanded_bytes + 1)
    except (OSError, EOFError, ValueError, TypeError, AttributeError) as exc:
        raise ArchiveVerifyError(f"stored object is unreadable: {exc}") from exc
    if len(expanded) > max_expanded_bytes:
        raise ArchiveVerifyError("stored object exceeds the expected expanded size")
    rows = _decode_expanded_segment(expanded)
    if [row.to_line() for row in rows] != [row.to_line() for row in expected_rows]:
        raise ArchiveVerifyError("stored object rows do not match the expected canonical rows")
    return rows
