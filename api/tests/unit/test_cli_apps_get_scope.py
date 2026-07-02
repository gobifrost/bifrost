"""`bifrost apps get <ref>` inside a bound solution workspace prefers the
install's own apps — a generic ref ("portal") must not silently resolve an
unrelated global app when the workspace's own app matches by slug or name
(RTM sharp edge, 2026-07-02)."""
from bifrost.commands.apps import _select_bound_app

SOL = "37937a44-1b0a-448d-a0cc-0896ca6a858c"

ITEMS = [
    {"id": "aaaa", "slug": "portal", "name": "Covi Portal", "solution_id": None},
    {"id": "bbbb", "slug": "rtm-dms", "name": "Portal", "solution_id": SOL},
    {"id": "cccc", "slug": "other", "name": "Other", "solution_id": "1111"},
]


def test_own_install_name_match_wins_over_foreign_slug():
    match, foreign = _select_bound_app(ITEMS, "portal", SOL)
    assert match is not None and match["id"] == "bbbb"
    # The unrelated global slug match is surfaced for a warning.
    assert [f["id"] for f in foreign] == ["aaaa"]


def test_own_install_slug_match():
    match, foreign = _select_bound_app(ITEMS, "rtm-dms", SOL)
    assert match is not None and match["id"] == "bbbb"
    assert foreign == []


def test_no_own_match_returns_none():
    match, foreign = _select_bound_app(ITEMS, "other", SOL)
    assert match is None
    assert foreign == []


def test_name_match_is_case_insensitive():
    match, _ = _select_bound_app(ITEMS, "PORTAL", SOL)
    assert match is not None and match["id"] == "bbbb"
