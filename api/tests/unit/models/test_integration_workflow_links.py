from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts.integrations import IntegrationCreate, IntegrationUpdate


def test_workflow_links_are_optional_and_empty_list_clears():
    assert IntegrationCreate(name="Directory").workflow_links == []
    assert "workflow_links" not in IntegrationUpdate().model_fields_set
    assert IntegrationUpdate(workflow_links=[]).workflow_links == []


def test_link_validation_rejects_duplicates_and_empty_or_overlong_labels():
    workflow_id = uuid4()
    link = {"workflow_id": workflow_id, "label": "Sync Now", "pinned": True}
    with pytest.raises(ValidationError, match="only be linked once"):
        IntegrationUpdate(workflow_links=[link, link])
    for label in ["", "   ", "a" * 81]:
        with pytest.raises(ValidationError):
            IntegrationCreate(name="Directory", workflow_links=[{**link, "label": label}])


def test_manifest_link_roundtrip_and_git_import_values():
    from bifrost.manifest import ManifestIntegration, parse_manifest, serialize_manifest, Manifest
    from bifrost.manifest_codec import Destination

    link = {"workflow_id": str(uuid4()), "label": "Sync Now", "pinned": True}
    integration = ManifestIntegration(id=str(uuid4()), name="Directory", workflow_links=[link])
    manifest = Manifest(integrations={integration.id: integration})
    restored = parse_manifest(serialize_manifest(manifest)).integrations[integration.id]
    assert restored.to_orm_values(Destination.GIT_SYNC).direct["workflow_links"] == [link]


@pytest.mark.asyncio
async def test_solution_connection_links_remap_without_mutating_source():
    from unittest.mock import AsyncMock, MagicMock
    from src.models.orm.solutions import Solution
    from src.services.solutions.deploy import SolutionBundle, SolutionDeployer, solution_entity_id

    workflow_id, solution_id = uuid4(), uuid4()
    link = {"workflow_id": str(workflow_id), "label": "Sync Now", "pinned": True}
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    bundle = SolutionBundle(solution=Solution(id=solution_id, slug="directory", name="Directory"), workflows=[{"id": str(workflow_id)}], connection_schemas=[{"integration_name": "Directory", "template": {"workflow_links": [link]}}])
    remapped = await SolutionDeployer(db)._remapped_bundle(bundle)
    restored_link = remapped.connection_schemas[0]["template"]["workflow_links"][0]
    assert restored_link == {**link, "workflow_id": str(solution_entity_id(solution_id, workflow_id))}
    assert bundle.connection_schemas[0]["template"]["workflow_links"][0] == link


def test_partial_manifest_import_remaps_linked_workflows():
    from bifrost.manifest import Manifest, ManifestIntegration
    from src.services.manifest_import import PartialImportSelection, rewrite_manifest_references

    workflow_id, target_id = uuid4(), uuid4()
    integration = ManifestIntegration(id=str(uuid4()), name="Directory", workflow_links=[{"workflow_id": str(workflow_id), "label": "Sync Now"}])
    manifest = Manifest(integrations={integration.id: integration})
    selection = PartialImportSelection(included_source_ids=frozenset({integration.id}), target_ids={workflow_id: target_id})
    restored = rewrite_manifest_references(manifest, selection)
    assert restored.integrations[integration.id].workflow_links[0].workflow_id == str(target_id)


def test_manifest_rejects_invalid_workflow_shortcuts():
    from bifrost.manifest import ManifestIntegration
    with pytest.raises(ValidationError):
        ManifestIntegration(id=str(uuid4()), name="Invalid", workflow_links=[{"workflow_id":"not-a-uuid", "label":"Sync"}])
    with pytest.raises(ValidationError):
        ManifestIntegration(id=str(uuid4()), name="Invalid", workflow_links=[{"workflow_id":str(uuid4()), "label":"   "}])
