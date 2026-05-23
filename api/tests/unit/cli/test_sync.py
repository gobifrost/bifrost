"""Tests for bifrost git commands (formerly sync)."""
from bifrost.git_commands import _format_sync_result, RESOLUTION_MAP


class TestFormatSyncResult:
    """Test sync result output formatting."""

    def test_success_no_changes(self):
        """Should report no changes on success with zero counts."""
        result = {"status": "success", "pulled": 0, "pushed": 0, "commit_sha": None}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "no changes" in text.lower()

    def test_success_with_pulled_and_pushed(self):
        """Should summarize pull/push counts on success."""
        result = {
            "status": "success",
            "pulled": 3,
            "pushed": 1,
            "commit_sha": "abc1234def5678",
        }
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "pulled 3" in text
        assert "pushed 1" in text
        assert "abc1234" in text

    def test_success_completed_status(self):
        """Should also accept 'completed' as a success status."""
        result = {"status": "completed", "pulled": 1, "pushed": 0, "commit_sha": None}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "Push complete" in text

    def test_conflicts_shown(self):
        """Should list each conflict with path and resolve command."""
        result = {
            "status": "conflict",
            "conflicts": [
                {
                    "path": "workflows/billing.py",
                    "display_name": "billing",
                    "entity_type": "workflow",
                },
            ],
        }
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "workflows/billing.py" in text
        assert "bifrost git resolve" in text
        assert "keep_remote" in text
        assert "keep_local" in text

    def test_conflict_resolution_commands_quote_paths(self):
        """Suggested resolve commands must be safe to copy into a shell."""
        result = {
            "status": "conflict",
            "conflicts": [
                {
                    "path": "workflows/billing.py; echo owned",
                    "display_name": "billing",
                    "entity_type": "workflow",
                },
            ],
        }

        lines = _format_sync_result(result)
        text = "\n".join(lines)

        assert "bifrost git resolve 'workflows/billing.py; echo owned=keep_remote'" in text
        assert "bifrost git resolve workflows/billing.py; echo owned=keep_remote" not in text

    def test_multiple_conflicts(self):
        """Should list all conflicts."""
        result = {
            "status": "conflict",
            "conflicts": [
                {"path": "workflows/a.py", "display_name": "a", "entity_type": "workflow"},
                {"path": "workflows/b.py", "display_name": "b", "entity_type": "workflow"},
            ],
        }
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "2 conflicts" in text
        assert "workflows/a.py" in text
        assert "workflows/b.py" in text

    def test_failed_with_error(self):
        """Should show error message on failure."""
        result = {"status": "failed", "error": "Authentication failed"}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "Authentication failed" in text
        assert "failed" in text.lower()

    def test_failed_unknown_error(self):
        """Should show fallback message when no error provided."""
        result = {"status": "failed"}
        lines = _format_sync_result(result)
        text = "\n".join(lines)
        assert "Unknown error" in text


class TestResolutionMap:
    """Test CLI-to-API resolution mapping."""

    def test_keep_local_maps_to_ours(self):
        assert RESOLUTION_MAP["keep_local"] == "ours"

    def test_keep_remote_maps_to_theirs(self):
        assert RESOLUTION_MAP["keep_remote"] == "theirs"
