"""
Chat artifact service (Chat V2 sub-project 4 — Artifacts).

The mirror image of the attachment service (input): a tool/skill returns an
artifact contract (file metadata + inline base64 bytes + an optional inert
preview), the trusted execution layer persists the bytes to S3 under
``_artifacts/{conversation_id}/{uuid}_{filename}``, and only metadata lives in
the ``message_artifacts`` table. Download URLs are minted scoped + expiring at
render time by the API — a tool NEVER returns a URL, and ``content_base64`` is
stripped before any value reaches the model or the client.

See Part C of docs/superpowers/specs/2026-06-17-agent-skill-bundles-and-
capabilities-design.md.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.agents import (
    ArtifactFilePublic,
    ArtifactInfo,
    ArtifactPreviewPublic,
    ArtifactToolContract,
)
from src.models.orm import MessageArtifact
from src.services.file_storage.service import get_file_storage_service

logger = logging.getLogger(__name__)

# S3 key prefix for artifact blobs — sibling to _attachments/ (artifacts are
# output and may outlive the message; attachments are message-bound input).
ARTIFACTS_PREFIX = "_artifacts/"

# Per-file cap mirrors the attachment limit; a generated report shouldn't exceed it.
MAX_ARTIFACT_FILE_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_FILES_PER_ARTIFACT = 10

# Inert preview kinds we render. No html/svg/react in v1.
PREVIEW_KINDS = {"markdown", "image", "pdf", "csv"}

# Default presigned-download TTL (seconds). Short — minted per render.
DOWNLOAD_URL_TTL_SECONDS = 600


class ArtifactError(Exception):
    """Raised for an invalid artifact contract returned by a tool."""


def extract_artifact_contract(result: Any) -> ArtifactToolContract | None:
    """Pull a well-formed ``artifact`` object off a tool result, or None.

    A tool result is artifact-bearing when it is a dict with an ``artifact`` key
    that validates against :class:`ArtifactToolContract`. A malformed artifact is
    logged and ignored (it must never break the turn) — the tool's normal text
    result still flows to the model.
    """
    if not isinstance(result, dict):
        return None
    raw = result.get("artifact")
    if raw is None:
        return None
    try:
        return ArtifactToolContract.model_validate(raw)
    except Exception:  # malformed artifact must not break the turn
        logger.warning("Ignoring malformed artifact contract on tool result", exc_info=True)
        return None


def strip_artifact_from_result(result: Any) -> Any:
    """Return the tool result with the ``artifact`` key removed.

    The inline base64 bytes must never reach the LLM history or the client. The
    model sees the tool's normal text result; the artifact is surfaced via the
    ``artifact_generated`` chunk + the persisted record instead.
    """
    if isinstance(result, dict) and "artifact" in result:
        return {k: v for k, v in result.items() if k != "artifact"}
    return result


class ArtifactService:
    """Persists tool-returned artifact files and assembles render metadata."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def build_s3_key(conversation_id: UUID, artifact_id: UUID, filename: str) -> str:
        """Build the S3 key: _artifacts/{conversation_id}/{uuid}_{filename}."""
        safe_name = filename.replace("/", "_").replace("\\", "_")
        return f"{ARTIFACTS_PREFIX}{conversation_id}/{artifact_id}_{safe_name}"

    async def persist(
        self,
        *,
        contract: ArtifactToolContract,
        conversation_id: UUID,
        message_id: UUID,
    ) -> ArtifactInfo:
        """Persist an artifact contract's files to S3 + DB; return render metadata.

        Decodes each file's inline base64, writes the bytes to S3, creates a
        ``MessageArtifact`` row per file, and resolves the preview to point at the
        persisted file id (image/pdf/csv) or carry the inline markdown. Does NOT
        commit — the caller commits.

        Raises :class:`ArtifactError` on an invalid contract (no files, too many,
        oversize, bad base64, unsupported preview kind).
        """
        if not contract.files:
            raise ArtifactError("Artifact contract has no files.")
        if len(contract.files) > MAX_FILES_PER_ARTIFACT:
            raise ArtifactError(
                f"Too many artifact files ({len(contract.files)}). "
                f"Maximum is {MAX_FILES_PER_ARTIFACT}."
            )

        storage = get_file_storage_service(self.db)

        # The markdown preview is inlined and points at no file; an image/pdf/csv
        # preview names a file we must map to its persisted artifact id.
        preview_kind = contract.preview.kind if contract.preview else None
        if preview_kind is not None and preview_kind not in PREVIEW_KINDS:
            raise ArtifactError(f"Unsupported artifact preview kind: {preview_kind!r}")
        preview_ref_name = (
            contract.preview.content_ref if contract.preview else None
        )

        public_files: list[ArtifactFilePublic] = []
        preview_file_id: UUID | None = None

        for f in contract.files:
            try:
                content = base64.b64decode(f.content_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ArtifactError(f"Artifact file {f.name!r} has invalid base64.") from exc
            size_bytes = len(content)
            if size_bytes <= 0:
                raise ArtifactError(f"Artifact file {f.name!r} is empty.")
            if size_bytes > MAX_ARTIFACT_FILE_BYTES:
                raise ArtifactError(
                    f"Artifact file {f.name!r} too large ({size_bytes} bytes). "
                    f"Maximum is {MAX_ARTIFACT_FILE_BYTES // (1024 * 1024)} MB."
                )

            artifact_id = uuid4()
            s3_key = self.build_s3_key(conversation_id, artifact_id, f.name)
            await storage.write_raw_to_s3(s3_key, content)
            sha256 = hashlib.sha256(content).hexdigest()

            row = MessageArtifact(
                id=artifact_id,
                message_id=message_id,
                conversation_id=conversation_id,
                title=contract.title,
                s3_key=s3_key,
                filename=f.name,
                content_type=f.content_type,
                size_bytes=size_bytes,
                sha256=sha256,
                preview_kind=preview_kind,
                preview_inline=(
                    contract.preview.inline
                    if contract.preview and preview_kind == "markdown"
                    else None
                ),
            )
            self.db.add(row)

            public_files.append(
                ArtifactFilePublic(
                    id=artifact_id,
                    filename=f.name,
                    content_type=f.content_type,
                    size_bytes=size_bytes,
                    sha256=sha256,
                )
            )
            if preview_ref_name is not None and f.name == preview_ref_name:
                preview_file_id = artifact_id

        await self.db.flush()

        preview: ArtifactPreviewPublic | None = None
        if preview_kind == "markdown" and contract.preview is not None:
            preview = ArtifactPreviewPublic(kind="markdown", inline=contract.preview.inline)
        elif preview_kind in {"image", "pdf", "csv"}:
            if preview_file_id is None:
                # A preview that names a missing file is a soft error: keep the
                # files, drop the preview rather than failing the whole turn.
                logger.warning(
                    "Artifact preview content_ref %r matched no file; dropping preview",
                    preview_ref_name,
                )
            else:
                preview = ArtifactPreviewPublic(kind=preview_kind, file_id=preview_file_id)

        return ArtifactInfo(title=contract.title, preview=preview, files=public_files)

    async def get_for_download(
        self, *, artifact_id: UUID, conversation_id: UUID
    ) -> MessageArtifact | None:
        """Load an artifact file scoped to its conversation (ownership enforced upstream)."""
        result = await self.db.execute(
            select(MessageArtifact)
            .where(MessageArtifact.id == artifact_id)
            .where(MessageArtifact.conversation_id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def mint_download_url(
        self, artifact: MessageArtifact, *, expires_in: int = DOWNLOAD_URL_TTL_SECONDS
    ) -> str:
        """Mint a scoped, expiring presigned download URL for one artifact file."""
        storage = get_file_storage_service(self.db)
        return await storage.generate_presigned_download_url(
            artifact.s3_key, expires_in=expires_in
        )


async def load_message_artifacts(
    db: AsyncSession, message_id: UUID
) -> list[MessageArtifact]:
    """Load artifacts produced for a message, ordered by creation."""
    result = await db.execute(
        select(MessageArtifact)
        .where(MessageArtifact.message_id == message_id)
        .order_by(MessageArtifact.created_at)
    )
    return list(result.scalars().all())


def build_artifact_infos(rows: list[MessageArtifact]) -> list[ArtifactInfo]:
    """Reconstruct render-time ArtifactInfo objects from persisted rows.

    Each ``MessageArtifact`` row is one file. All files a single tool emitted
    share the same ``title``/``preview_kind`` (denormalized onto every row), so we
    group consecutive rows by title into one artifact. The preview is resolved
    back to point at the file whose preview metadata is set:
      - markdown → carried inline on the file row
      - image/pdf/csv → the row whose preview_kind is set names the preview file
    """
    if not rows:
        return []

    # Group by (title) preserving creation order. A message rarely has more than
    # one artifact group, but multiple tool calls in a turn can each emit one.
    groups: list[list[MessageArtifact]] = []
    for row in rows:
        if groups and groups[-1][0].title == row.title:
            groups[-1].append(row)
        else:
            groups.append([row])

    infos: list[ArtifactInfo] = []
    for group in groups:
        files = [
            ArtifactFilePublic(
                id=r.id,
                filename=r.filename,
                content_type=r.content_type,
                size_bytes=r.size_bytes,
                sha256=r.sha256,
            )
            for r in group
        ]
        preview: ArtifactPreviewPublic | None = None
        for r in group:
            if r.preview_kind == "markdown" and r.preview_inline is not None:
                preview = ArtifactPreviewPublic(kind="markdown", inline=r.preview_inline)
                break
            if r.preview_kind in {"image", "pdf", "csv"}:
                preview = ArtifactPreviewPublic(kind=r.preview_kind, file_id=r.id)  # type: ignore[arg-type]
                break
        infos.append(ArtifactInfo(title=group[0].title, preview=preview, files=files))
    return infos
