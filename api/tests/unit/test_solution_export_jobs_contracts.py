from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts.solutions import (
    SolutionExportJobCreate,
    SolutionExportJobPublic,
    SolutionExportOptions,
)


def test_solution_export_options_requires_at_least_one_include() -> None:
    with pytest.raises(ValidationError, match="At least one include_\\* option"):
        SolutionExportOptions(
            include_configs=False,
            include_secrets=False,
            include_tables=False,
            include_files=False,
        )


def test_backup_export_options_allow_configs_secrets_tables_files_and_password() -> None:
    create = SolutionExportJobCreate(
        options=SolutionExportOptions(
            include_configs=True,
            include_secrets=True,
            include_tables=True,
            include_files=True,
            password="correct horse battery staple",
        )
    )

    assert create.options.include_configs is True
    assert create.options.include_secrets is True
    assert create.options.include_tables is True
    assert create.options.include_files is True
    assert create.options.password == "correct horse battery staple"


def test_public_job_response_exposes_status_progress_download_and_artifact_fields() -> None:
    now = datetime.now(timezone.utc)
    job = SolutionExportJobPublic(
        id=uuid4(),
        solution_id=uuid4(),
        organization_id=uuid4(),
        requested_by_id=uuid4(),
        status="completed",
        progress_percent=100,
        message="Export ready",
        failure_message=None,
        artifact_size_bytes=12345,
        artifact_sha256="a" * 64,
        expires_at=now,
        completed_at=now,
        created_at=now,
        updated_at=now,
        download_url="https://example.test/download",
    )

    assert job.status == "completed"
    assert job.progress_percent == 100
    assert job.download_url == "https://example.test/download"
    assert job.expires_at == now
    assert job.failure_message is None
    assert job.artifact_size_bytes == 12345
    assert job.artifact_sha256 == "a" * 64
