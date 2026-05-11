"""Unit tests for ApplicationRepository.replace_application (repoint)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.models.orm.applications import Application
from src.routers.applications import ApplicationRepository


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _FakeSession:
    def __init__(
        self,
        *,
        apps: list[Application],
        source_paths: set[str] | None = None,
    ):
        self.apps = apps
        self.source_paths = source_paths or set()
        self.execute_calls = 0
        self.flushed = False
        self.refreshed: Application | None = None

    async def execute(self, _stmt):
        self.execute_calls += 1
        target = getattr(self, "target_app", None)
        target_path = getattr(self, "target_path", None)

        if self.execute_calls == 1:
            conflict = next(
                (
                    app
                    for app in self.apps
                    if app.repo_path == target_path and app.id != target.id
                ),
                None,
            )
            return _ScalarResult(conflict)

        if self.execute_calls == 2:
            return _ScalarsResult([app for app in self.apps if app.id != target.id])

        if self.execute_calls == 3:
            prefix = f"{target_path}/"
            has_source = any(path.startswith(prefix) for path in self.source_paths)
            return _ScalarResult(object() if has_source else None)

        raise AssertionError(f"unexpected execute call {self.execute_calls}")

    async def flush(self):
        self.flushed = True

    async def refresh(self, app: Application):
        self.refreshed = app


class _TestRepository(ApplicationRepository):
    def __init__(self, session: _FakeSession, target: Application):
        super().__init__(session=session, org_id=None, is_superuser=True)
        self._target = target
        session.target_app = target

    async def get(self, id):
        return self._target if id == self._target.id else None

    async def replace_application(self, app_id, new_repo_path, *, force=False):
        self.session.target_path = new_repo_path.rstrip("/")
        return await super().replace_application(
            app_id,
            new_repo_path,
            force=force,
        )


def _make_app(*, slug: str, repo_path: str) -> Application:
    return Application(
        id=uuid4(),
        name=slug,
        slug=slug,
        repo_path=repo_path,
        access_level="authenticated",
    )


def _repo(
    app: Application,
    *,
    others: list[Application] | None = None,
    source_paths: set[str] | None = None,
) -> _TestRepository:
    return _TestRepository(
        _FakeSession(apps=[app, *(others or [])], source_paths=source_paths),
        app,
    )


@pytest.mark.asyncio
async def test_replace_repoints_when_all_checks_pass():
    app = _make_app(slug="foo", repo_path="apps/foo")
    repo = _repo(app, source_paths={"apps/foo-v2/index.tsx"})

    result = await repo.replace_application(app.id, "apps/foo-v2", force=False)

    assert result is not None
    assert result.repo_path == "apps/foo-v2"
    assert repo.session.flushed is True
    assert repo.session.refreshed is app


@pytest.mark.asyncio
async def test_replace_noop_when_path_unchanged():
    app = _make_app(slug="foo", repo_path="apps/foo")
    repo = _repo(app)

    result = await repo.replace_application(app.id, "apps/foo", force=False)

    assert result is not None
    assert result.repo_path == "apps/foo"
    assert repo.session.execute_calls == 0
    assert repo.session.flushed is False


@pytest.mark.asyncio
async def test_replace_rejects_duplicate_repo_path():
    app_a = _make_app(slug="a", repo_path="apps/a")
    app_b = _make_app(slug="b", repo_path="apps/taken")
    repo = _repo(app_a, others=[app_b], source_paths={"apps/taken/index.tsx"})

    with pytest.raises(ValueError, match="already claimed"):
        await repo.replace_application(app_a.id, "apps/taken", force=False)


@pytest.mark.asyncio
async def test_replace_rejects_nested_under_existing():
    app_a = _make_app(slug="a", repo_path="apps/a")
    app_outer = _make_app(slug="outer", repo_path="apps/outer")
    repo = _repo(
        app_a,
        others=[app_outer],
        source_paths={"apps/outer/sub/index.tsx"},
    )

    with pytest.raises(ValueError, match="nested"):
        await repo.replace_application(app_a.id, "apps/outer/sub", force=False)


@pytest.mark.asyncio
async def test_replace_rejects_existing_nested_under_target():
    app_a = _make_app(slug="a", repo_path="apps/a")
    app_inner = _make_app(slug="inner", repo_path="apps/outer/inner")
    repo = _repo(app_a, others=[app_inner], source_paths={"apps/outer/index.tsx"})

    with pytest.raises(ValueError, match="nested"):
        await repo.replace_application(app_a.id, "apps/outer", force=False)


@pytest.mark.asyncio
async def test_replace_rejects_empty_prefix():
    app = _make_app(slug="foo", repo_path="apps/foo")
    repo = _repo(app)

    with pytest.raises(ValueError, match="no files"):
        await repo.replace_application(app.id, "apps/does-not-exist", force=False)


@pytest.mark.asyncio
async def test_force_does_not_bypass_uniqueness():
    app_a = _make_app(slug="a", repo_path="apps/a")
    app_b = _make_app(slug="b", repo_path="apps/taken")
    repo = _repo(app_a, others=[app_b], source_paths={"apps/taken/index.tsx"})

    with pytest.raises(ValueError, match="already claimed"):
        await repo.replace_application(app_a.id, "apps/taken", force=True)


@pytest.mark.asyncio
async def test_force_does_not_bypass_nesting():
    app_a = _make_app(slug="a", repo_path="apps/a")
    app_outer = _make_app(slug="outer", repo_path="apps/outer")
    repo = _repo(app_a, others=[app_outer], source_paths={"apps/outer/index.tsx"})

    with pytest.raises(ValueError, match="nested"):
        await repo.replace_application(app_a.id, "apps/outer/sub", force=True)


@pytest.mark.asyncio
async def test_force_bypasses_source_exists():
    app = _make_app(slug="foo", repo_path="apps/foo")
    repo = _repo(app)

    result = await repo.replace_application(app.id, "apps/empty", force=True)

    assert result is not None
    assert result.repo_path == "apps/empty"
