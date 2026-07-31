"""Administrator-reviewed promotion of pinned private Solution revisions."""

from __future__ import annotations

import hashlib
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.solution_builder import (
    PromotionEntityCounts,
    PromotionResultDTO,
    PromotionReviewDTO,
    PromotionTargetRequest,
)
from src.models.orm.applications import Application
from src.models.orm.config import Config
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.models.orm.users import Role, UserRole
from src.services.audit import emit_audit
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.solutions.deploy import SolutionDeployConflict
from src.services.solutions.scope_rehome import rehome_solution_owned_rows
from src.services.solutions.write_lock import solution_write_lock
from src.services.solutions.zip_install import (
    deploy_zip_to_solution_path,
    preview_zip_path,
)


class PromotionNotFound(Exception):
    """No requested private promotion exists for this id."""


class PromotionBlocked(Exception):
    """The pinned review is stale, incomplete, or conflicts at target scope."""

    def __init__(self, blockers: Iterable[str]):
        self.blockers = list(blockers)
        super().__init__("; ".join(self.blockers))


def _zip_hashes(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if not name.endswith("/")
        }


async def _revision_diff(
    solution_id: UUID,
    pinned: UUID,
    prior: UUID | None,
) -> list[str]:
    if prior is None:
        return []
    with tempfile.TemporaryDirectory(prefix="bifrost-promotion-diff-") as tmp:
        root = Path(tmp)
        current_path = root / "current.zip"
        prior_path = root / "prior.zip"
        storage = SolutionRevisionStorage(solution_id)
        if not await storage.copy_to_path(pinned, current_path):
            raise PromotionBlocked(["Pinned source artifact is missing"])
        if not await storage.copy_to_path(prior, prior_path):
            return []
        current = _zip_hashes(current_path)
        previous = _zip_hashes(prior_path)
        return sorted(
            path
            for path in set(current) | set(previous)
            if current.get(path) != previous.get(path)
        )


async def _count(
    db: AsyncSession,
    model: type,
    solution_id: UUID,
) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(model)
                .where(model.solution_id == solution_id)
            )
        ).scalar_one()
    )


async def _load_requested(
    db: AsyncSession,
    solution_id: UUID,
) -> tuple[Solution, SolutionBuilderProject]:
    row = (
        await db.execute(
            select(Solution, SolutionBuilderProject)
            .join(
                SolutionBuilderProject,
                SolutionBuilderProject.solution_id == Solution.id,
            )
            .where(
                Solution.id == solution_id,
                Solution.visibility == "private",
                SolutionBuilderProject.promotion_status == "requested",
            )
        )
    ).one_or_none()
    if row is None:
        raise PromotionNotFound(str(solution_id))
    return row[0], row[1]


async def promotion_review(
    db: AsyncSession,
    solution_id: UUID,
) -> PromotionReviewDTO:
    solution, project = await _load_requested(db, solution_id)
    pinned_id = project.promotion_revision_id
    revision = (
        await db.get(SolutionSourceRevision, pinned_id)
        if pinned_id is not None
        else None
    )
    blockers: list[str] = []
    if pinned_id is None or revision is None or revision.solution_id != solution_id:
        blockers.append("Promotion has no valid pinned source revision")
    if project.current_revision_id != pinned_id:
        blockers.append("Current source changed after promotion was requested")
    if project.deployed_revision_id != pinned_id:
        blockers.append("Pinned source is no longer the deployed preview")

    turn = (
        (
            await db.execute(
                select(SolutionBuilderTurn)
                .join(
                    SolutionBuilderSession,
                    SolutionBuilderSession.id == SolutionBuilderTurn.session_id,
                )
                .where(
                    SolutionBuilderSession.solution_id == solution_id,
                    SolutionBuilderTurn.output_revision_id == pinned_id,
                )
                .order_by(SolutionBuilderTurn.completed_at.desc().nullslast())
            )
        )
        .scalars()
        .first()
    )
    build = (
        await db.get(SolutionBuildJob, turn.build_job_id)
        if turn is not None and turn.build_job_id is not None
        else None
    )
    deploy = (
        await db.get(SolutionDeployJob, turn.deploy_job_id)
        if turn is not None and turn.deploy_job_id is not None
        else None
    )
    if turn is None or turn.status != "succeeded":
        blockers.append("Pinned revision does not have a successful builder turn")
    if deploy is None or deploy.status != "succeeded":
        blockers.append("Pinned revision deploy is not green")
    if build is not None and build.status != "succeeded":
        blockers.append("Pinned revision app build is not green")

    unresolved_roles = sorted(
        str(name)
        for name in ((deploy.result or {}).get("roles_unresolved") or [])
    ) if deploy is not None else []
    connection_names = sorted(
        (
            await db.execute(
                select(SolutionConnectionSchema.integration_name).where(
                    SolutionConnectionSchema.solution_id == solution_id
                )
            )
        ).scalars().all()
    )

    prior_revision_id = (
        (
            await db.execute(
                select(SolutionBuilderTurn.output_revision_id)
                .join(
                    SolutionBuilderSession,
                    SolutionBuilderSession.id == SolutionBuilderTurn.session_id,
                )
                .where(
                    SolutionBuilderSession.solution_id == solution_id,
                    SolutionBuilderTurn.status == "succeeded",
                    SolutionBuilderTurn.output_revision_id.is_not(None),
                    SolutionBuilderTurn.output_revision_id != pinned_id,
                    SolutionBuilderTurn.completed_at
                    < (turn.completed_at if turn is not None else project.updated_at),
                )
                .order_by(SolutionBuilderTurn.completed_at.desc())
            )
        )
        .scalars()
        .first()
    )

    source_counts = PromotionEntityCounts()
    changed_paths: list[str] = []
    if revision is not None and pinned_id is not None:
        with tempfile.TemporaryDirectory(prefix="bifrost-promotion-review-") as tmp:
            source_path = Path(tmp) / "source.zip"
            if await SolutionRevisionStorage(solution_id).copy_to_path(
                pinned_id,
                source_path,
            ):
                preview = preview_zip_path(source_path)
                source_counts = PromotionEntityCounts(
                    workflows=len(preview.workflows),
                    tables=len(preview.tables),
                    apps=len(preview.apps),
                    forms=len(preview.forms),
                    agents=len(preview.agents),
                    claims=len(preview.claims),
                    configs=len(preview.config_schemas),
                    files=len(preview.file_locations),
                    file_policies=len(preview.file_policies),
                    policy_rules=await _count(db, PolicyRule, solution_id),
                    events=len(preview.events),
                )
            else:
                blockers.append("Pinned source artifact is missing")
        changed_paths = await _revision_diff(
            solution_id,
            pinned_id,
            prior_revision_id,
        )

    config_keys = (
        (
            await db.execute(
                select(SolutionConfigSchema.key)
                .where(SolutionConfigSchema.solution_id == solution_id)
                .where(
                    ~select(Config.id)
                    .where(
                        Config.solution_id == solution_id,
                        Config.key == SolutionConfigSchema.key,
                    )
                    .exists()
                )
                .where(
                    select(Config.id)
                    .where(
                        Config.solution_id.is_(None),
                        Config.organization_id == solution.organization_id,
                        Config.key == SolutionConfigSchema.key,
                    )
                    .exists()
                )
            )
        )
        .scalars()
        .all()
    )

    return PromotionReviewDTO(
        solution_id=solution.id,
        slug=solution.slug,
        name=solution.name,
        owner_user_id=solution.owner_user_id,
        organization_id=solution.organization_id,
        promotion_status=project.promotion_status,
        pinned_revision_id=pinned_id,
        source_sha256=revision.source_sha256 if revision else None,
        source_size_bytes=revision.size_bytes if revision else None,
        prior_deployed_revision_id=prior_revision_id,
        changed_paths=changed_paths,
        requested_at=project.promotion_requested_at,
        requested_by=project.promotion_requested_by,
        current_revision_id=project.current_revision_id,
        deployed_revision_id=project.deployed_revision_id,
        build_job_id=turn.build_job_id if turn else None,
        deploy_job_id=turn.deploy_job_id if turn else None,
        build_status=build.status if build else None,
        deploy_status=deploy.status if deploy else None,
        entity_counts=source_counts,
        unresolved_roles=unresolved_roles,
        connection_names=connection_names,
        config_keys_requiring_reentry_for_global=sorted(config_keys),
        global_repo_access=solution.global_repo_access,
        ready=not blockers,
        blockers=blockers,
    )


async def list_promotion_reviews(db: AsyncSession) -> list[PromotionReviewDTO]:
    ids = (
        await db.execute(
            select(SolutionBuilderProject.solution_id)
            .join(Solution, Solution.id == SolutionBuilderProject.solution_id)
            .where(
                Solution.visibility == "private",
                SolutionBuilderProject.promotion_status == "requested",
            )
            .order_by(SolutionBuilderProject.promotion_requested_at.asc())
        )
    ).scalars().all()
    return [await promotion_review(db, solution_id) for solution_id in ids]


async def _assert_target_collisions(
    db: AsyncSession,
    solution: Solution,
    target_org: UUID | None,
) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('bifrost:solution:' || :s))"),
        {"s": solution.slug},
    )
    scope_predicate = (
        Solution.organization_id.is_(None)
        if target_org is None
        else Solution.organization_id == target_org
    )
    sibling = (
        await db.execute(
            select(Solution.id).where(
                Solution.id != solution.id,
                Solution.slug == solution.slug,
                Solution.visibility == "shared",
                scope_predicate,
            )
        )
    ).scalar_one_or_none()
    if sibling is not None:
        raise PromotionBlocked(
            [f"A shared Solution with slug '{solution.slug}' already exists"]
        )

    app_slugs = (
        await db.execute(
            select(Application.slug).where(Application.solution_id == solution.id)
        )
    ).scalars().all()
    for slug in app_slugs:
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('bifrost:appslug:' || :s))"
            ),
            {"s": slug},
        )
        predicates = [
            Application.slug == slug,
            Application.solution_id != solution.id,
        ]
        if target_org is not None:
            predicates.append(
                or_(
                    Application.organization_id == target_org,
                    Application.organization_id.is_(None),
                )
            )
        collision = (
            await db.execute(select(Application.id).where(*predicates))
        ).scalars().first()
        if collision is not None:
            raise PromotionBlocked(
                [f"App slug '{slug}' conflicts with a visible app"]
            )


async def _assign_role_users(
    db: AsyncSession,
    assignments: dict[str, list[UUID]],
    *,
    assigned_by: UUID,
) -> None:
    for role_name, user_ids in assignments.items():
        role_id = (
            await db.execute(select(Role.id).where(Role.name == role_name))
        ).scalar_one_or_none()
        if role_id is None:
            raise PromotionBlocked([f"Reviewed role '{role_name}' does not exist"])
        existing = set(
            (
                await db.execute(
                    select(UserRole.user_id).where(
                        UserRole.role_id == role_id,
                        UserRole.user_id.in_(user_ids),
                    )
                )
            ).scalars().all()
        )
        db.add_all(
            UserRole(
                role_id=role_id,
                user_id=user_id,
                assigned_by=str(assigned_by),
            )
            for user_id in user_ids
            if user_id not in existing
        )


async def promote_private_solution(
    db: AsyncSession,
    solution_id: UUID,
    request: PromotionTargetRequest,
    *,
    admin_user_id: UUID,
) -> PromotionResultDTO:
    review = await promotion_review(db, solution_id)
    blockers = list(review.blockers)
    if review.unresolved_roles and not request.approve_role_creation:
        blockers.append("Unresolved role creation has not been approved")
    missing_connections = sorted(
        set(review.connection_names) - set(request.approved_connection_names)
    )
    if missing_connections:
        blockers.append(
            "Connection declarations are not approved: "
            + ", ".join(missing_connections)
        )
    if blockers:
        raise PromotionBlocked(blockers)
    assert review.pinned_revision_id is not None

    target_org = review.organization_id if request.target == "company" else None
    if request.target == "company" and target_org is None:
        raise PromotionBlocked(["Company promotion requires an organization"])

    try:
        async with solution_write_lock(solution_id):
            solution, project = await _load_requested(db, solution_id)
            await db.refresh(project, with_for_update=True)
            if (
                project.promotion_revision_id != review.pinned_revision_id
                or project.current_revision_id != review.pinned_revision_id
                or project.deployed_revision_id != review.pinned_revision_id
            ):
                raise PromotionBlocked(
                    ["Promotion request changed during administrator review"]
                )
            await _assert_target_collisions(db, solution, target_org)

            with tempfile.TemporaryDirectory(
                prefix="bifrost-promotion-replay-"
            ) as tmp:
                source_path = Path(tmp) / "source.zip"
                copied = await SolutionRevisionStorage(solution_id).copy_to_path(
                    review.pinned_revision_id,
                    source_path,
                )
                if not copied:
                    raise PromotionBlocked(["Pinned source artifact is missing"])

                solution.organization_id = target_org
                solution.global_repo_access = request.allow_global_repo_access
                await db.flush()
                result = await deploy_zip_to_solution_path(
                    db,
                    solution,
                    source_path,
                    force=True,
                    promotion=True,
                )
                await rehome_solution_owned_rows(
                    db,
                    solution_id=solution_id,
                    organization_id=target_org,
                )
                await _assign_role_users(
                    db,
                    request.role_user_assignments,
                    assigned_by=admin_user_id,
                )
                # Generated Python, schedules, events, and autonomous agents
                # remain inactive because promotion replay keeps the runtime
                # suppression arm enabled. Visibility is the final DB mutation.
                solution.visibility = "shared"
                project.promotion_status = "none"
                await emit_audit(
                    db,
                    "solution.promote",
                    resource_type="solution",
                    resource_id=solution_id,
                    details={
                        "target": request.target,
                        "revision_id": str(review.pinned_revision_id),
                        "source_sha256": review.source_sha256,
                        "roles_created": list(result.roles_created),
                        "approved_connections": request.approved_connection_names,
                        "global_repo_access": request.allow_global_repo_access,
                    },
                )
                # The pinned bundle is already the deployed preview, so a
                # pre-commit finalize can only republish reviewed deterministic
                # artifacts. It keeps visibility and role/scope changes atomic.
                await result.finalize_s3()
                await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise PromotionBlocked(
            ["Target scope changed concurrently and now conflicts"]
        ) from exc
    except (PromotionBlocked, SolutionDeployConflict):
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise

    return PromotionResultDTO(
        solution_id=solution_id,
        target=request.target,
        visibility="shared",
        organization_id=target_org,
        promoted_revision_id=review.pinned_revision_id,
        roles_created=list(result.roles_created),
    )
