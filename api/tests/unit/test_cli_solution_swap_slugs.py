"""`bifrost solution swap-slugs` — thin CLI wrapper over POST /applications/swap-slugs.

Resolves slug or id args, then POSTs the swap. Mocks BifrostClient so no
network/DB is touched.
"""
from __future__ import annotations

from unittest import mock

from click.testing import CliRunner

from bifrost.commands.solution import solution_group

A_ID = "11111111-1111-1111-1111-111111111111"
B_ID = "22222222-2222-2222-2222-222222222222"


def _resp(payload, status=200):
    r = mock.MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = str(payload)
    return r


def _client(captured):
    async def get(path):  # type: ignore[no-untyped-def]
        captured.setdefault("gets", []).append(path)
        # /api/applications/<slug> → resolve slug to id
        slug = path.rsplit("/", 1)[-1]
        return _resp({"id": A_ID if slug == "orders" else B_ID, "slug": slug})

    async def post(path, json=None):  # type: ignore[no-untyped-def]
        captured["post"] = (path, json)
        return _resp({
            "applications": [
                {"name": "Orders v2", "slug": "orders"},
                {"name": "Orders v1", "slug": "orders-legacy"},
            ],
            "total": 2,
        })

    c = mock.AsyncMock()
    c.get = get
    c.post = post
    return c


def _invoke(args, captured):
    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=_client(captured)):
        return CliRunner().invoke(solution_group, ["swap-slugs", *args])


def test_swap_by_ids() -> None:
    captured: dict = {}
    result = _invoke([A_ID, B_ID], captured)
    assert result.exit_code == 0, result.output
    path, body = captured["post"]
    assert path == "/api/applications/swap-slugs"
    assert body == {"app_a": A_ID, "app_b": B_ID}
    # ids pass straight through — no slug-resolution GETs.
    assert not captured.get("gets")
    assert "/apps/orders" in result.output


def test_swap_by_slugs_resolves_first() -> None:
    captured: dict = {}
    result = _invoke(["orders", "orders-v2"], captured)
    assert result.exit_code == 0, result.output
    assert captured["gets"] == [
        "/api/applications/orders",
        "/api/applications/orders-v2",
    ]
    _, body = captured["post"]
    assert body == {"app_a": A_ID, "app_b": B_ID}
