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


def _make_tarball(
    repo: str,
    ref: str,
    files: dict[str, bytes],
    public_skills: list[str] | None = None,
    public_skill_targets: dict[str, str] | None = None,
) -> bytes:
    """Build a tarball mimicking what GitHub's codeload returns.

    GitHub wraps everything in a ``<repo-name>-<sha>/`` prefix, so we
    simulate that. ``public_skills`` lists the names that should appear as
    symlinks under the top-level ``skills/`` directory (the plugin allowlist).
    ``public_skill_targets`` optionally lets those aliases point at a different
    real skill folder under ``.claude/skills/``. Defaults to every skill seen
    under ``.claude/skills/`` so existing tests that don't care about filtering
    keep their current shape.
    """
    repo_name = repo.split("/")[-1]
    prefix = f"{repo_name}-{ref}"
    if public_skills is None:
        derived: set[str] = set()
        for relpath in files:
            if not relpath.startswith(".claude/skills/"):
                continue
            tail = relpath[len(".claude/skills/"):]
            name = tail.split("/", 1)[0]
            if name:
                derived.add(name)
        public_skills = sorted(derived)
    if public_skill_targets is None:
        public_skill_targets = {name: name for name in public_skills}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for relpath, contents in files.items():
            data = contents
            info = tarfile.TarInfo(name=f"{prefix}/{relpath}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name in public_skills:
            info = tarfile.TarInfo(name=f"{prefix}/skills/{name}")
            info.type = tarfile.SYMTYPE
            target = public_skill_targets.get(name, name)
            info.linkname = f"../.claude/skills/{target}"
            tar.addfile(info)
    return buf.getvalue()


@pytest.fixture
def stub_github(monkeypatch: pytest.MonkeyPatch):
    """Replace httpx.get so the codeload URL returns our synthetic tarball.

    The fixture returns a closure that the test calls with the file map it
    wants the "repo" to ship.
    """

    state: dict[str, object] = {
        "calls": [],
        "public_skills": None,
        "public_skill_targets": None,
    }

    def install(files: dict[str, bytes], repo: str = "jackmusick/bifrost") -> None:
        def _get(url: str, **_kwargs):
            state["calls"].append(url)  # type: ignore[union-attr]
            ref = "main"
            if "/refs/heads/" in url:
                ref = url.rsplit("/refs/heads/", 1)[1]
            elif "/refs/tags/" in url:
                ref = url.rsplit("/refs/tags/", 1)[1]
            public = state["public_skills"]
            tarball = _make_tarball(
                repo,
                ref,
                files,
                public_skills=public,  # type: ignore[arg-type]
                public_skill_targets=state["public_skill_targets"],  # type: ignore[arg-type]
            )
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

    def test_only_public_skills_are_installed(
        self,
        workspace: Path,
        stub_github,
        capsys: pytest.CaptureFixture,
    ) -> None:
        # Mirror the real repo: bifrost-build is symlinked from skills/ (public);
        # bifrost-debug only exists under .claude/skills/ (internal).
        stub_github["install"](
            {
                ".claude/skills/bifrost-build/SKILL.md": b"public\n",
                ".claude/skills/bifrost-debug/SKILL.md": b"internal\n",
                ".claude/skills/bifrost-secaudit/SKILL.md": b"internal\n",
            },
        )
        # Override the auto-derived allowlist to only expose bifrost-build.
        stub_github["public_skills"] = ["bifrost-build"]  # type: ignore[index]

        rc = skill_module.handle_skill(["update"])
        assert rc == 0

        for root in (".claude/skills", ".agents/skills"):
            assert (workspace / root / "bifrost-build/SKILL.md").is_file()
            assert not (workspace / root / "bifrost-debug").exists()
            assert not (workspace / root / "bifrost-secaudit").exists()
        out = capsys.readouterr().out
        assert "Installed bifrost-build" in out
        assert "bifrost-debug" not in out
        assert "bifrost-secaudit" not in out

    def test_public_skill_alias_installs_real_skill_folder(
        self,
        workspace: Path,
        stub_github,
        capsys: pytest.CaptureFixture,
    ) -> None:
        stub_github["install"](
            {
                ".claude/skills/bifrost-build/SKILL.md": b"public\n",
                ".claude/skills/bifrost-build/notes.md": b"notes\n",
            },
        )
        stub_github["public_skills"] = ["build"]  # type: ignore[index]
        stub_github["public_skill_targets"] = {"build": "bifrost-build"}  # type: ignore[index]

        rc = skill_module.handle_skill(["update"])
        assert rc == 0

        for root in (".claude/skills", ".agents/skills"):
            assert (workspace / root / "bifrost-build/SKILL.md").is_file()
            assert (workspace / root / "bifrost-build/notes.md").is_file()
            assert not (workspace / root / "build").exists()
        out = capsys.readouterr().out
        assert "Installed bifrost-build" in out

    def test_repo_with_no_public_skills_errors(
        self,
        workspace: Path,
        stub_github,
        capsys: pytest.CaptureFixture,
    ) -> None:
        # Repo has skills under .claude/skills/ but none symlinked from skills/.
        # That's the shape of any fork/repo without a Claude Code plugin manifest.
        stub_github["install"](
            {".claude/skills/private/SKILL.md": b"x"},
        )
        stub_github["public_skills"] = []  # type: ignore[index]

        rc = skill_module.handle_skill(["update"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "No public skills" in err

    def test_rejects_tarball_skill_path_traversal(
        self,
        workspace: Path,
        stub_github,
        capsys: pytest.CaptureFixture,
    ) -> None:
        stub_github["install"](
            {
                ".claude/skills/foo/../evil/SKILL.md": b"evil\n",
            },
        )
        stub_github["public_skills"] = ["foo"]  # type: ignore[index]

        rc = skill_module.handle_skill(["update"])

        assert rc == 1
        assert "unsafe skill path" in capsys.readouterr().err
        assert not (workspace / ".claude/skills/evil").exists()
        assert not (workspace / ".agents/skills/evil").exists()

    def test_rejects_tarball_backslash_skill_path(
        self,
        workspace: Path,
        stub_github,
        capsys: pytest.CaptureFixture,
    ) -> None:
        stub_github["install"](
            {
                ".claude/skills/foo\\evil/SKILL.md": b"evil\n",
            },
        )
        stub_github["public_skills"] = ["foo"]  # type: ignore[index]

        rc = skill_module.handle_skill(["update"])

        assert rc == 1
        assert "unsafe skill path" in capsys.readouterr().err
        assert not (workspace / ".claude/skills/foo\\evil").exists()
        assert not (workspace / ".agents/skills/foo\\evil").exists()

    def test_rejects_existing_symlink_skill_target_before_delete(
        self,
        workspace: Path,
        stub_github,
        capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outside = workspace / "outside"
        outside.mkdir()
        target = workspace / ".claude/skills/foo"
        target.mkdir(parents=True)
        original_is_symlink = Path.is_symlink

        def fake_is_symlink(path: Path) -> bool:
            return path == target or original_is_symlink(path)

        monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)
        stub_github["install"](
            {
                ".claude/skills/foo/SKILL.md": b"safe\n",
            },
        )

        rc = skill_module.handle_skill(["update"])

        assert rc == 1
        assert "refusing to replace symlinked skill directory" in capsys.readouterr().err
        assert target.exists()
        assert outside.is_dir()


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
