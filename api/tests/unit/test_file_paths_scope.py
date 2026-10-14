"""Storage scope → metadata org mapping shared by write and move paths."""

from uuid import UUID

import botocore.session

from shared.file_paths import SCOPE_SKIP, scope_to_org_id

ORG = "22222222-2222-2222-2222-222222222222"


def test_scope_to_org_id_maps_storage_scopes() -> None:
    assert scope_to_org_id("docs", ORG) == UUID(ORG)
    assert scope_to_org_id("docs", "global") is None
    assert scope_to_org_id("workspace", None) is None
    assert scope_to_org_id("docs", None) is SCOPE_SKIP
    assert scope_to_org_id("docs", "not-a-uuid") is SCOPE_SKIP


def test_botocore_supports_conditional_multipart_complete() -> None:
    """files.move relies on IfNoneMatch on CompleteMultipartUpload."""
    model = botocore.session.get_session().get_service_model("s3")
    members = model.operation_model("CompleteMultipartUpload").input_shape.members
    assert "IfNoneMatch" in members
