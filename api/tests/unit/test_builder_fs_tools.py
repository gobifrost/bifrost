"""Adversarial coverage for the builder's safe filesystem + archive primitives.

These tests are the contract for `src/services/builder/fs_tools.py`: the model
supplies every path string, so each rejection below is a live attack surface,
not a hypothetical.
"""

from __future__ import annotations

import os
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from src.services.builder.fs_tools import (
    WorkspaceLimits,
    WorkspaceRoot,
    WorkspaceViolation,
    safe_extract_zip,
)


@pytest.fixture
def limits() -> WorkspaceLimits:
    return WorkspaceLimits(
        max_files=10,
        max_file_bytes=1024,
        max_total_bytes=4096,
        max_read_bytes=64,
    )


@pytest.fixture
def workspace(tmp_path: Path, limits: WorkspaceLimits) -> WorkspaceRoot:
    root = tmp_path / "ws"
    root.mkdir()
    return WorkspaceRoot(root, limits)


# --- path syntax rejection ------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.txt",
        "a/../../escape.txt",
        "a/b/../../../escape.txt",
        "..",
        "/etc/passwd",
        "/",
        "with\x00nul.txt",
        "windows\\style.txt",
        "C:/Windows/system32",
        "c:nested/file.txt",
        "",
        "   ",
    ],
)
def test_resolve_rejects_hostile_paths(workspace: WorkspaceRoot, bad_path: str) -> None:
    with pytest.raises(WorkspaceViolation):
        workspace.read_file(bad_path)
    with pytest.raises(WorkspaceViolation):
        workspace.write_file(bad_path, b"data")


def test_dotdot_rejected_even_when_it_stays_inside(workspace: WorkspaceRoot) -> None:
    """`a/../b.txt` resolves inside the root but is still refused as syntax."""
    workspace.make_directory("a")
    with pytest.raises(WorkspaceViolation, match=r"\.\."):
        workspace.write_file("a/../b.txt", b"data")


# --- symlink and hardlink rejection ---------------------------------------


def test_symlinked_directory_pointing_outside_is_rejected(
    tmp_path: Path, limits: WorkspaceLimits
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"top secret")
    (root / "link").symlink_to(outside, target_is_directory=True)

    ws = WorkspaceRoot(root, limits)

    with pytest.raises(WorkspaceViolation, match="symlink"):
        ws.read_file("link/secret.txt")
    with pytest.raises(WorkspaceViolation, match="symlink"):
        ws.write_file("link/planted.txt", b"pwned")

    assert not (outside / "planted.txt").exists()


def test_symlinked_file_is_rejected(tmp_path: Path, limits: WorkspaceLimits) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    target = tmp_path / "outside.txt"
    target.write_bytes(b"top secret")
    (root / "alias.txt").symlink_to(target)

    ws = WorkspaceRoot(root, limits)
    with pytest.raises(WorkspaceViolation, match="symlink"):
        ws.read_file("alias.txt")


def test_symlinked_file_is_not_listed(tmp_path: Path, limits: WorkspaceLimits) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "real.txt").write_bytes(b"ok")
    (root / "alias.txt").symlink_to(tmp_path / "real.txt")

    assert WorkspaceRoot(root, limits).list_files() == ["real.txt"]


def test_hardlinked_file_read_is_rejected(
    tmp_path: Path, limits: WorkspaceLimits
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    original = root / "a.txt"
    original.write_bytes(b"payload")
    os.link(original, root / "b.txt")

    ws = WorkspaceRoot(root, limits)
    with pytest.raises(WorkspaceViolation, match="hardlink"):
        ws.read_file("a.txt")
    with pytest.raises(WorkspaceViolation, match="hardlink"):
        ws.read_file("b.txt")


def test_fifo_is_rejected(tmp_path: Path, limits: WorkspaceLimits) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    os.mkfifo(root / "pipe")

    ws = WorkspaceRoot(root, limits)
    with pytest.raises(WorkspaceViolation, match="regular file"):
        ws.read_file("pipe")


# --- reads ----------------------------------------------------------------


def test_read_missing_file(workspace: WorkspaceRoot) -> None:
    with pytest.raises(WorkspaceViolation, match="not found"):
        workspace.read_file("nope.txt")


def test_read_truncates_at_cap(workspace: WorkspaceRoot) -> None:
    workspace.write_file("big.txt", b"x" * 200)

    content, truncated = workspace.read_file("big.txt")

    assert truncated is True
    assert content == b"x" * workspace.limits.max_read_bytes


def test_read_under_cap_is_not_truncated(workspace: WorkspaceRoot) -> None:
    workspace.write_file("small.txt", b"hello")

    assert workspace.read_file("small.txt") == (b"hello", False)


# --- writes ---------------------------------------------------------------


def test_write_is_atomic_and_leaves_no_temp(workspace: WorkspaceRoot) -> None:
    workspace.write_file("dir/file.txt", b"first")
    workspace.write_file("dir/file.txt", b"second")

    assert (workspace.root / "dir" / "file.txt").read_bytes() == b"second"
    assert workspace.list_files() == ["dir/file.txt"]


def test_failed_write_leaves_no_torn_file_and_no_temp(
    workspace: WorkspaceRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace.write_file("file.txt", b"original")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("src.services.builder.fs_tools.os.replace", boom)

    with pytest.raises(OSError):
        workspace.write_file("file.txt", b"replacement")

    assert (workspace.root / "file.txt").read_bytes() == b"original"
    assert workspace.list_files() == ["file.txt"]


def test_write_rejects_over_per_file_limit(workspace: WorkspaceRoot) -> None:
    with pytest.raises(WorkspaceViolation, match="per-file"):
        workspace.write_file("big.bin", b"x" * (workspace.limits.max_file_bytes + 1))
    assert workspace.list_files() == []


def test_write_rejects_over_total_workspace_limit(workspace: WorkspaceRoot) -> None:
    chunk = b"x" * 1024
    for index in range(4):
        workspace.write_file(f"f{index}.bin", chunk)

    with pytest.raises(WorkspaceViolation, match="total byte limit"):
        workspace.write_file("overflow.bin", chunk)


def test_overwrite_counts_only_the_delta(workspace: WorkspaceRoot) -> None:
    """Replacing a full-size file must not trip the total-bytes ceiling."""
    chunk = b"x" * 1024
    for index in range(4):
        workspace.write_file(f"f{index}.bin", chunk)

    workspace.write_file("f0.bin", b"y" * 1024)

    assert workspace.read_file("f0.bin")[0].startswith(b"y")


def test_write_rejects_over_file_count_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    ws = WorkspaceRoot(root, WorkspaceLimits(max_files=2, max_read_bytes=64))
    ws.write_file("a.txt", b"a")
    ws.write_file("b.txt", b"b")

    with pytest.raises(WorkspaceViolation, match="file count"):
        ws.write_file("c.txt", b"c")


# --- delete / mkdir -------------------------------------------------------


def test_delete_file(workspace: WorkspaceRoot) -> None:
    workspace.write_file("gone.txt", b"bye")
    workspace.delete_file("gone.txt")

    assert workspace.list_files() == []


def test_delete_rejects_directory(workspace: WorkspaceRoot) -> None:
    workspace.make_directory("adir")
    with pytest.raises(WorkspaceViolation, match="regular file"):
        workspace.delete_file("adir")


def test_delete_rejects_traversal(workspace: WorkspaceRoot) -> None:
    with pytest.raises(WorkspaceViolation):
        workspace.delete_file("../outside.txt")


def test_make_directory_is_idempotent(workspace: WorkspaceRoot) -> None:
    workspace.make_directory("a/b/c")
    workspace.make_directory("a/b/c")

    assert (workspace.root / "a" / "b" / "c").is_dir()


def test_make_directory_rejects_existing_file(workspace: WorkspaceRoot) -> None:
    workspace.write_file("thing", b"data")
    with pytest.raises(WorkspaceViolation, match="not a directory"):
        workspace.make_directory("thing")


# --- search ---------------------------------------------------------------


def test_search_text_finds_matches_and_respects_glob(
    workspace: WorkspaceRoot,
) -> None:
    workspace.write_file("src/a.py", b"import os\nvalue = 1\n")
    workspace.write_file("src/b.txt", b"value = 2\n")

    hits = workspace.search_text(r"value", rel_glob="**/*.py")

    assert [(h.path, h.line_number, h.line) for h in hits] == [
        ("src/a.py", 2, "value = 1")
    ]


def test_search_text_bounds_hit_count(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    ws = WorkspaceRoot(root, WorkspaceLimits(max_read_bytes=4096))
    ws.write_file("many.txt", b"hit\n" * 50)

    assert len(ws.search_text("hit", max_hits=5)) == 5


def test_search_text_skips_files_over_the_read_cap(workspace: WorkspaceRoot) -> None:
    """Search never reads more than the read cap, so oversized files are skipped."""
    workspace.write_file("big.txt", b"hit\n" * 50)

    assert workspace.search_text("hit") == []


def test_search_text_default_glob_covers_root_level_files(tmp_path: Path) -> None:
    """The default `**/*` must not skip files sitting at the workspace root."""
    root = tmp_path / "ws"
    root.mkdir()
    ws = WorkspaceRoot(root, WorkspaceLimits(max_read_bytes=4096))
    ws.write_file("top.py", b"needle\n")
    ws.write_file("nested/deep.py", b"needle\n")

    assert {h.path for h in ws.search_text("needle")} == {"top.py", "nested/deep.py"}


def test_search_text_rejects_bad_regex(workspace: WorkspaceRoot) -> None:
    with pytest.raises(WorkspaceViolation, match="invalid search pattern"):
        workspace.search_text("(unclosed")


# --- zip extraction -------------------------------------------------------


def _write_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_extract_happy_path(tmp_path: Path, limits: WorkspaceLimits) -> None:
    archive = _write_zip(
        tmp_path / "src.zip",
        {"app/main.py": b"print('hi')\n", "README.md": b"# hello\n"},
    )
    dest = tmp_path / "out"
    dest.mkdir()

    assert safe_extract_zip(archive, dest, limits) == ["README.md", "app/main.py"]
    assert (dest / "app" / "main.py").read_bytes() == b"print('hi')\n"


@pytest.mark.parametrize(
    "member_name",
    ["../escape.txt", "a/../../escape.txt", "/abs.txt", "windows\\name.txt"],
)
def test_extract_rejects_hostile_member_names(
    tmp_path: Path, limits: WorkspaceLimits, member_name: str
) -> None:
    archive = _write_zip(tmp_path / "bad.zip", {member_name: b"payload"})
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(WorkspaceViolation):
        safe_extract_zip(archive, dest, limits)

    assert list(dest.iterdir()) == []
    assert not (tmp_path / "escape.txt").exists()


def test_extract_rejects_symlink_member(
    tmp_path: Path, limits: WorkspaceLimits
) -> None:
    archive_path = tmp_path / "link.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        info = zipfile.ZipInfo("link.txt")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")

    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(WorkspaceViolation, match="symlink member"):
        safe_extract_zip(archive_path, dest, limits)


def test_extract_rejects_duplicate_members(
    tmp_path: Path, limits: WorkspaceLimits
) -> None:
    archive_path = tmp_path / "dupe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("a.txt", "first")
        archive.writestr("a.txt", "second")

    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(WorkspaceViolation, match="duplicate member"):
        safe_extract_zip(archive_path, dest, limits)


def test_extract_rejects_case_insensitive_duplicates(
    tmp_path: Path, limits: WorkspaceLimits
) -> None:
    archive = _write_zip(tmp_path / "case.zip", {"a.txt": b"one", "A.TXT": b"two"})
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(WorkspaceViolation, match="duplicate member"):
        safe_extract_zip(archive, dest, limits)


def test_extract_rejects_oversized_member(
    tmp_path: Path, limits: WorkspaceLimits
) -> None:
    archive = _write_zip(
        tmp_path / "big.zip", {"big.bin": b"x" * (limits.max_file_bytes + 1)}
    )
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(WorkspaceViolation, match="per-file byte limit"):
        safe_extract_zip(archive, dest, limits)

    assert list(dest.iterdir()) == []


def test_extract_rejects_oversized_total(tmp_path: Path) -> None:
    limits = WorkspaceLimits(
        max_files=10, max_file_bytes=1024, max_total_bytes=2048, max_read_bytes=64
    )
    archive = _write_zip(
        tmp_path / "total.zip", {f"f{i}.bin": b"x" * 1024 for i in range(4)}
    )
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(WorkspaceViolation, match="total byte limit"):
        safe_extract_zip(archive, dest, limits)


def test_extract_rejects_too_many_members(tmp_path: Path) -> None:
    limits = WorkspaceLimits(
        max_files=3, max_file_bytes=1024, max_total_bytes=4096, max_read_bytes=64
    )
    archive = _write_zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(4)})
    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(WorkspaceViolation, match="max file count"):
        safe_extract_zip(archive, dest, limits)


def test_extract_rejects_lying_declared_size(
    tmp_path: Path, limits: WorkspaceLimits
) -> None:
    """A member whose declared file_size understates its real payload.

    The up-front declared-size check passes because the header lies. Extraction
    must still refuse it and write nothing: the falsified size desynchronises
    the stream from its CRC, and that surfaces as a WorkspaceViolation rather
    than leaking a raw zipfile error to the caller.
    """
    archive_path = _write_zip(
        tmp_path / "liar.zip", {"payload.bin": b"x" * (limits.max_file_bytes + 512)}
    )
    _falsify_declared_sizes(archive_path, claimed_size=16)

    dest = tmp_path / "out"
    dest.mkdir()

    with pytest.raises(WorkspaceViolation, match="corrupt member stream"):
        safe_extract_zip(archive_path, dest, limits)

    assert not (dest / "payload.bin").exists()
    assert list(dest.iterdir()) == []


def test_extract_streamed_byte_budget_stops_oversized_expansion(
    tmp_path: Path,
) -> None:
    """The running byte counter, not the header, is what bounds bytes on disk.

    Limits are lowered between writing the archive and extracting it, so every
    declared size is already within the up-front check's view of a legal file
    and only the per-chunk accounting can reject the expansion.
    """
    archive = _write_zip(
        tmp_path / "expand.zip", {f"f{i}.bin": b"x" * 4096 for i in range(4)}
    )
    dest = tmp_path / "out"
    dest.mkdir()

    tight = WorkspaceLimits(
        max_files=10, max_file_bytes=8192, max_total_bytes=10000, max_read_bytes=64
    )

    with pytest.raises(WorkspaceViolation, match="total byte limit"):
        safe_extract_zip(archive, dest, tight)


def _falsify_declared_sizes(archive_path: Path, claimed_size: int) -> None:
    """Rewrite every stored uncompressed-size field to ``claimed_size``.

    Patches both the local file headers (signature ``PK\\x03\\x04``, size at
    offset 22) and the central directory entries (``PK\\x01\\x02``, offset 24)
    so ``ZipInfo.file_size`` lies while the compressed stream stays intact. The
    archive is written STORED, so compressed size is left alone deliberately —
    only the uncompressed claim is falsified.
    """
    raw = bytearray(archive_path.read_bytes())
    packed = struct.pack("<I", claimed_size)

    offset = 0
    while (found := raw.find(b"PK\x03\x04", offset)) != -1:
        raw[found + 22 : found + 26] = packed
        offset = found + 4

    offset = 0
    while (found := raw.find(b"PK\x01\x02", offset)) != -1:
        raw[found + 24 : found + 28] = packed
        offset = found + 4

    archive_path.write_bytes(raw)


def test_extract_then_workspace_round_trip(
    tmp_path: Path, limits: WorkspaceLimits
) -> None:
    archive = _write_zip(
        tmp_path / "src.zip",
        {"app/main.py": b"value = 1\n", "app/util/helper.py": b"value = 2\n"},
    )
    dest = tmp_path / "ws"
    dest.mkdir()

    safe_extract_zip(archive, dest, limits)
    ws = WorkspaceRoot(dest, limits)

    assert ws.list_files() == ["app/main.py", "app/util/helper.py"]
    assert ws.read_file("app/main.py") == (b"value = 1\n", False)
    assert [h.path for h in ws.search_text("value")] == [
        "app/main.py",
        "app/util/helper.py",
    ]

    ws.write_file("app/new.py", b"value = 3\n")
    ws.delete_file("app/util/helper.py")

    assert ws.list_files() == ["app/main.py", "app/new.py"]
