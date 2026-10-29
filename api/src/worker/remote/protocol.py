"""Small, dependency-free authenticated wire protocol for remote launchers.

The launcher uses this module before application imports are permitted.  A
WebSocket gives framing only; every control message is fenced by a persisted
session generation and authenticated hello.  This avoids treating a transport
connection as worker identity.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import tempfile
import time
from base64 import b64decode, b64encode
from dataclasses import asdict, dataclass
from collections.abc import Iterator
from typing import Any, Mapping
from uuid import UUID


AUTH_DOMAIN = b"bifrost.remote-control.hello.v1\0"
# The WebSocket transports bounded frames. Logical workflow contexts/results
# are fragmented so this transport limit is never an execution payload limit.
MAX_FRAME_BYTES = 1 << 20
# Reassembly spills after one transport frame. Logical execution contexts and
# results keep the same size contract as the local process pipe; the wire
# bound limits only an individual WebSocket frame, never the logical payload.
FRAGMENT_SPOOL_BYTES = MAX_FRAME_BYTES
MAX_CLOCK_SKEW_SECONDS = 60


class ProtocolError(ValueError):
    """A peer supplied an invalid or unsafe control frame."""


class AuthenticationError(ProtocolError):
    """A peer did not prove possession of the platform control secret."""


@dataclass(frozen=True)
class Hello:
    launcher_id: UUID
    incarnation_id: UUID
    job_uid: str | None
    pod_uid: str | None
    hostname: str
    form: str
    issued_at: int
    nonce: str
    hello_sequence: int

    def __post_init__(self) -> None:
        if not self.hostname or self.form not in {"job", "static"}:
            raise ProtocolError("hello identity fields are required")
        if self.form == "job" and (not self.job_uid or not self.pod_uid):
            raise ProtocolError("job hello requires Kubernetes identities")
        if self.form == "static" and (self.job_uid is not None or self.pod_uid is not None):
            raise ProtocolError("static hello cannot claim Kubernetes identities")
        if len(self.nonce) < 24 or len(self.nonce) > 256:
            raise ProtocolError("hello nonce must be 24-256 characters")
        if self.hello_sequence < 1:
            raise ProtocolError("hello sequence must be positive")

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            {
                "incarnation_id": str(self.incarnation_id),
                "issued_at": self.issued_at,
                "form": self.form,
                "job_uid": self.job_uid,
                "launcher_id": str(self.launcher_id),
                "nonce": self.nonce,
                "pod_uid": self.pod_uid,
                "hostname": self.hostname,
                "hello_sequence": self.hello_sequence,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()


@dataclass(frozen=True)
class SignedHello(Hello):
    proof: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SignedHello":
        try:
            return cls(
                launcher_id=UUID(str(value["launcher_id"])),
                incarnation_id=UUID(str(value["incarnation_id"])),
                job_uid=str(value["job_uid"]) if value.get("job_uid") is not None else None,
                pod_uid=str(value["pod_uid"]) if value.get("pod_uid") is not None else None,
                hostname=str(value["hostname"]),
                form=str(value["form"]),
                issued_at=int(value["issued_at"]),
                nonce=str(value["nonce"]),
                hello_sequence=int(value["hello_sequence"]),
                proof=str(value["proof"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError("invalid hello") from exc

    def to_dict(self) -> dict[str, Any]:
        return {key: str(value) if isinstance(value, UUID) else value for key, value in asdict(self).items()}


@dataclass(frozen=True)
class ControlEnvelope:
    session_generation: int
    sequence: int
    kind: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.session_generation < 1 or self.sequence < 1:
            raise ProtocolError("session generation and sequence must be positive")
        if not self.kind or len(self.kind) > 64:
            raise ProtocolError("invalid control message kind")

    def to_json(self) -> str:
        raw = json.dumps(asdict(self), default=str, separators=(",", ":"), sort_keys=True)
        if len(raw.encode()) > MAX_FRAME_BYTES:
            raise ProtocolError("control frame exceeds limit")
        return raw

    @classmethod
    def from_json(cls, raw: str | bytes) -> "ControlEnvelope":
        return cls._parse_json(raw, max_bytes=MAX_FRAME_BYTES)

    @classmethod
    def _parse_json(cls, raw: str | bytes, *, max_bytes: int) -> "ControlEnvelope":
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not isinstance(raw, str) or len(raw.encode()) > max_bytes:
            raise ProtocolError("control frame exceeds limit")
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ProtocolError("control frame is not JSON") from exc
        return cls._from_value(value)

    @classmethod
    def _from_value(cls, value: Any) -> "ControlEnvelope":
        if not isinstance(value, dict):
            raise ProtocolError("control frame must be a JSON object")
        try:
            payload = value["payload"]
            if not isinstance(payload, dict):
                raise TypeError("payload")
            return cls(
                session_generation=int(value["session_generation"]),
                sequence=int(value["sequence"]),
                kind=str(value["kind"]),
                payload=payload,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid control envelope") from exc


def fragment(envelope: ControlEnvelope) -> Iterator[ControlEnvelope]:
    """Encode one logical envelope into bounded ordered transport frames."""
    raw = json.dumps(asdict(envelope), default=str, separators=(",", ":"), sort_keys=True).encode()
    # A base64 fragment plus JSON envelope must fit in MAX_FRAME_BYTES.
    chunk_size = ((MAX_FRAME_BYTES - 1024) * 3) // 4
    if len(raw) <= chunk_size:
        yield envelope
        return
    message_id = secrets.token_urlsafe(18)
    total = (len(raw) + chunk_size - 1) // chunk_size
    for index, offset in enumerate(range(0, len(raw), chunk_size)):
        yield ControlEnvelope(
            session_generation=envelope.session_generation,
            sequence=envelope.sequence + index,
            kind="fragment",
            payload={
                "message_id": message_id,
                "index": index,
                "total": total,
                "data": b64encode(raw[offset : offset + chunk_size]).decode(),
            },
        )


class FragmentAssembler:
    """One bounded, ordered message buffer per control connection."""

    def __init__(self) -> None:
        self._message_id: str | None = None
        self._total = 0
        self._count = 0
        self._spool: Any = None

    def feed(self, envelope: ControlEnvelope) -> ControlEnvelope | None:
        if envelope.kind != "fragment":
            if self._message_id is not None:
                raise ProtocolError("interleaved control message fragments")
            return envelope
        payload = envelope.payload
        try:
            message_id = str(payload["message_id"])
            index = int(payload["index"])
            total = int(payload["total"])
            data = b64decode(str(payload["data"]), validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            self._reset()
            raise ProtocolError("invalid control fragment") from exc
        if total < 2 or total > 2**31 or index < 0 or index >= total:
            self._reset()
            raise ProtocolError("invalid control fragment sequence")
        if self._message_id is None:
            if index != 0:
                raise ProtocolError("fragment sequence must start at zero")
            self._message_id, self._total = message_id, total
            self._spool = tempfile.SpooledTemporaryFile(max_size=FRAGMENT_SPOOL_BYTES, mode="w+b")
        if message_id != self._message_id or total != self._total or index != self._count:
            self._reset()
            raise ProtocolError("out-of-order control fragments")
        assert self._spool is not None
        self._spool.write(data)
        self._count += 1
        if self._count != total:
            return None
        self._spool.seek(0)
        try:
            return ControlEnvelope._from_value(json.load(self._spool))
        except (TypeError, ValueError) as exc:
            raise ProtocolError("reassembled control message is not JSON") from exc
        finally:
            self._reset()

    def _reset(self) -> None:
        self._message_id = None
        self._total = 0
        self._count = 0
        if self._spool is not None:
            self._spool.close()
        self._spool = None


def new_nonce() -> str:
    return secrets.token_urlsafe(32)


def nonce_digest(nonce: str) -> str:
    """Persist only a one-way replay marker, never a bearer credential."""
    return hashlib.sha256(nonce.encode()).hexdigest()


def sign_hello(secret: str, hello: Hello) -> SignedHello:
    proof = hmac.new(secret.encode(), AUTH_DOMAIN + hello.canonical_bytes(), hashlib.sha256).hexdigest()
    return SignedHello(**asdict(hello), proof=proof)


def verify_hello(
    secret: str,
    hello: SignedHello,
    *,
    now: int | None = None,
    max_clock_skew_seconds: int = MAX_CLOCK_SKEW_SECONDS,
) -> Hello:
    if now is None:
        now = int(time.time())
    if abs(now - hello.issued_at) > max_clock_skew_seconds:
        raise AuthenticationError("hello proof expired")
    unsigned = Hello(
        launcher_id=hello.launcher_id,
        incarnation_id=hello.incarnation_id,
        job_uid=hello.job_uid,
        pod_uid=hello.pod_uid,
        hostname=hello.hostname,
        form=hello.form,
        issued_at=hello.issued_at,
        nonce=hello.nonce,
        hello_sequence=hello.hello_sequence,
    )
    expected = sign_hello(secret, unsigned).proof
    if not hmac.compare_digest(hello.proof, expected):
        raise AuthenticationError("invalid hello proof")
    return unsigned

