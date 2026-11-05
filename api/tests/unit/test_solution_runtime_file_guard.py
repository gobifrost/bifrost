"""Runtime data must remain writable while Solution definitions stay protected."""

from uuid import uuid4

from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.models.orm.workflows import Workflow
from src.services.solutions.guard import _instance_is_managed


def test_runtime_file_metadata_is_exempt_from_the_definition_write_guard():
    metadata = FileMetadata(
        solution_id=uuid4(), location="migration-data", path="state.json"
    )
    assert not _instance_is_managed(metadata)


def test_file_policies_and_workflows_remain_deploy_owned():
    assert _instance_is_managed(
        FilePolicy(solution_id=uuid4(), location="migration-data", path="")
    )
    assert _instance_is_managed(Workflow(solution_id=uuid4()))
