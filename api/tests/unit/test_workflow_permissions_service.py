"""Pure contract and digest behaviour for workflow permission requests."""

import pytest
from pydantic import ValidationError

from src.models.contracts.workflow_permissions import (
    RequestedWorkflowPermissions,
    WorkflowPermissionGrantSpec,
)
from src.services.workflow_permissions import request_digest


def _restricted(*grants: tuple[str, str | None]) -> RequestedWorkflowPermissions:
    return RequestedWorkflowPermissions(
        mode="restricted",
        grants=[WorkflowPermissionGrantSpec(permission=p, boundary=b) for p, b in grants],
    )


def test_digest_ignores_grant_order():
    a = _restricted(("tables.read", None), ("workflows.execute", "platform"))
    b = _restricted(("workflows.execute", "platform"), ("tables.read", None))
    assert request_digest(a) == request_digest(b)


def test_digest_changes_with_mode():
    full = RequestedWorkflowPermissions(mode="full")
    assert request_digest(full) != request_digest(_restricted())


def test_digest_changes_with_grants():
    assert request_digest(_restricted(("tables.read", None))) != request_digest(
        _restricted(("tables.readwrite", None))
    )


def test_digest_changes_with_boundary():
    assert request_digest(_restricted(("tables.read", None))) != request_digest(
        _restricted(("tables.read", "platform"))
    )


def test_rejects_unknown_permission_domain():
    with pytest.raises(ValidationError, match="Unknown permission domain"):
        WorkflowPermissionGrantSpec(permission="nonsense.read")


def test_rejects_bad_permission_suffix():
    with pytest.raises(ValidationError, match="Invalid permission format"):
        WorkflowPermissionGrantSpec(permission="tables.write")


def test_rejects_unknown_boundary():
    with pytest.raises(ValidationError):
        WorkflowPermissionGrantSpec(permission="tables.read", boundary="everywhere")


def test_full_mode_rejects_grants():
    with pytest.raises(ValidationError, match="empty when mode is 'full'"):
        RequestedWorkflowPermissions(
            mode="full", grants=[WorkflowPermissionGrantSpec(permission="tables.read")]
        )


def test_rejects_duplicate_grants():
    with pytest.raises(ValidationError, match="duplicate grants"):
        _restricted(("tables.read", None), ("tables.read", None))
