import pytest

from shared.file_paths import resolve_s3_key
from src.models.contracts.policies import FilePolicies
from src.services.file_policy_service import FilePolicyService
from src.services.file_storage import FileStorageService
from src.services.file_structure_service import FileStructureService


@pytest.mark.asyncio
async def test_list_prefix_returns_direct_children_only(db_session):
    storage = FileStorageService(db_session)
    await storage.write_raw_to_s3(resolve_s3_key("gallery", "global", "a.png"), b"x")
    await storage.write_raw_to_s3(
        resolve_s3_key("gallery", "global", "sub/b.png"), b"y"
    )

    svc = FileStructureService(db_session)
    entries = await svc.list_prefix(org_id=None, location="gallery", prefix="")
    names = {(e.name, e.kind) for e in entries}
    assert ("a.png", "file") in names
    assert ("sub", "folder") in names
    assert ("b.png", "file") not in names  # not a direct child


@pytest.mark.asyncio
async def test_list_shares_excludes_reserved_includes_uploads_readonly(db_session):
    storage = FileStorageService(db_session)
    await storage.write_raw_to_s3(resolve_s3_key("gallery", "global", "a.png"), b"x")
    await storage.write_raw_to_s3(resolve_s3_key("uploads", "global", "u.png"), b"x")
    await storage.write_raw_to_s3("_solutions/global/workflows/run.py", b"x")
    await storage.write_raw_to_s3("_solution_artifacts/global/source.zip", b"x")

    svc = FileStructureService(db_session)
    shares = await svc.list_shares(org_id=None)
    by_loc = {s.location: s for s in shares}
    assert "gallery" in by_loc and by_loc["gallery"].read_only is False
    assert "uploads" in by_loc and by_loc["uploads"].read_only is True
    assert "workspace" not in by_loc
    assert "temp" not in by_loc
    assert "_solutions" not in by_loc
    assert "_solution_artifacts" not in by_loc


@pytest.mark.asyncio
async def test_list_shares_includes_policied_but_empty_share(db_session):
    await FilePolicyService(db_session).upsert_policy(
        organization_id=None,
        location="reports",
        path="",
        policies=FilePolicies(policies=[]),
    )
    svc = FileStructureService(db_session)
    shares = await svc.list_shares(org_id=None)
    assert "reports" in {s.location for s in shares}  # has_policy, no files yet
