"""Unit tests for ``bifrost skill`` (list / update / remove).

The fetch path is exercised against a synthetic in-memory tarball so the
test doesn't hit GitHub. The on-disk side runs against a tmp_path workspace
so we can assert on what landed in ``.claude/skills/`` and ``.agents/skills/``.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import httpx
import pytest

from bifrost import skill as skill_module


def _make_tarball(repo: str, ref: str, files: dict[str, bytes]) -> bytes:
    """Build a tarball mimicking what GitHub's codeload returns.

    GitHub wraps everything in a ``<repo-name>-<sha>/`` prefix, so we
    simulate that.
    """
    repo_name = repo.split("/")[-1]
    prefix = f"{repo_name}-{ref}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for relpath, contents in files.items():
            data = contents
            info = tarfile.TarInfo(name=f"{prefix}/{relpath}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def stub_github(monkeypatch: pytest.MonkeyPatch):
    """Replace httpx.get so the codeload URL returns our synthetic tarball.

    The fixture returns a closure that the test calls with the file map it
    wants the "repo" to ship.
    """

    state: dict[str, object] = {"calls": []}

    def install(files: dict[str, bytes], repo: str = "jackmusick/bifrost") -> None:
        def _get(url: str, **_kwargs):
            state["calls"].append(url)  # type: ignore[union-attr]
            ref = "main"
            if "/refs/heads/" in url:
                ref = url.rsplit("/refs/heads/", 1)[1]
            elif "/refs/tags/" in url:
                ref = url.rsplit("/refs/tags/", 1)[1]
            tarball = _make_tarball(repo, ref, files)
            request = httpx.Request("GET", url)
            return httpx.Response(200, content=tarball, request=request)

        monkeypatch.setattr(skill_module.httpx, "get", _get)

    state["install"] = install  # type: ignore[index]
    return state


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run ``bifrost skill`` commands as if invoked from ``tmp_path``."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestSkillUpdate:
    def test_install_all_skills_writes_to_both_dirs(
        self,
        workspace: Path,
        stub_github,
        capsys: pytest.CaptureFixture,
    ) -> None:
        stub_github["install"](
            {
                ".claude/skills/foo/SKILL.md": b"# Foo skill\n",
                ".claude/skills/foo/notes.md": b"notes\n",
                ".claude/skills/bar/SKILL.md": b"# Bar skill\n",
                # Sibling files that aren't skills must be ignored.
                ".claude/commands/some.md": b"ignore me\n",
                "README.md": b"ignore me too\n",
            }
        )

        rc = skill_module.handle_skill(["update"])
        assert rc == 0

        for root in (".claude/skills", ".agents/skills"):
            for name in ("foo", "bar"):
                assert (workspace / root / name / "SKILL.md").is_file()
        assert (workspace / ".claude/skills/foo/notes.md").is_file()
        assert (workspace / ".agents/skills/foo/notes.md").is_file()
        # Non-skill content was not copied.
        assert not (workspace / ".claude/skills/some.md").exists()
        assert not (workspace / "README.md").exists()
        captured = capsys.readouterr().out
        assert "Installed foo" in captured
        assert "Installed bar" in captured

    def test_positional_arg_rejected(
        self,
        workspace: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        # Update is all-or-nothing. A positional arg is a usage error.
        rc = skill_module.handle_skill(["update", "foo"])
        assert rc == 1
        assert "no positional args" in capsys.readouterr().err

    def test_update_wipes_stale_files_before_writing(
        self,
        workspace: Path,
        stub_github,
    ) -> None:
        # Pre-existing file that should be removed by the update.
        stale = workspace / ".claude/skills/foo/old.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("stale")

        stub_github["install"](
            {
                ".claude/skills/foo/SKILL.md": b"new",
            }
        )
        rc = skill_module.handle_skill(["update"])
        assert rc == 0
        assert (workspace / ".claude/skills/foo/SKILL.md").read_bytes() == b"new"
        assert not stale.exists()

    def test_ref_flag_is_used_in_url(
        self,
        workspace: Path,
        stub_github,
    ) -> None:
        stub_github["install"]({".claude/skills/foo/SKILL.md": b"x"})
        skill_module.handle_skill(["update", "--ref", "v9.9.9"])
        # The fetcher tries the branch URL first, falls back to tag — assert
        # that the requested ref reached at least one of those URLs.
        urls = stub_github["calls"]  # type: ignore[index]
        assert any("v9.9.9" in u for u in urls), urls

    def test_repo_flag_overrides_default(
        self,
        workspace: Path,
        stub_github,
    ) -> None:
        stub_github["install"](
            {".claude/skills/foo/SKILL.md": b"x"}, repo="jackmusick/other-repo"
        )
        skill_module.handle_skill(["update", "--repo", "jackmusick/other-repo"])
        urls = stub_github["calls"]  # type: ignore[index]
        assert any("jackmusick/other-repo" in u for u in urls), urls


class TestSkillList:
    def test_list_finds_skills_in_both_dirs(
        self,
        workspace: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        (workspace / ".claude/skills/alpha").mkdir(parents=True)
        (workspace / ".claude/skills/alpha/SKILL.md").write_text("a")
        (workspace / ".agents/skills/beta").mkdir(parents=True)
        (workspace / ".agents/skills/beta/SKILL.md").write_text("b")
        (workspace / ".claude/skills/both").mkdir(parents=True)
        (workspace / ".claude/skills/both/SKILL.md").write_text("c")
        (workspace / ".agents/skills/both").mkdir(parents=True)
        (workspace / ".agents/skills/both/SKILL.md").write_text("c")

        rc = skill_module.handle_skill(["list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "alpha\t(claude)" in out
        assert "beta\t(agents)" in out
        assert "both\t(claude+agents)" in out

    def test_list_when_no_skills_installed(
        self,
        workspace: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        rc = skill_module.handle_skill(["list"])
        assert rc == 0
        assert "No skills installed" in capsys.readouterr().err


class TestSkillRemove:
    def test_remove_clears_both_locations(
        self,
        workspace: Path,
    ) -> None:
        for root in (".claude/skills", ".agents/skills"):
            d = workspace / root / "victim"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("bye")
        rc = skill_module.handle_skill(["remove", "victim"])
        assert rc == 0
        assert not (workspace / ".claude/skills/victim").exists()
        assert not (workspace / ".agents/skills/victim").exists()

    def test_remove_unknown_skill_errors(
        self,
        workspace: Path,
        capsys: pytest.CaptureFixture,
    ) -> None:
        rc = skill_module.handle_skill(["remove", "ghost"])
        assert rc == 1
        assert "not found" in capsys.readouterr().err
