"""File storage service package."""

from .models import (
    AvailableReplacementInfo,
    FileDiagnosticInfo,
    PendingDeactivationInfo,
    WorkflowIdConflictInfo,
    WriteResult,
)
from .entity_detector import detect_platform_entity_type, detect_python_entity_type
from .object_storage import FileObjectStorage
from .service import FileStorageService, get_file_storage_service

__all__ = [
    # Main Service
    "FileStorageService",
    "get_file_storage_service",
    # Models
    "AvailableReplacementInfo",
    "FileDiagnosticInfo",
    "PendingDeactivationInfo",
    "WorkflowIdConflictInfo",
    "WriteResult",
    # Entity Detection
    "detect_platform_entity_type",
    "detect_python_entity_type",
    # S3 Client
    "FileObjectStorage",
]
