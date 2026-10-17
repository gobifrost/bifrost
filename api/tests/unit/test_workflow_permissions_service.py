"""Pure contract and digest behaviour for workflow permission requests."""

import pytest
from pydantic import ValidationError

from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    WorkflowPermissionGrantSpec,
)
from src.services.workflow_permissions import request_digest


def _restricted(*permissions: str) -> RequestedWorkflowPermissions:
    return RequestedWorkflowPermissions(
        mode="restricted",
        grants=[WorkflowPermissionGrantSpec(permission=permission) for permission in permissions],
    )


def test_digest_ignores_grant_order():
    a = _restricted("tables.read", "workflows.execute")
    b = _restricted("workflows.execute", "tables.read")
    assert request_digest(a) == request_digest(b)


def test_digest_changes_with_mode():
    full = RequestedWorkflowPermissions(mode="full")
    assert request_digest(full) != request_digest(_restricted())


def test_digest_changes_with_grants():
    assert request_digest(_restricted("tables.read")) != request_digest(_restricted("tables.readwrite"))


def test_rejects_obsolete_placement_fields():
    with pytest.raises(ValidationError, match="boundary"):
        WorkflowPermissionGrantSpec.model_validate(
            {"permission": "tables.read", "boundary": "platform"}
        )


def test_rejects_unknown_permission_domain():
    with pytest.raises(ValidationError, match="Unknown permission domain"):
        WorkflowPermissionGrantSpec(permission="nonsense.read")


def test_rejects_bad_permission_suffix():
    with pytest.raises(ValidationError, match="Invalid permission format"):
        WorkflowPermissionGrantSpec(permission="tables.write")


def test_full_mode_rejects_grants():
    with pytest.raises(ValidationError, match="empty when mode is 'full'"):
        RequestedWorkflowPermissions(
            mode="full", grants=[WorkflowPermissionGrantSpec(permission="tables.read")]
        )


def test_rejects_duplicate_grants():
    with pytest.raises(ValidationError, match="duplicate grants"):
        _restricted("tables.read", "tables.read")
