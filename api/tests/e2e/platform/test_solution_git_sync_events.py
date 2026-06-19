"""read_workspace_bundle must carry events, or a git-connected sync deletes every
EventSource/schedule/webhook for the install (audit CRITICAL, live-confirmed 1->0)."""
import pathlib
import types
import uuid

import pytest

from src.services.solutions.git_sync import read_workspace_bundle


pytestmark = pytest.mark.e2e


def _make_fake_solution(slug: str) -> object:
    """Return a minimal Solution-like object with only the .slug attribute needed."""
    sol = types.SimpleNamespace(
        id=uuid.uuid4(),
        slug=slug,
        name="Events Test Solution",
        organization_id=None,
    )
    return sol


def test_read_workspace_bundle_carries_events(tmp_path):
    slug = f"events-test-{uuid.uuid4().hex[:8]}"
    sol = _make_fake_solution(slug)

    bifrost = tmp_path / ".bifrost"
    bifrost.mkdir()
    (tmp_path / "bifrost.solution.yaml").write_text(
        f"slug: {slug}\nname: T\nversion: 0.1.0\n"
    )
    (bifrost / "events.yaml").write_text(
        "events:\n"
        "  11111111-1111-1111-1111-111111111111:\n"
        "    id: 11111111-1111-1111-1111-111111111111\n"
        "    name: nightly\n"
        "    source_type: schedule\n"
        "    is_active: true\n"
        "    schedule: {cron: '0 0 * * *', timezone: UTC}\n"
        "    subscriptions: []\n"
    )
    bundle = read_workspace_bundle(sol, tmp_path)
    assert len(bundle.events) == 1, "events.yaml must populate bundle.events"
    assert bundle.events[0]["name"] == "nightly"
