"""End-to-end: POST /api/files/search over the live stack.

Proves the REST contract a CLI, MCP client, or the editor relies on: bounded
pages whose cursor reaches every match exactly once, any-extension text
indexing from the ordinary write path, and Solution source (written by a real
deploy) searchable beside workspace source and labelled read-only.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _write(e2e_client, headers, path: str, content: str) -> None:
    resp = e2e_client.post("/api/files/write", headers=headers, json={
        "path": path, "content": content, "mode": "cloud", "location": "workspace",
    })
    assert resp.status_code == 204, f"write {path}: {resp.status_code} {resp.text}"


def _search(e2e_client, headers, **body) -> dict:
    resp = e2e_client.post("/api/files/search", headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_pages_cover_every_match_exactly_once(e2e_client, platform_admin):
    token = f"E2ESEARCH{uuid.uuid4().hex[:10]}"
    for i in range(30):
        _write(e2e_client, platform_admin.headers, f"search_e2e/{token}/f{i:02}.txt", f"{token}\n")
    seen, cursor, pages = [], None, 0
    while True:
        body = {"query": token, "include_pattern": f"search_e2e/{token}/**"}
        if cursor:
            body["cursor"] = cursor
        page = _search(e2e_client, platform_admin.headers, **body)
        pages += 1
        seen += [m["file_path"] for m in page["matches"]]
        if page["response_complete"]:
            assert page["next_cursor"] is None and "no more results" in page["guidance"]
            break
        assert page["returned"] == 25 and f'cursor="{page["next_cursor"]}"' in page["guidance"]
        cursor = page["next_cursor"]
    assert pages == 2
    assert seen == sorted(seen) and len(seen) == len(set(seen)) == 30


def test_json_and_extensionless_files_are_searchable(e2e_client, platform_admin):
    token = f"E2EANYEXT{uuid.uuid4().hex[:10]}"
    _write(e2e_client, platform_admin.headers, f"search_e2e/{token}/c.json", f'{{"k": "{token}"}}')
    _write(e2e_client, platform_admin.headers, f"search_e2e/{token}/Dockerfile", f"# {token}")
    page = _search(e2e_client, platform_admin.headers, query=token, output_mode="files")
    assert sorted(f["file_path"].rsplit("/", 1)[-1] for f in page["files"]) == ["Dockerfile", "c.json"]


def test_cursor_from_other_query_is_rejected(e2e_client, platform_admin):
    token = f"E2ECURSOR{uuid.uuid4().hex[:10]}"
    for i in range(2):
        _write(e2e_client, platform_admin.headers, f"search_e2e/{token}/{i}.txt", token)
    first = _search(e2e_client, platform_admin.headers, query=token, limit=1)
    resp = e2e_client.post("/api/files/search", headers=platform_admin.headers, json={
        "query": f"{token}-other", "cursor": first["next_cursor"],
    })
    assert resp.status_code == 400 and "does not belong" in resp.json()["detail"]


def test_deployed_solution_source_is_searchable_and_read_only(e2e_client, platform_admin):
    from tests.e2e.platform.conftest import deploy_solution

    token = f"E2ESOL{uuid.uuid4().hex[:10]}"
    slug = f"search-{token.lower()}"
    resp = e2e_client.post("/api/solutions", headers=platform_admin.headers, json={
        "slug": slug, "name": slug, "scope": "global", "global_repo_access": False,
    })
    assert resp.status_code in (200, 201), resp.text
    solution_id = resp.json()["id"]
    path = f"functions/{token.lower()}.py"
    deployed = deploy_solution(
        e2e_client, solution_id, platform_admin.headers,
        {"python_files": {path: f"MARK = '{token}'\n"}, "workflows": []},
    )
    assert deployed.status_code in (200, 201), deployed.text
    _write(e2e_client, platform_admin.headers, path, f"WS_MARK = '{token}'\n")

    both = _search(e2e_client, platform_admin.headers, query=token)
    assert [(m["source"]["kind"], m["source"]["editable"], m["source"]["solution_slug"]) for m in both["matches"]] == [
        ("workspace", True, None),
        ("solution", False, slug),
    ]
    only = _search(e2e_client, platform_admin.headers, query=token, solution_id=solution_id)
    assert [m["source"]["kind"] for m in only["matches"]] == ["solution"]
