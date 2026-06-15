"""E2E: PUT /api/solutions/{id}/readme is refused (409) for a git-connected
install — the repo owns the README and auto-pull would clobber a hand edit.
A disconnected install can still set its README directly.
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.e2e


def _create_solution(e2e_client, headers, slug: str, *, git: bool) -> str:
    body: dict = {"slug": slug, "name": slug.upper(), "scope": "global"}
    if git:
        # A git-connected install carries a repo URL; create disconnected then
        # PATCH the git fields on (mirrors how the UI connects an install).
        pass
    r = e2e_client.post("/api/solutions", headers=headers, json=body)
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    if git:
        r2 = e2e_client.patch(
            f"/api/solutions/{sid}",
            headers=headers,
            json={
                "git_connected": True,
                "git_repo_url": "https://example.com/acme/widget.git",
            },
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["git_connected"] is True
    return sid


def test_readme_put_succeeds_on_disconnected_install(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"readme-disc-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug, git=False)

    r = e2e_client.put(
        f"/api/solutions/{sid}/readme",
        headers=headers,
        json={"readme": "# Hand-annotated\n\nLocal note."},
    )
    assert r.status_code == 200, r.text
    assert r.json()["readme"].startswith("# Hand-annotated")

    # Clearing (readme=null) also works on a disconnected install.
    r2 = e2e_client.put(
        f"/api/solutions/{sid}/readme",
        headers=headers,
        json={"readme": None},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["readme"] is None


def test_readme_put_refused_on_git_connected_install(e2e_client, platform_admin):
    headers = platform_admin.headers
    slug = f"readme-git-{uuid.uuid4().hex[:8]}"
    sid = _create_solution(e2e_client, headers, slug, git=True)

    r = e2e_client.put(
        f"/api/solutions/{sid}/readme",
        headers=headers,
        json={"readme": "# Should be refused"},
    )
    assert r.status_code == 409, r.text
    assert "git-connected" in r.json()["detail"].lower()
