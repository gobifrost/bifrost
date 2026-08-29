"""V2 Apps have deployment state without a publish/draft concept.

The compatibility ``is_published`` field means launchable for V2 Apps: an
independent App needs an active artifact, while a Solution App keeps its
existing unversioned deploy artifact behavior.
"""
from __future__ import annotations

from src.models.orm.applications import Application


def _app(app_model: str, snapshot=None) -> Application:
    a = Application(name="x", slug="x", repo_path="apps/x", app_model=app_model)
    a.published_snapshot = snapshot
    return a


def test_independent_v2_is_not_launchable_before_deploy():
    assert _app("standalone_v2", snapshot=None).is_published is False


def test_independent_v2_is_launchable_with_active_deployment():
    app = _app("standalone_v2")
    app.active_deployment_id = "11111111-1111-1111-1111-111111111111"
    assert app.is_published is True


def test_v2_has_no_unpublished_changes():
    assert _app("standalone_v2", snapshot=None).has_unpublished_changes is False


def test_v1_unpublished_without_snapshot():
    # Legacy v1 keeps the snapshot-based gate.
    assert _app("inline_v1", snapshot=None).is_published is False


def test_v1_published_with_snapshot():
    assert _app("inline_v1", snapshot={"deployed_by": "x"}).is_published is True
