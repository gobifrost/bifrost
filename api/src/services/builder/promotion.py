"""Administrator-reviewed promotion of pinned private Solution revisions."""

from __future__ import annotations

import hashlib
import tempfile
import zipfile
from collections.abc import Collection, Iterable
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, or_, select, text, update
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
    SolutionBuilderRelease,
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)
from src.models.orm.organizations import Organization
from src.models.orm.solution_config_schema import SolutionConfigSchema
from src.models.orm.solution_connection_schema import SolutionConnectionSchema
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.models.orm.role_assignments import RoleAssignment
from src.models.orm.users import Role, User
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


def _promotion_build_ids(
    turn: SolutionBuilderTurn | None,
    deploy: SolutionDeployJob | None,
) -> list[UUID]:
    """Return every app build attached to the pinned deploy, in deploy order."""

    raw_ids = (deploy.result or {}).get("build_job_ids") if deploy is not None else None
    if not raw_ids and turn is not None and turn.build_job_id is not None:
        return [turn.build_job_id]
    if raw_ids is None:
        return []
    if not isinstance(raw_ids, list):
        raise ValueError("Pinned revision deploy returned malformed build identifiers")
    try:
        return [UUID(str(raw_id)) for raw_id in raw_ids]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Pinned revision deploy returned malformed build identifiers"
        ) from exc


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
    deploy = (
        await db.get(SolutionDeployJob, turn.deploy_job_id)
        if turn is not None and turn.deploy_job_id is not None
        else None
    )
    try:
        build_ids = _promotion_build_ids(turn, deploy)
    except ValueError as exc:
        build_ids = []
        blockers.append(str(exc))
    builds = (
        (
            await db.execute(
                select(SolutionBuildJob).where(SolutionBuildJob.id.in_(build_ids))
            )
        )
        .scalars()
        .all()
        if build_ids
        else []
    )
    builds_by_id = {build.id: build for build in builds}
    missing_build_ids = [
        build_id for build_id in build_ids if build_id not in builds_by_id
    ]
    failed_builds = [
        builds_by_id[build_id]
        for build_id in build_ids
        if build_id in builds_by_id and builds_by_id[build_id].status != "succeeded"
    ]
    build_status = (
        "missing"
        if missing_build_ids
        else failed_builds[0].status
        if failed_builds
        else "succeeded"
        if build_ids
        else None
    )
    if turn is None or turn.status != "succeeded":
        blockers.append("Pinned revision does not have a successful builder turn")
    if deploy is None or deploy.status != "succeeded":
        blockers.append("Pinned revision deploy is not green")
    if missing_build_ids:
        blockers.append("Pinned revision app build record is missing")
    if failed_builds:
        blockers.append("Pinned revision app build is not green")

    unresolved_roles = (
        sorted(
            str(name) for name in ((deploy.result or {}).get("roles_unresolved") or [])
        )
        if deploy is not None
        else []
    )
    connection_names = sorted(
        (
            await db.execute(
                select(SolutionConnectionSchema.integration_name).where(
                    SolutionConnectionSchema.solution_id == solution_id
                )
            )
        )
        .scalars()
        .all()
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

    config_keys: list[str] = []
    if solution.organization_id is not None:
        config_keys = list(
            (
                await db.execute(
                    select(SolutionConfigSchema.key)
                    .where(SolutionConfigSchema.solution_id == solution_id)
                    .where(
                        select(Config.id)
                        .where(
                            Config.organization_id == solution.organization_id,
                            Config.key == SolutionConfigSchema.key,
                        )
                        .exists()
                    )
                    .where(
                        ~select(Config.id)
                        .where(
                            Config.organization_id.is_(None),
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
        build_job_id=build_ids[0] if build_ids else None,
        build_job_ids=build_ids,
        deploy_job_id=turn.deploy_job_id if turn else None,
        build_status=build_status,
        deploy_status=deploy.status if deploy else None,
        entity_counts=source_counts,
        unresolved_roles=unresolved_roles,
        connection_names=connection_names,
        config_keys_requiring_reentry_for_global=sorted(config_keys),
        global_repo_access=solution.global_repo_access,
        ready=not blockers,
        blockers=blockers,
    )


async def list_promotion_reviews(
    db: AsyncSession,
    *,
    source_organization_ids: Collection[UUID | None] | None = None,
) -> list[PromotionReviewDTO]:
    """List requested reviews inside the caller's admitted source boundary.

    ``None`` deliberately means unrestricted because the router uses it only
    for the Platform Admin wildcard.  An empty collection means no visible
    source organizations.  Keeping this filter in the query prevents an
    unauthorized private Solution from being materialized and filtered later.
    """

    query = (
        select(SolutionBuilderProject.solution_id)
        .join(Solution, Solution.id == SolutionBuilderProject.solution_id)
        .where(
            Solution.visibility == "private",
            SolutionBuilderProject.promotion_status == "requested",
        )
        .order_by(SolutionBuilderProject.promotion_requested_at.asc())
    )
    if source_organization_ids is not None:
        organization_ids = {
            organization_id
            for organization_id in source_organization_ids
            if organization_id is not None
        }
        include_platform = None in source_organization_ids
        predicates = []
        if organization_ids:
            predicates.append(Solution.organization_id.in_(organization_ids))
        if include_platform:
            predicates.append(Solution.organization_id.is_(None))
        if not predicates:
            return []
        query = query.where(or_(*predicates))
    ids = (await db.execute(query)).scalars().all()
    return [await promotion_review(db, solution_id) for solution_id in ids]


async def _assert_target_collisions(
    db: AsyncSession,
    solution: Solution,
    target_org: UUID | None,
    *,
    allowed_solution_ids: set[UUID] | None = None,
) -> None:
    allowed_ids = allowed_solution_ids or {solution.id}
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
                Solution.id.not_in(allowed_ids),
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
        (
            await db.execute(
                select(Application.slug).where(Application.solution_id == solution.id)
            )
        )
        .scalars()
        .all()
    )
    for slug in app_slugs:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('bifrost:appslug:' || :s))"),
            {"s": slug},
        )
        predicates = [
            Application.slug == slug,
            Application.solution_id.not_in(allowed_ids),
        ]
        if target_org is not None:
            predicates.append(
                or_(
                    Application.organization_id == target_org,
                    Application.organization_id.is_(None),
                )
            )
        collision = (
            (await db.execute(select(Application.id).where(*predicates)))
            .scalars()
            .first()
        )
        if collision is not None:
            raise PromotionBlocked([f"App slug '{slug}' conflicts with a visible app"])


async def _release_for_target(
    db: AsyncSession,
    *,
    source_solution_id: UUID,
    target_organization_id: UUID | None,
) -> SolutionBuilderRelease | None:
    target_predicate = (
        SolutionBuilderRelease.target_organization_id.is_(None)
        if target_organization_id is None
        else SolutionBuilderRelease.target_organization_id == target_organization_id
    )
    return (
        (
            await db.execute(
                select(SolutionBuilderRelease)
                .where(
                    SolutionBuilderRelease.source_solution_id == source_solution_id,
                    target_predicate,
                )
                .with_for_update()
            )
        )
        .scalars()
        .one_or_none()
    )


async def _prepare_release_target(
    db: AsyncSession,
    *,
    source: Solution,
    target_organization_id: UUID | None,
    global_repo_access: bool,
    runtime_mode: str,
    admin_user_id: UUID,
) -> tuple[SolutionBuilderRelease, Solution]:
    release = await _release_for_target(
        db,
        source_solution_id=source.id,
        target_organization_id=target_organization_id,
    )
    published = (
        await db.get(Solution, release.published_solution_id)
        if release is not None
        else None
    )
    if release is not None and published is None:
        raise PromotionBlocked(["Published release target is missing"])

    allowed_ids = {source.id}
    if published is not None:
        allowed_ids.add(published.id)
    await _assert_target_collisions(
        db,
        source,
        target_organization_id,
        allowed_solution_ids=allowed_ids,
    )

    if published is None:
        published = Solution(
            slug=source.slug,
            name=source.name,
            organization_id=target_organization_id,
            global_repo_access=global_repo_access,
            owner_user_id=None,
            visibility="shared",
            status="active",
        )
        db.add(published)
        await db.flush()
        release = SolutionBuilderRelease(
            source_solution_id=source.id,
            published_solution_id=published.id,
            target_organization_id=target_organization_id,
            runtime_mode=runtime_mode,
            approved_by=admin_user_id,
        )
        db.add(release)
        await db.flush()
    else:
        if (
            published.visibility != "shared"
            or published.organization_id != target_organization_id
        ):
            raise PromotionBlocked(["Published release target has an invalid scope"])
        published.slug = source.slug
        published.name = source.name
        published.status = "active"
        published.global_repo_access = global_repo_access

    assert release is not None
    return release, published


async def _assign_role_users(
    db: AsyncSession,
    assignments: dict[str, list[UUID]],
    *,
    assigned_by: UUID,
    target_organization_id: UUID | None,
) -> None:
    from src.models.orm.role_assignments import RoleAssignmentBoundary

    for role_name, user_ids in assignments.items():
        role_id = (
            await db.execute(select(Role.id).where(Role.name == role_name))
        ).scalar_one_or_none()
        if role_id is None:
            raise PromotionBlocked([f"Reviewed role '{role_name}' does not exist"])
        existing = set(
            (
                await db.execute(
                    select(RoleAssignment.user_id).where(
                        RoleAssignment.role_id == role_id,
                        RoleAssignment.user_id.in_(user_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        db.add_all(
            RoleAssignment(
                role_id=role_id,
                user_id=user_id,
                assigned_by_user_id=assigned_by,
                boundaries=[
                    RoleAssignmentBoundary(
                        boundary_kind=(
                            "organization"
                            if target_organization_id is not None
                            else "platform"
                        ),
                        organization_id=target_organization_id,
                    )
                ],
            )
            for user_id in user_ids
            if user_id not in existing
        )


async def _validate_role_user_assignments(
    db: AsyncSession,
    assignments: dict[str, list[UUID]],
    *,
    allowed_roles: Iterable[str],
    target_organization_id: UUID | None,
) -> None:
    """Keep promotion-time membership changes inside the reviewed target scope."""

    unknown_roles = sorted(set(assignments) - set(allowed_roles))
    if unknown_roles:
        raise PromotionBlocked(
            [
                "Role assignments were supplied for roles outside this review: "
                + ", ".join(unknown_roles)
            ]
        )

    requested_user_ids = {
        user_id for user_ids in assignments.values() for user_id in user_ids
    }
    if not requested_user_ids:
        return
    users = (
        (
            await db.execute(
                select(User.id, User.organization_id, User.is_active).where(
                    User.id.in_(requested_user_ids)
                )
            )
        )
        .tuples()
        .all()
    )
    valid_user_ids = {
        user_id
        for user_id, organization_id, is_active in users
        if is_active and organization_id == target_organization_id
    }
    if valid_user_ids != requested_user_ids:
        target_label = (
            "the selected customer organization"
            if target_organization_id is not None
            else "the global platform scope"
        )
        raise PromotionBlocked(
            [f"Role assignments must contain active users from {target_label}"]
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

    if request.target == "global" and request.target_organization_id is not None:
        raise PromotionBlocked(["Global promotion cannot target an organization"])
    target_org = (
        request.target_organization_id or review.organization_id
        if request.target == "company"
        else None
    )
    if request.target == "company" and target_org is None:
        raise PromotionBlocked(["Company promotion requires an organization"])
    if target_org is not None and await db.get(Organization, target_org) is None:
        raise PromotionBlocked(["Target organization does not exist"])
    await _validate_role_user_assignments(
        db,
        request.role_user_assignments,
        allowed_roles=review.unresolved_roles,
        target_organization_id=target_org,
    )

    try:
        async with solution_write_lock(solution_id):
            source, project = await _load_requested(db, solution_id)
            await db.refresh(project, with_for_update=True)
            if (
                project.promotion_revision_id != review.pinned_revision_id
                or project.current_revision_id != review.pinned_revision_id
                or project.deployed_revision_id != review.pinned_revision_id
            ):
                raise PromotionBlocked(
                    ["Promotion request changed during administrator review"]
                )
            release, published = await _prepare_release_target(
                db,
                source=source,
                target_organization_id=target_org,
                global_repo_access=request.allow_global_repo_access,
                runtime_mode=request.runtime_mode,
                admin_user_id=admin_user_id,
            )

            async with solution_write_lock(published.id):
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

                    result = await deploy_zip_to_solution_path(
                        db,
                        published,
                        source_path,
                        force=True,
                        promotion=True,
                        isolated_app_builds=True,
                        source_revision_id=review.pinned_revision_id,
                        requested_by=admin_user_id,
                    )
                    await rehome_solution_owned_rows(
                        db,
                        solution_id=published.id,
                        organization_id=target_org,
                    )
                    await _assign_role_users(
                        db,
                        request.role_user_assignments,
                        assigned_by=admin_user_id,
                        target_organization_id=target_org,
                    )
                    await db.execute(
                        update(Application)
                        .where(Application.solution_id == published.id)
                        .values(runtime_mode=request.runtime_mode)
                    )
                    # Runtime activation remains suppressed by promotion replay.
                    # The private source and all Builder history stay untouched.
                    published.visibility = "shared"
                    release.published_revision_id = review.pinned_revision_id
                    release.runtime_mode = request.runtime_mode
                    release.approved_by = admin_user_id
                    release.published_at = datetime.now(timezone.utc)
                    project.promotion_status = "none"
                    await emit_audit(
                        db,
                        "solution.release.publish",
                        resource_type="solution",
                        resource_id=published.id,
                        details={
                            "source_solution_id": str(solution_id),
                            "release_id": str(release.id),
                            "target": request.target,
                            "target_organization_id": str(target_org)
                            if target_org
                            else None,
                            "revision_id": str(review.pinned_revision_id),
                            "source_sha256": review.source_sha256,
                            "roles_created": list(result.roles_created),
                            "approved_connections": request.approved_connection_names,
                            "global_repo_access": request.allow_global_repo_access,
                            "runtime_mode": request.runtime_mode,
                        },
                    )
                    await emit_audit(
                        db,
                        "solution.publish",
                        resource_type="solution",
                        resource_id=solution_id,
                        details={
                            "release_id": str(release.id),
                            "published_solution_id": str(published.id),
                            "revision_id": str(review.pinned_revision_id),
                            "target": request.target,
                        },
                    )
                    # The pinned bundle is already the deployed preview, so a
                    # pre-commit finalize only publishes reviewed deterministic
                    # artifacts to the separate release install.
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
        release_id=release.id,
        published_solution_id=published.id,
        solution_id=solution_id,
        target=request.target,
        visibility="shared",
        organization_id=target_org,
        promoted_revision_id=review.pinned_revision_id,
        roles_created=list(result.roles_created),
    )
