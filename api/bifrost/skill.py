"""Implementation of ``bifrost skill`` — install/update/remove agent skills.

Skills live in the bifrost repo at ``.claude/skills/<name>/``. Only those
exposed by the Claude Code plugin manifest (i.e. symlinked from the top-level
``skills/`` directory) are considered public and installed by this command —
internal maintainer skills like ``bifrost-debug``/``bifrost-secaudit`` stay
in the repo where they belong.

Public skills are copied into the user's workspace at:

* ``<cwd>/.claude/skills/<name>/`` — picked up by Claude Code.
* ``<cwd>/.agents/skills/<name>/`` — the cross-harness portable convention
  read by Copilot CLI, Codex CLI, Gemini CLI, Cursor (via .agents/skills),
  and others. Same file format, same content.

The command is platform-agnostic — it does NOT call the Bifrost API. It pulls
a tarball from the public GitHub repo via httpx (no `git` binary required)
and unpacks the relevant subdirectories.
"""

from __future__ import annotations

import io
import shutil
import sys
import tarfile
from pathlib import Path
from pathlib import PurePosixPath

import httpx


_DEFAULT_REPO = "gobifrost/bifrost"
_DEFAULT_REF = "main"


def _print_help() -> None:
    print("""
Usage: bifrost skill <subcommand> [options]

Manage Bifrost agent skills. Skills are installed in two locations so they
work across Claude Code, Copilot CLI, Cursor, Codex, and Gemini CLI:

  <cwd>/.claude/skills/<name>/   (Claude Code)
  <cwd>/.agents/skills/<name>/   (cross-harness portable convention)

Subcommands:
  list                    List installed skills in the current workspace
  update                  Install / update all public Bifrost skills from GitHub
  remove <name>           Remove an installed skill from both locations

Options for update:
  --ref <tag-or-branch>   Git ref to pull from (default: main)
  --repo <owner/repo>     GitHub repo to pull from (default: gobifrost/bifrost)

Examples:
  bifrost skill list
  bifrost skill update                  # all skills, latest main
  bifrost skill update --ref v1.4.2     # pin to a release
""".strip())


def handle_skill(args: list[str]) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        _print_help()
        return 0 if args else 1

    sub, sub_args = args[0], args[1:]
    if sub == "list":
        return _handle_list(sub_args)
    if sub == "update":
        return _handle_update(sub_args)
    if sub == "remove":
        return _handle_remove(sub_args)
    print(f"Unknown skill subcommand: {sub}", file=sys.stderr)
    _print_help()
    return 1


def _skill_dirs(cwd: Path) -> tuple[Path, Path]:
    return cwd / ".claude" / "skills", cwd / ".agents" / "skills"


def _handle_list(_args: list[str]) -> int:
    cwd = Path.cwd()
    claude_dir, agents_dir = _skill_dirs(cwd)
    seen: dict[str, list[str]] = {}
    for label, root in (("claude", claude_dir), ("agents", agents_dir)):
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and (entry / "SKILL.md").is_file():
                seen.setdefault(entry.name, []).append(label)

    if not seen:
        print(
            f"No skills installed in {claude_dir} or {agents_dir}.",
            file=sys.stderr,
        )
        return 0

    for name in sorted(seen):
        locations = "+".join(seen[name])
        print(f"{name}\t({locations})")
    return 0


def _handle_remove(args: list[str]) -> int:
    if not args or args[0] in ("-h", "--help"):
        print("Usage: bifrost skill remove <name>", file=sys.stderr)
        return 1
    name = args[0]
    cwd = Path.cwd()
    claude_dir, agents_dir = _skill_dirs(cwd)
    removed_any = False
    for root in (claude_dir, agents_dir):
        target = root / name
        if target.is_dir():
            shutil.rmtree(target)
            print(f"Removed {target}")
            removed_any = True
    if not removed_any:
        print(f"Skill {name!r} not found in either location.", file=sys.stderr)
        return 1
    return 0


def _parse_update_args(args: list[str]) -> tuple[str, str] | None:
    """Returns (ref, repo) or None on error (after printing usage)."""
    ref = _DEFAULT_REF
    repo = _DEFAULT_REPO
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-h", "--help"):
            _print_help()
            return None
        if arg == "--ref":
            if i + 1 >= len(args):
                print("Error: --ref requires a value", file=sys.stderr)
                return None
            ref = args[i + 1]
            i += 2
            continue
        if arg == "--repo":
            if i + 1 >= len(args):
                print("Error: --repo requires a value", file=sys.stderr)
                return None
            repo = args[i + 1]
            i += 2
            continue
        print(f"Error: unexpected argument {arg!r}", file=sys.stderr)
        print("`bifrost skill update` takes no positional args.", file=sys.stderr)
        return None
    return ref, repo


def _handle_update(args: list[str]) -> int:
    parsed = _parse_update_args(args)
    if parsed is None:
        return 1
    ref, repo = parsed

    cwd = Path.cwd()
    claude_dir, agents_dir = _skill_dirs(cwd)

    print(f"Fetching skills from {repo}@{ref} ...")
    try:
        skill_files = _fetch_skill_files(repo, ref)
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Error fetching tarball: {exc}", file=sys.stderr)
        return 1

    targets = sorted({path.split("/", 1)[0] for path in skill_files})
    if not targets:
        print(
            f"No .claude/skills/ entries found in {repo}@{ref}.", file=sys.stderr
        )
        return 1

    for skill_name in targets:
        claude_target = claude_dir / skill_name
        agents_target = agents_dir / skill_name
        files_for_skill = {
            relpath: contents
            for relpath, contents in skill_files.items()
            if relpath == skill_name or relpath.startswith(f"{skill_name}/")
        }
        # Wipe stale state — a removed file in the repo should disappear locally.
        if claude_target.exists():
            shutil.rmtree(claude_target)
        if agents_target.exists():
            shutil.rmtree(agents_target)
        _write_skill(claude_target, skill_name, files_for_skill)
        _write_skill(agents_target, skill_name, files_for_skill)
        print(f"Installed {skill_name} → {claude_target}, {agents_target}")

    return 0


def _fetch_skill_files(repo: str, ref: str) -> dict[str, bytes]:
    """Fetch the repo tarball and return ``{relpath: contents}`` for public skill files.

    ``relpath`` is repo-relative under ``.claude/skills/`` (e.g.
    ``bifrost-build/SKILL.md``).

    Only skills exposed by the repo's Claude Code plugin manifest are returned —
    determined by the symlinks in the top-level ``skills/`` directory. Internal
    skills under ``.claude/skills/`` that are NOT symlinked from ``skills/`` are
    excluded so end users don't end up with maintainer-only skills like
    ``bifrost-debug`` or ``bifrost-secaudit``. The ``skills/`` symlinks are the
    same allowlist consumed by the plugin loader, so the CLI and the plugin stay
    in lockstep automatically.
    """
    url = f"https://codeload.github.com/{repo}/tar.gz/refs/heads/{ref}"
    # Try the branch URL first, fall back to tag URL on 404.
    response = httpx.get(url, timeout=60.0, follow_redirects=True)
    if response.status_code == 404:
        url = f"https://codeload.github.com/{repo}/tar.gz/refs/tags/{ref}"
        response = httpx.get(url, timeout=60.0, follow_redirects=True)
    response.raise_for_status()

    public_skills: set[str] = set()
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
        members = tar.getmembers()

        # First pass: read top-level skills/ symlinks to build the allowlist.
        # Some Windows checkouts materialize symlinks as tiny text files whose
        # contents are the intended target. Accept that shape too so a repacked
        # archive from Windows still exposes the same public skills.
        # The public alias and the real skill folder name can differ, e.g.
        # ``skills/build -> ../.claude/skills/bifrost-build``.
        for member in members:
            parts = member.name.split("/", 1)
            if len(parts) != 2:
                continue
            inner = parts[1]
            if not inner.startswith("skills/") or inner == "skills/":
                continue
            name = inner[len("skills/"):].rstrip("/")
            if not name or "/" in name:
                continue
            if member.issym() or member.islnk():
                target_name = PurePosixPath(member.linkname).name
                public_skills.add(target_name or name)
                continue
            if member.isfile():
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                try:
                    target = extracted.read().decode("utf-8").strip()
                except UnicodeDecodeError:
                    continue
                target_name = PurePosixPath(target).name
                if target_name:
                    public_skills.add(target_name)

        if not public_skills:
            raise ValueError(
                f"No public skills found under skills/ in {repo}@{ref}. "
                "The repo may not expose a Claude Code plugin manifest."
            )

        # Second pass: extract content for allowlisted skills only.
        for member in members:
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) != 2:
                continue
            inner = parts[1]
            prefix = ".claude/skills/"
            if not inner.startswith(prefix):
                continue
            relpath = inner[len(prefix):]
            if not relpath:
                continue
            skill_name = relpath.split("/", 1)[0]
            if skill_name not in public_skills:
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            files[relpath] = extracted.read()
    return files


def _write_skill(target_root: Path, skill_name: str, files: dict[str, bytes]) -> None:
    """Write ``files`` rooted at ``target_root``.

    ``files`` keys are relative to the parent of ``target_root`` (e.g.
    ``bifrost-build/SKILL.md``); strip the leading ``<skill_name>/`` so the
    on-disk layout is ``target_root/SKILL.md`` etc.
    """
    target_root.mkdir(parents=True, exist_ok=True)
    prefix = f"{skill_name}/"
    for relpath, contents in files.items():
        if relpath == skill_name:
            # Edge case: a file at the skill root with no subpath.
            continue
        if not relpath.startswith(prefix):
            continue
        sub = relpath[len(prefix):]
        out_path = target_root / sub
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(contents)


__all__ = ["handle_skill"]
