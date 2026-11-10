"""Provider-neutral object reads; versions are concurrency tokens, not hashes."""

from abc import abstractmethod
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Literal, NewType, Protocol, Self

ObjectVersion = NewType("ObjectVersion", str)
PageCursor = NewType("PageCursor", str)
ErrorKind = Literal[
    "not_found", "permission_denied", "precondition_failed", "conflict",
    "unavailable", "backend_error", "unsupported", "namespace_not_found",
]


class StorageConfigurationError(ValueError):
    """The selected provider cannot be constructed from its configuration."""


class StorageError(Exception):
    """Stable failure category; provider codes are diagnostic details only."""

    def __init__(self, kind: ErrorKind, *, provider_code: str, status_code: int | None = None):
        super().__init__(f"Object storage request failed ({kind})")
        self.kind = kind
        self.provider_code = provider_code
        self.status_code = status_code


@dataclass(frozen=True)
class ContentProperties:
    content_type: str | None = None
    content_disposition: str | None = None
    content_encoding: str | None = None
    content_language: str | None = None
    cache_control: str | None = None
    http_expires: str | None = None


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    version: ObjectVersion
    modified_at: datetime | None
    properties: ContentProperties
    metadata: Mapping[str, str]
    http_etag: str | None


class ObjectBody(Protocol):
    @abstractmethod
    async def read(self, amt: int | None = None) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def iter_chunks(self, chunk_size: int = 8 * 1024 * 1024) -> AsyncIterator[bytes]:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class ObjectRead:
    info: ObjectInfo
    body: ObjectBody


@dataclass(frozen=True)
class WriteOptions:
    properties: ContentProperties = field(default_factory=ContentProperties)
    metadata: Mapping[str, str] = field(default_factory=dict)
    create_only: bool = False
    expected_version: ObjectVersion | None = None

    def __post_init__(self) -> None:
        if self.create_only and self.expected_version is not None:
            raise ValueError("Create-only and expected-version writes are mutually exclusive")


@dataclass(frozen=True)
class WriteResult:
    version: ObjectVersion


@dataclass(frozen=True)
class StreamWriteResult:
    version: ObjectVersion
    size: int
    sha256: str


@dataclass(frozen=True)
class CopyOptions:
    properties: ContentProperties | None = None
    metadata: Mapping[str, str] | None = None
    create_only: bool = False
    expected_version: ObjectVersion | None = None

    def __post_init__(self) -> None:
        if self.create_only and self.expected_version is not None:
            raise ValueError("Create-only and expected-version copies are mutually exclusive")


class CopyConditionFailed(StorageError):
    def __init__(self, side: Literal["source", "destination"]):
        super().__init__("precondition_failed", provider_code="CopyConditionFailed", status_code=412)
        self.side: Literal["source", "destination"] = side


@dataclass(frozen=True)
class ObjectSummary:
    key: str
    size: int
    version: ObjectVersion
    modified_at: datetime | None


@dataclass(frozen=True)
class ObjectPage:
    objects: tuple[ObjectSummary, ...]
    prefixes: tuple[str, ...]
    next_cursor: PageCursor | None


@dataclass(frozen=True)
class SignedRequest:
    method: Literal["GET", "PUT"]
    url: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    expires_in: int


class ObjectStore(Protocol):
    """One configured namespace, with provider-owned connection lifecycle.

    open_read owns its response body until context exit, including cancellation.
    A supplied expected_version must be checked atomically with the read.
    Conditional writes must commit their check atomically with the object.
    Page cursors and versions belong to this provider/namespace, not a migration.
    """

    @abstractmethod
    async def __aenter__(self) -> Self:
        raise NotImplementedError

    @abstractmethod
    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def serialize_version(self, version: ObjectVersion) -> str:
        """Encode durable witnesses in this provider's established representation."""
        raise NotImplementedError

    @abstractmethod
    def restore_version(self, value: str) -> ObjectVersion:
        """Restore a persisted witness without treating it as a content hash."""
        raise NotImplementedError

    @abstractmethod
    async def probe(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stat(self, key: str) -> ObjectInfo:
        raise NotImplementedError

    @abstractmethod
    def open_read(
        self, key: str, *, expected_version: ObjectVersion | None = None,
    ) -> AbstractAsyncContextManager[ObjectRead]:
        raise NotImplementedError

    @abstractmethod
    async def write(self, key: str, content: bytes, *, options: WriteOptions | None = None) -> WriteResult:
        raise NotImplementedError

    @abstractmethod
    async def write_stream(
        self, key: str, content: AsyncIterator[bytes], *, options: WriteOptions | None = None,
    ) -> StreamWriteResult:
        raise NotImplementedError

    @abstractmethod
    async def copy(
        self, source: ObjectInfo, destination_key: str, *, options: CopyOptions | None = None,
    ) -> WriteResult:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, key: str, *, expected_version: ObjectVersion | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete_many(self, keys: Sequence[str]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def list_page(
        self, prefix: str = "", *, delimiter: str | None = None,
        cursor: PageCursor | None = None, limit: int = 1000,
    ) -> ObjectPage:
        raise NotImplementedError

    @abstractmethod
    async def signed_request(
        self, key: str, method: Literal["GET", "PUT"], *, expires_in: int = 3600,
        content_type: str | None = None, content_length: int | None = None, response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> SignedRequest:
        raise NotImplementedError


class SyncObjectStore(Protocol):
    """Narrow whole-object reads for parent-process import/setup callers."""

    @abstractmethod
    def read(self, key: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


SnapshotGuard = Callable[[Mapping[str, ObjectVersion]], None]


class WorkspaceMirror(Protocol):
    """Provider-owned bulk transfer, including uncommitted files and Git internals."""

    @property
    @abstractmethod
    def follows_symlinks(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def download(self, prefix: str, target: Path) -> None:
        raise NotImplementedError

    @abstractmethod
    async def upload(self, source: Path, prefix: str, *, validate_snapshot: SnapshotGuard | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, prefix: str) -> None:
        raise NotImplementedError
