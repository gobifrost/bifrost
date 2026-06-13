"""standalone_v2 apps have NO publish/draft concept — created == published.

The v1 draft→publish editor flow doesn't apply to v2 (source-built + served), so
`is_published` is unconditionally True for v2 and `has_unpublished_changes` is
False. This is what stops the v1 "Not Published"/"Open Editor" screen from
leaking onto a v2 app reached by slug. Pure ORM-property logic, no DB needed.
"""
from __future__ import annotations

from src.models.orm.applications import Application


def _app(app_model: str, snapshot=None) -> Application:
    a = Application(name="x", slug="x", repo_path="apps/x", app_model=app_model)
    a.published_snapshot = snapshot
    return a


def test_v2_is_always_published_even_without_snapshot():
    # No published_snapshot, but v2 → still "published" (created == published).
    assert _app("standalone_v2", snapshot=None).is_published is True


def test_v2_has_no_unpublished_changes():
    assert _app("standalone_v2", snapshot=None).has_unpublished_changes is False


def test_v1_unpublished_without_snapshot():
    # Legacy v1 keeps the snapshot-based gate.
    assert _app("inline_v1", snapshot=None).is_published is False


def test_v1_published_with_snapshot():
    assert _app("inline_v1", snapshot={"deployed_by": "x"}).is_published is True
