"""Solution runtime file-location declarations."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from bifrost.manifest import Manifest, ManifestFiles
from src.models.orm.file_metadata import FileMetadata, FilePolicy
from src.models.orm.solution_file_location import SolutionFileLocation
from src.models.orm.solutions import Solution
from src.services.manifest_import import ManifestResolver
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployConflict,
    SolutionDeployer,
)

pytestmark = pytest.mark.e2e


async def _make_solution(db) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"files-{uuid.uuid4().hex[:8]}",
        name="Files",
        organization_id=None,
    )
    db.add(sol)
    await db.flush()
    return sol


async def _locations(db, solution_id) -> list[tuple[str, int]]:
    rows = (
        await db.execute(
            select(SolutionFileLocation)
            .where(SolutionFileLocation.solution_id == solution_id)
            .order_by(SolutionFileLocation.position, SolutionFileLocation.location)
        )
    ).scalars().all()
    return [(row.location, row.position) for row in rows]


async def _file_policies(db, solution_id) -> list[tuple[str, str, dict]]:
    rows = (
        await db.execute(
            select(FilePolicy)
            .where(FilePolicy.solution_id == solution_id)
            .order_by(FilePolicy.location, FilePolicy.path)
        )
    ).scalars().all()
    return [(row.location, row.path, row.policies) for row in rows]


async def test_deploy_persists_and_reconciles_file_locations(db_session) -> None:
    sol = await _make_solution(db_session)
    deployer = SolutionDeployer(db_session)

    await deployer.deploy(
        SolutionBundle(solution=sol, file_locations=["reports", "invoices"])
    )
    await db_session.flush()

    assert await _locations(db_session, sol.id) == [
        ("reports", 0),
        ("invoices", 1),
    ]

    await deployer.deploy(SolutionBundle(solution=sol, file_locations=["invoices"]))
    await db_session.flush()

    assert await _locations(db_session, sol.id) == [("invoices", 0)]


async def test_deploy_seeds_solution_file_location_admin_policy(db_session) -> None:
    sol = await _make_solution(db_session)

    await SolutionDeployer(db_session).deploy(
        SolutionBundle(solution=sol, file_locations=["reports", "invoices"])
    )
    await db_session.flush()

    assert await _file_policies(db_session, sol.id) == [
        ("invoices", "", {"policies": [{"$ref": "admin_bypass"}]}),
        ("reports", "", {"policies": [{"$ref": "admin_bypass"}]}),
    ]


@pytest.mark.parametrize(
    ("locations", "message"),
    [
        (["reports", "reports"], "duplicate file location"),
        (["workspace"], "workspace"),
        (["Reports"], "Invalid location"),
        (["team/reports"], "Invalid location"),
        (["_repo"], "Invalid location"),
        (["my_reports"], "Invalid location"),
    ],
)
async def test_deploy_rejects_invalid_file_locations(
    db_session, locations: list[str], message: str
) -> None:
    sol = await _make_solution(db_session)

    with pytest.raises(SolutionDeployConflict, match=message):
        await SolutionDeployer(db_session).deploy(
            SolutionBundle(solution=sol, file_locations=locations)
        )


async def test_deploy_rejects_removing_location_with_existing_files(db_session) -> None:
    sol = await _make_solution(db_session)
    await SolutionDeployer(db_session).deploy(
        SolutionBundle(solution=sol, file_locations=["reports", "invoices"])
    )
    db_session.add(
        FileMetadata(
            organization_id=None,
            solution_id=sol.id,
            location="reports",
            path="q1.pdf",
            s3_key=f"reports/{sol.id}/q1.pdf",
            size_bytes=1,
            sha256="ab" * 32,
        )
    )
    await db_session.flush()

    with pytest.raises(
        SolutionDeployConflict,
        match="cannot remove file location 'reports' while files still exist",
    ):
        await SolutionDeployer(db_session).deploy(
            SolutionBundle(solution=sol, file_locations=["invoices"])
        )


async def test_manifest_import_persists_file_locations_for_install(
    db_session, tmp_path
) -> None:
    sol = await _make_solution(db_session)
    manifest = Manifest(files=ManifestFiles(locations=["reports", "invoices"]))

    await ManifestResolver(db_session).plan_import(
        manifest,
        work_dir=tmp_path,
        install_id=sol.id,
    )
    await db_session.flush()

    assert await _locations(db_session, sol.id) == [
        ("reports", 0),
        ("invoices", 1),
    ]


async def test_generate_manifest_includes_solution_file_locations(db_session) -> None:
    from src.services.manifest_generator import generate_manifest

    sol = await _make_solution(db_session)
    db_session.add(SolutionFileLocation(solution_id=sol.id, location="invoices", position=1))
    db_session.add(SolutionFileLocation(solution_id=sol.id, location="reports", position=0))
    await db_session.flush()

    manifest = await generate_manifest(db_session, solution_id=sol.id)

    assert manifest.files.locations == ["reports", "invoices"]
