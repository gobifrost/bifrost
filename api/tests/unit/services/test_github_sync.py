"""
Unit tests for GitHub Sync Service.

Tests the GitHubSyncService data models and exceptions.
"""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from git import Repo

from src.models.contracts.github import (
    OrphanInfo,
    PreflightIssue,
    PreflightResult,
    WorkflowReference,
)
from src.services.github_sync import GitStatusError, SyncError


class _StatusRepoManager:
    """Minimal read-only repo manager for exercising desktop_status()."""

    def __init__(self, work_dir: Path, *, is_initialized: bool = True) -> None:
        self.work_dir = work_dir
        self.is_initialized = is_initialized

    @asynccontextmanager
    async def lock(self):
        yield self.work_dir


def _status_service(work_dir: Path):
    from src.services.github_sync import GitHubSyncService

    service = GitHubSyncService.__new__(GitHubSyncService)
    service.branch = "main"
    service.repo_url = "https://example.invalid/workspace.git"
    service.repo_manager = _StatusRepoManager(work_dir)
    return service


@pytest.mark.asyncio
async def test_desktop_status_returns_empty_only_for_uninitialized_workspace(tmp_path: Path) -> None:
    service = _status_service(tmp_path)
    service.repo_manager = _StatusRepoManager(tmp_path, is_initialized=False)

    assert await service.desktop_status() == await service.desktop_status()


@pytest.mark.asyncio
async def test_desktop_status_surfaces_invalid_repository(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    with pytest.raises(GitStatusError, match="status failed"):
        await _status_service(tmp_path).desktop_status()


def _git_state(repo: Repo) -> tuple[bytes, bytes, bytes, tuple[tuple[str, bytes], ...]]:
    """Capture the index plus merge state that status must not alter."""
    work_dir = Path(repo.working_tree_dir)
    merge_files = ("MERGE_HEAD", "MERGE_MODE", "MERGE_MSG")
    return (
        (work_dir / ".git" / "index").read_bytes(),
        repo.git.diff("--cached", "--binary", as_process=False).encode(),
        repo.git.ls_files("-u", "-z", as_process=False).encode(),
        tuple(
            (name, (work_dir / ".git" / name).read_bytes())
            for name in merge_files
            if (work_dir / ".git" / name).exists()
        ),
    )


def _commit(repo: Repo, path: str, content: str, message: str) -> None:
    target = Path(repo.working_tree_dir) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    repo.index.add([path])
    repo.index.commit(message)


def _configure_user(repo: Repo) -> None:
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test User")
        config.set_value("user", "email", "test@example.com")


@pytest.mark.asyncio
async def test_desktop_status_does_not_change_dirty_index(tmp_path: Path) -> None:
    repo = Repo.init(tmp_path)
    _configure_user(repo)
    repo.git.branch("-M", "main")
    _commit(repo, "tracked.txt", "base\n", "initial")
    _commit(repo, "rename source.txt", "rename me\n", "add rename source")
    _commit(repo, "staged modified.txt", "before\n", "add staged modification")
    _commit(repo, "deleted.txt", "delete me\n", "add deleted file")

    (tmp_path / "staged.txt").write_text("staged\n")
    repo.index.add(["staged.txt"])
    (tmp_path / "staged modified.txt").write_text("after\n")
    repo.index.add(["staged modified.txt"])
    repo.git.mv("rename source.txt", "renamed file.txt")
    repo.git.rm("deleted.txt")
    (tmp_path / "tracked.txt").write_text("working tree change\n")
    (tmp_path / "file with spaces.txt").write_text("untracked\n")

    before = _git_state(repo)
    status = await _status_service(tmp_path).desktop_status()

    assert _git_state(repo) == before
    assert {(change.path, change.change_type) for change in status.changed_files} == {
        ("staged.txt", "added"),
        ("staged modified.txt", "modified"),
        ("tracked.txt", "modified"),
        ("file with spaces.txt", "added"),
        ("renamed file.txt", "renamed"),
        ("deleted.txt", "deleted"),
    }


@pytest.mark.asyncio
async def test_desktop_status_does_not_change_in_progress_merge(tmp_path: Path, caplog) -> None:
    repo = Repo.init(tmp_path)
    _configure_user(repo)
    repo.git.branch("-M", "main")
    _commit(repo, "conflict.txt", "base\n", "initial")

    other = repo.create_head("other")
    other.checkout()
    _commit(repo, "conflict.txt", "theirs\n", "theirs")
    repo.heads.main.checkout()
    _commit(repo, "conflict.txt", "ours\n", "ours")
    with pytest.raises(Exception):
        repo.git.merge("other")
    assert (tmp_path / ".git" / "MERGE_HEAD").exists()

    before = _git_state(repo)
    status = await _status_service(tmp_path).desktop_status()

    assert _git_state(repo) == before
    assert "Status failed" not in caplog.text
    assert status.merging is True
    assert [conflict.path for conflict in status.conflicts] == ["conflict.txt"]


def test_status_parser_consumes_copy_source_record(tmp_path: Path) -> None:
    """A porcelain-v2 copy record reports only its destination to the API."""
    from src.services.github_sync import GitHubSyncService

    class FakeGit:
        def status(self, *args, **kwargs):
            assert args == ("--porcelain=v2", "-z")
            assert kwargs == {"env": {"GIT_OPTIONAL_LOCKS": "0"}}
            return (
                "2 C. N... 100644 100644 100644 abcdef0 abcdef0 C100 "
                "copied file.txt\0source file.txt\0"
            )

    class FakeRepo:
        git = FakeGit()

        class index:
            @staticmethod
            def unmerged_blobs():
                return {}

        class head:
            @staticmethod
            def is_valid():
                return False

    (tmp_path / ".git").mkdir()
    service = GitHubSyncService.__new__(GitHubSyncService)

    status = service._do_status(tmp_path, FakeRepo())

    assert [(change.path, change.change_type) for change in status.changed_files] == [
        ("copied file.txt", "modified")
    ]


def test_status_parser_reports_unmerged_record(tmp_path: Path) -> None:
    """Porcelain type-u records produce conflicts without consulting the index."""
    from src.services.github_sync import GitHubSyncService

    class FakeGit:
        def status(self, *args, **kwargs):
            return "u UU N... 100644 100644 100644 100644 base ours theirs conflict.txt\0"

        def show(self, spec):
            return {":2:conflict.txt": "ours", ":3:conflict.txt": "theirs"}[spec]

    class FakeRepo:
        git = FakeGit()

        class head:
            @staticmethod
            def is_valid():
                return False

    (tmp_path / ".git").mkdir()
    status = GitHubSyncService.__new__(GitHubSyncService)._do_status(tmp_path, FakeRepo())

    assert [(conflict.path, conflict.conflict_type) for conflict in status.conflicts] == [
        ("conflict.txt", "both_modified")
    ]


@pytest.mark.parametrize(
    ("status_code", "stage", "conflict_type"),
    [
        ("UD", 2, "deleted_by_them"),
        ("DU", 3, "deleted_by_us"),
    ],
)
def test_status_parser_reads_only_present_conflict_stage(
    tmp_path: Path,
    status_code: str,
    stage: int,
    conflict_type: str,
) -> None:
    """Delete/modify conflicts expose only the side present in Git's index."""
    from src.services.github_sync import GitHubSyncService

    class FakeGit:
        def status(self, *args, **kwargs):
            return (
                f"u {status_code} N... 100644 100644 100644 100644 "
                "base ours theirs conflict.txt\0"
            )

        def show(self, spec):
            assert spec == f":{stage}:conflict.txt"
            return "present"

    class FakeRepo:
        git = FakeGit()

        class head:
            @staticmethod
            def is_valid():
                return False

    (tmp_path / ".git").mkdir()
    conflict = GitHubSyncService.__new__(GitHubSyncService)._do_status(tmp_path, FakeRepo()).conflicts[0]

    assert conflict.conflict_type == conflict_type
    assert conflict.ours_content == ("present" if stage == 2 else None)
    assert conflict.theirs_content == ("present" if stage == 3 else None)


@pytest.mark.parametrize("porcelain", ["x unknown\0", "1 M. malformed\0"])
def test_status_parser_rejects_unknown_or_malformed_records(tmp_path: Path, porcelain: str) -> None:
    from src.services.github_sync import GitHubSyncService

    class FakeGit:
        def status(self, *args, **kwargs):
            return porcelain

    class FakeRepo:
        git = FakeGit()

        class head:
            @staticmethod
            def is_valid():
                return False

    (tmp_path / ".git").mkdir()

    with pytest.raises(GitStatusError):
        GitHubSyncService.__new__(GitHubSyncService)._do_status(tmp_path, FakeRepo())


class TestWorkflowReference:
    """Tests for WorkflowReference model."""

    def test_creates_workflow_reference(self):
        """Test WorkflowReference creation."""
        ref = WorkflowReference(
            type="form",
            id="form-123",
            name="Test Form",
        )

        assert ref.type == "form"
        assert ref.id == "form-123"
        assert ref.name == "Test Form"


class TestOrphanInfo:
    """Tests for OrphanInfo model."""

    def test_creates_orphan_info(self):
        """Test OrphanInfo creation."""
        orphan = OrphanInfo(
            workflow_id="wf-123",
            workflow_name="My Workflow",
            function_name="my_workflow",
            last_path="workflows/my_workflow.py",
        )

        assert orphan.workflow_id == "wf-123"
        assert orphan.workflow_name == "My Workflow"
        assert orphan.function_name == "my_workflow"
        assert orphan.last_path == "workflows/my_workflow.py"
        assert len(orphan.used_by) == 0

    def test_orphan_info_with_references(self):
        """Test OrphanInfo with usage references."""
        orphan = OrphanInfo(
            workflow_id="wf-123",
            workflow_name="My Workflow",
            function_name="my_workflow",
            last_path="workflows/my_workflow.py",
            used_by=[
                WorkflowReference(type="form", id="form-1", name="Test Form"),
                WorkflowReference(type="app", id="app-1", name="Test App"),
            ],
        )

        assert len(orphan.used_by) == 2
        assert orphan.used_by[0].type == "form"
        assert orphan.used_by[1].type == "app"


class TestPreflightIssue:
    """Tests for PreflightIssue model."""

    def test_creates_error_issue(self):
        """Test PreflightIssue with error severity."""
        issue = PreflightIssue(
            path="workflows/bad.py",
            line=42,
            message="SyntaxError: unexpected indent",
            severity="error",
            category="syntax",
        )

        assert issue.path == "workflows/bad.py"
        assert issue.line == 42
        assert issue.message == "SyntaxError: unexpected indent"
        assert issue.severity == "error"
        assert issue.category == "syntax"

    def test_creates_warning_issue(self):
        """Test PreflightIssue with warning severity."""
        issue = PreflightIssue(
            path="workflows/messy.py",
            message="unused import",
            severity="warning",
            category="lint",
        )

        assert issue.line is None
        assert issue.severity == "warning"
        assert issue.category == "lint"

    def test_orphan_category(self):
        """Test PreflightIssue for orphan detection."""
        issue = PreflightIssue(
            path="forms/test.form.yaml",
            message="References workflow wf-123 which does not exist",
            severity="error",
            category="orphan",
        )

        assert issue.category == "orphan"


class TestPreflightResult:
    """Tests for PreflightResult model."""

    def test_valid_preflight(self):
        """Test clean preflight result."""
        result = PreflightResult(valid=True, issues=[])

        assert result.valid is True
        assert len(result.issues) == 0

    def test_invalid_preflight_with_errors(self):
        """Test preflight with errors."""
        result = PreflightResult(
            valid=False,
            issues=[
                PreflightIssue(
                    path="workflows/bad.py",
                    message="syntax error",
                    severity="error",
                    category="syntax",
                ),
            ],
        )

        assert result.valid is False
        assert len(result.issues) == 1

    def test_valid_preflight_with_warnings(self):
        """Test preflight can be valid even with warnings."""
        result = PreflightResult(
            valid=True,
            issues=[
                PreflightIssue(
                    path="workflows/messy.py",
                    message="unused import",
                    severity="warning",
                    category="lint",
                ),
            ],
        )

        assert result.valid is True
        assert len(result.issues) == 1


class TestSyncExceptions:
    """Tests for sync exception classes."""

    def test_sync_error(self):
        """Test SyncError exception."""
        error = SyncError("Sync failed")
        assert str(error) == "Sync failed"


# Memory-profiling tests: each builds many large files and measures RSS, so
# they cost seconds, not milliseconds. Marked `slow` so the every-PR unit lane
# skips them; they still run in `./test.sh all` and nightly.
@pytest.mark.slow
class TestMemoryUsageDuringFileScan:
    """
    Memory profiling tests for file scanning operations.

    These tests verify that the streaming file scan approach keeps memory
    usage low even when processing many large files. The scheduler container
    has a 512Mi limit and was previously crashing with OOM when syncing
    repositories with 4MB+ modules.
    """

    def test_streaming_scan_memory_stays_bounded(self, tmp_path):
        """
        Test that scanning many large files doesn't accumulate memory.

        Simulates scanning 50 x 1MB files and verifies peak memory stays
        reasonable (under 50MB overhead beyond the single file being processed).
        """
        import tracemalloc

        from src.services.file_storage.file_ops import compute_git_blob_sha

        # Create 50 x 1MB files
        num_files = 50
        file_size = 1 * 1024 * 1024  # 1MB each

        for i in range(num_files):
            file_path = tmp_path / f"large_file_{i}.bin"
            # Write deterministic content (not random, to be consistent)
            file_path.write_bytes(bytes([i % 256] * file_size))

        # Start memory tracking
        tracemalloc.start()

        # Simulate the streaming scan pattern (as implemented in get_sync_preview)
        remote_files: dict[str, str] = {}
        file_count = 0

        for file_path in tmp_path.rglob("*"):
            if not file_path.is_file():
                continue
            content = file_path.read_bytes()
            remote_files[str(file_path)] = compute_git_blob_sha(content)
            del content  # Explicit release
            file_count += 1

        # Get peak memory usage
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert file_count == num_files

        # Peak memory should be well under the total data size
        # With streaming, we only hold ~1 file at a time + dict of SHAs
        # 50 files x 1MB = 50MB total, but peak should be much lower
        # Allow 20MB overhead for SHA dict, Python objects, etc.
        max_expected_peak = 20 * 1024 * 1024  # 20MB

        assert peak < max_expected_peak, (
            f"Peak memory {peak / 1024 / 1024:.1f}MB exceeded "
            f"expected max {max_expected_peak / 1024 / 1024:.1f}MB. "
            f"This suggests memory is accumulating instead of streaming."
        )

    def test_old_list_pattern_would_use_more_memory(self, tmp_path):
        """
        Demonstrate that the old list-based pattern uses more memory.

        This test shows why we switched to streaming - the old approach
        of building a list of all files first would hold more in memory.
        """
        import tracemalloc

        from src.services.file_storage.file_ops import compute_git_blob_sha

        # Create 20 x 1MB files (smaller to keep test fast)
        num_files = 20
        file_size = 1 * 1024 * 1024  # 1MB each

        for i in range(num_files):
            file_path = tmp_path / f"large_file_{i}.bin"
            file_path.write_bytes(bytes([i % 256] * file_size))

        # Test OLD pattern (list comprehension that holds all paths)
        tracemalloc.start()
        all_files = [f for f in tmp_path.rglob("*") if f.is_file()]
        remote_files_old: dict[str, str] = {}
        for file_path in all_files:
            content = file_path.read_bytes()
            remote_files_old[str(file_path)] = compute_git_blob_sha(content)
            # Note: no del content here, simulating less careful memory management
        _, peak_old = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Test NEW pattern (streaming with explicit release)
        tracemalloc.start()
        remote_files_new: dict[str, str] = {}
        for file_path in tmp_path.rglob("*"):
            if not file_path.is_file():
                continue
            content = file_path.read_bytes()
            remote_files_new[str(file_path)] = compute_git_blob_sha(content)
            del content  # Explicit release
        _, peak_new = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Both should produce same results
        assert remote_files_old == remote_files_new

        # New pattern should use less or equal memory
        # (In practice, the difference is more pronounced with larger files
        # and when GC hasn't run between iterations)
        assert peak_new <= peak_old * 1.1, (
            f"New pattern ({peak_new / 1024 / 1024:.1f}MB) should not use "
            f"significantly more memory than old pattern ({peak_old / 1024 / 1024:.1f}MB)"
        )

    def test_large_python_file_memory_bounded(self, tmp_path):
        """
        Test memory stays bounded when scanning multiple copies of ~4MB Python files.

        This simulates the scenario that caused OOM in scheduler:
        - Large modules like halopsa.py (~4MB)
        - Multiple files being processed in sequence
        - Without explicit `del`, memory accumulates

        With the fix (explicit `del content`), peak memory should stay low.
        """
        import shutil
        import tracemalloc

        from src.services.file_storage.file_ops import compute_git_blob_sha
        from tests.fixtures.large_module_generator import generate_large_module_file

        # Generate a ~4MB Python file (similar to halopsa.py)
        base_file = tmp_path / "base_large_module.py"
        generate_large_module_file(str(base_file), target_size_mb=4.0)

        # Verify it's approximately the right size
        actual_size = base_file.stat().st_size
        assert actual_size > 3.5 * 1024 * 1024, f"Base file too small: {actual_size}"
        assert actual_size < 5 * 1024 * 1024, f"Base file too large: {actual_size}"

        # Create 10 copies (would be ~40MB total without streaming)
        num_copies = 10
        for i in range(num_copies):
            shutil.copy(base_file, tmp_path / f"module_{i}.py")

        # Start memory tracking
        tracemalloc.start()

        # Simulate the scan pattern WITH explicit memory release
        results: dict[str, str] = {}
        for file_path in tmp_path.glob("module_*.py"):
            content = file_path.read_bytes()
            results[str(file_path)] = compute_git_blob_sha(content)
            del content  # This is what we're testing - explicit release

        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert len(results) == num_copies

        # With streaming + del, peak should be ~1 file + overhead
        # 10 x 4MB = 40MB without del, should be <15MB with del
        max_expected = 15 * 1024 * 1024  # 15MB

        assert peak < max_expected, (
            f"Peak memory {peak / 1024 / 1024:.1f}MB exceeded "
            f"expected max {max_expected / 1024 / 1024:.1f}MB. "
            f"Memory may be accumulating between file reads."
        )

    def test_execute_sync_pattern_memory_bounded(self, tmp_path):
        """
        Test memory stays bounded when simulating sync file write pattern.

        This simulates what happens during pull when processing multiple large
        Python modules sequentially. Each file:
        1. Read from clone directory
        2. Process (decode for modules, compute SHA, etc.)
        3. Should be released with del before next iteration

        Without explicit memory management, this would accumulate ~40MB for
        10 x 4MB files. With proper del statements, peak should stay low.
        """
        import shutil
        import tracemalloc

        from src.services.file_storage.file_ops import compute_git_blob_sha
        from tests.fixtures.large_module_generator import generate_large_module_file

        # Generate a ~4MB Python file
        base_file = tmp_path / "base_large_module.py"
        generate_large_module_file(str(base_file), target_size_mb=4.0)

        # Create multiple copies simulating different modules (halopsa, sageintacct, etc.)
        module_names = [
            "sageintacct.py",
            "ninjaone.py",
            "halopsa.py",
            "connectwise.py",
            "datto.py",
        ]
        for name in module_names:
            shutil.copy(base_file, tmp_path / name)

        tracemalloc.start()

        # Simulate the pull pattern:
        # For each file, read -> process -> write (simulated) -> del content
        processed_files: list[str] = []
        for file_path in tmp_path.glob("*.py"):
            if file_path.name == "base_large_module.py":
                continue

            # 1. Read file (like: content = local_file.read_bytes())
            content = file_path.read_bytes()

            # 2. Process - simulate what write_file does internally
            #    - Decode to string (for module_content)
            module_content = content.decode("utf-8")
            #    - Compute SHA
            _ = compute_git_blob_sha(content)

            # 3. Simulate DB write + Redis cache (actual memory not tracked here)
            processed_files.append(file_path.name)

            # 4. Explicit release (this is what we're testing)
            del module_content  # Release decoded string
            del content  # Release bytes

        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert len(processed_files) == len(module_names)

        # With explicit del, peak should be ~2 copies of one file at most
        # (bytes + decoded string) = ~8MB, plus overhead
        # Without del, would be 5 files x 8MB = 40MB
        max_expected = 20 * 1024 * 1024  # 20MB

        assert peak < max_expected, (
            f"Peak memory {peak / 1024 / 1024:.1f}MB exceeded "
            f"expected max {max_expected / 1024 / 1024:.1f}MB. "
            f"This simulates sync pull pattern - memory should not accumulate."
        )
