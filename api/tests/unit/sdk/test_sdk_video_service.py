"""Focused tests for the shared SDK video generation service.

Covers ``shared.sdk_video`` used by ``POST /api/sdk/artifacts/video``
and the fixed local status entry point:

- enqueue metadata (canonical definition, requester/org/resource fields)
- notification failure still commits/refreshes/publishes in order
- commit/refresh/update ordering on the happy path
- status visibility (owner reads, stranger gets 404, admin reads)
- serialization matches the shared ``PlatformJobPublic`` contract
- local status stays fixed to SDK video jobs
- the service takes an explicit trusted principal, never a Request
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import shared.sdk_video as sdk_video
from shared.sdk_video import (
    SdkVideoJobError,
    can_read_platform_job,
    enqueue_sdk_video_job,
    finalize_sdk_video_job,
    get_platform_job_status,
    get_sdk_video_job_status,
    sdk_video_job_accepted,
)
from src.core.principal import UserPrincipal
from src.jobs.platform.video_generation import SDK_VIDEO_GENERATION_DEFINITION
from src.models.contracts.artifacts import VideoArtifactSpec


def _user(**kwargs) -> UserPrincipal:
    return UserPrincipal(
        user_id=kwargs.get("user_id", uuid4()),
        email=kwargs.get("email", "sdk@example.com"),
        organization_id=kwargs.get("organization_id", None),
        name=kwargs.get("name", "SDK"),
        is_active=True,
        is_superuser=kwargs.get("is_superuser", False),
        is_verified=True,
    )


def _spec() -> VideoArtifactSpec:
    return VideoArtifactSpec(filename="launch.mp4", prompt="A launch video")


@pytest.mark.asyncio
async def test_enqueue_persists_canonical_metadata(db_session) -> None:
    user = _user()
    workspace_id = uuid4()
    execution_id = uuid4()
    job, reused = await enqueue_sdk_video_job(
        db_session,
        user,
        spec=_spec(),
        workspace_id=workspace_id,
        execution_id=execution_id,
    )
    assert reused is False
    assert job.job_type == SDK_VIDEO_GENERATION_DEFINITION.job_type
    assert job.organization_id == user.organization_id
    assert job.requested_by_user_id == str(user.user_id)
    assert job.requested_by_email == user.email
    assert job.requested_by_name == user.name
    assert job.resource_type == "artifact"
    assert job.resource_id == "launch.mp4"
    assert job.title == "Generating launch.mp4"
    assert job.action_url is None
    assert job.dedupe_key is None
    assert job.status == "queued"


@pytest.mark.asyncio
async def test_enqueue_uses_name_fallback_for_requester_name(db_session) -> None:
    user = _user(name="")
    job, _ = await enqueue_sdk_video_job(db_session, user, spec=_spec())
    assert job.requested_by_name == user.email


@pytest.mark.asyncio
async def test_finalize_orders_notification_commit_refresh_publish() -> None:
    events: list[str] = []
    db = MagicMock()
    db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    db.refresh = AsyncMock(side_effect=lambda *a: events.append("refresh"))
    job = MagicMock()
    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        import src.services.platform_jobs as pj

        orig_ensure = pj.ensure_platform_job_notification
        orig_publish = pj.publish_platform_job_update

        async def fake_ensure(session, job_arg) -> None:
            assert session is db
            assert job_arg is job
            events.append("notify")

        async def fake_publish(job_arg) -> None:
            assert job_arg is job
            events.append("publish")

        mp.setattr(pj, "ensure_platform_job_notification", fake_ensure)
        mp.setattr(pj, "publish_platform_job_update", fake_publish)
        await finalize_sdk_video_job(db, job)
    assert events == ["notify", "commit", "refresh", "publish"]
    assert orig_ensure is not None and orig_publish is not None


@pytest.mark.asyncio
async def test_finalize_notification_failure_still_commits_and_publishes() -> None:
    events: list[str] = []
    db = MagicMock()
    db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
    db.refresh = AsyncMock(side_effect=lambda *a: events.append("refresh"))
    job = MagicMock()
    job.id = uuid4()
    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        import src.services.platform_jobs as pj

        async def boom(*args, **kwargs) -> None:
            events.append("notify")
            raise RuntimeError("notify down")

        async def fake_publish(job_arg) -> None:
            events.append("publish")

        mp.setattr(pj, "ensure_platform_job_notification", boom)
        mp.setattr(pj, "publish_platform_job_update", fake_publish)
        await finalize_sdk_video_job(db, job)
    assert events == ["notify", "commit", "refresh", "publish"]


def test_accepted_payload_mirrors_committed_job() -> None:
    job = MagicMock()
    job.id = uuid4()
    job.notification_id = uuid4()
    job.status = "queued"
    accepted = sdk_video_job_accepted(job, False)
    assert accepted.job_id == job.id
    assert accepted.notification_id == job.notification_id
    assert accepted.status.value == "queued"
    assert accepted.reused is False


@pytest.mark.asyncio
async def test_status_visibility_owner_admin_and_stranger(db_session) -> None:
    owner = _user()
    stranger = _user()
    admin = _user(is_superuser=True)
    job, _ = await enqueue_sdk_video_job(db_session, owner, spec=_spec())
    await db_session.commit()

    seen = await get_platform_job_status(db_session, owner, job.id)
    assert seen.id == job.id
    assert seen.job_type == SDK_VIDEO_GENERATION_DEFINITION.job_type

    seen_admin = await get_platform_job_status(db_session, admin, job.id)
    assert seen_admin.id == job.id

    with pytest.raises(SdkVideoJobError) as exc:
        await get_platform_job_status(db_session, stranger, job.id)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_status_missing_job_is_404(db_session) -> None:
    with pytest.raises(SdkVideoJobError) as exc:
        await get_platform_job_status(db_session, _user(), uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_status_serialization_matches_shared_contract(db_session) -> None:
    from src.services.platform_jobs import platform_job_to_public

    owner = _user()
    job, _ = await enqueue_sdk_video_job(db_session, owner, spec=_spec())
    await db_session.commit()
    await db_session.refresh(job)
    assert (await get_platform_job_status(db_session, owner, job.id)).model_dump(
        mode="json"
    ) == platform_job_to_public(job).model_dump(mode="json")


@pytest.mark.asyncio
async def test_local_status_rejects_non_video_jobs(db_session) -> None:
    from src.models.orm.platform_jobs import PlatformJob

    owner = _user()
    other = PlatformJob(
        job_type="solution.deploy",
        payload={},
        requested_by_user_id=str(owner.user_id),
        requested_by_email=owner.email,
        requested_by_name=owner.name,
        title="Deploy",
        status="queued",
        phase="Queued",
    )
    db_session.add(other)
    await db_session.commit()

    with pytest.raises(SdkVideoJobError) as exc:
        await get_sdk_video_job_status(db_session, owner, other.id)
    assert exc.value.status_code == 404

    video, _ = await enqueue_sdk_video_job(db_session, owner, spec=_spec())
    await db_session.commit()
    seen = await get_sdk_video_job_status(db_session, owner, video.id)
    assert seen.id == video.id


def test_can_read_rule_matches_historical_router() -> None:
    owner_id = uuid4()
    job = MagicMock()
    job.requested_by_user_id = str(owner_id)
    owner = _user(user_id=owner_id)
    assert can_read_platform_job(job, owner) is True
    assert can_read_platform_job(job, _user()) is False
    assert can_read_platform_job(job, _user(is_superuser=True)) is True


def test_service_takes_explicit_principal_never_a_request() -> None:
    assert "fastapi" not in sdk_video.__name__
    for name in ("enqueue_sdk_video_job", "finalize_sdk_video_job"):
        params = set(inspect.signature(getattr(sdk_video, name)).parameters)
        assert "request" not in params, name
    for name in (
        "get_visible_platform_job",
        "get_platform_job_status",
        "get_sdk_video_job_status",
    ):
        params = set(inspect.signature(getattr(sdk_video, name)).parameters)
        assert "user" in params, name
    source = inspect.getsource(sdk_video)
    assert "from fastapi" not in source
    assert "import fastapi" not in source
    assert "HTTPException(" not in source
