"""
Unit tests for Code Editor MCP Tools.

Tests the precision editing tools:
- list_content: List files, optionally filtered by path prefix
- search_content: Regex search with context
- read_content_lines: Line range reading
- get_content: Full content read
- patch_content: Surgical edits
- replace_content: Full content write
- delete_content: Delete files

All files are accessed via their path in file_index / S3 _repo/ store.
No entity_type or app_id parameters -- everything is path-based.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from src.services.mcp_server.server import MCPContext


def get_result_data(result: ToolResult) -> dict:
    """Extract structured data from a ToolResult."""
    return result.structured_content or {}


def get_result_text(result: ToolResult) -> str:
    """Extract display text from a ToolResult."""
    if not result.content:
        return ""
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    return "\n".join(texts)


def is_error_result(result: ToolResult) -> bool:
    """Check if a ToolResult represents an error."""
    if result.structured_content and "error" in result.structured_content:
        return True
    if result.content and isinstance(result.content, str) and result.content.startswith("Error:"):
        return True
    return False


@pytest.fixture
def platform_admin_context() -> MCPContext:
    """Create an MCPContext for a platform admin user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=None,
        is_platform_admin=True,
        user_email="admin@platform.local",
        user_name="Platform Admin",
    )


@pytest.fixture
def org_user_context() -> MCPContext:
    """Create an MCPContext for a regular org user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_admin=False,
        user_email="user@org.local",
        user_name="Org User",
    )


class TestListContent:
    """Tests for the list_content MCP tool."""

    @pytest.mark.asyncio
    async def test_list_workflows(self, platform_admin_context):
        """Should list workflow paths using path_prefix."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "workflows/sync_tickets.py",
                "workflows/sync_users.py",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
                path_prefix="workflows/",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert "files" in data
            assert len(data["files"]) == 2
            assert data["files"][0]["path"] == "workflows/sync_tickets.py"
            assert data["files"][1]["path"] == "workflows/sync_users.py"
            mock_repo.list.assert_called_once_with("workflows/")

    @pytest.mark.asyncio
    async def test_list_modules(self, platform_admin_context):
        """Should list module paths using path_prefix."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "modules/helpers.py",
                "modules/utils.py",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
                path_prefix="modules/",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert "files" in data
            assert len(data["files"]) == 2
            assert data["files"][0]["path"] == "modules/helpers.py"
            assert data["files"][1]["path"] == "modules/utils.py"

    @pytest.mark.asyncio
    async def test_list_app_files(self, platform_admin_context):
        """Should list app files using path_prefix with app slug."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "apps/test-app/components/Header.tsx",
                "apps/test-app/pages/index.tsx",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
                path_prefix="apps/test-app/",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert "files" in data
            assert len(data["files"]) == 2
            assert data["files"][0]["path"] == "apps/test-app/components/Header.tsx"
            assert data["files"][1]["path"] == "apps/test-app/pages/index.tsx"

    @pytest.mark.asyncio
    async def test_list_with_path_prefix(self, platform_admin_context):
        """Should filter by path_prefix when provided."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "workflows/sync_tickets.py",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
                path_prefix="workflows/",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_list_all_files(self, platform_admin_context):
        """Should list all files when no path_prefix given."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "apps/my-app/pages/index.tsx",
                "modules/helpers.py",
                "workflows/sync.py",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["count"] == 3
            mock_repo.list.assert_called_once_with("")

    @pytest.mark.asyncio
    async def test_list_content_denies_non_admin(self, org_user_context):
        """Path-based code editor tools are platform-admin only."""
        from src.services.mcp_server.tools.code_editor import list_content

        result = await list_content(
            context=org_user_context,
            path_prefix="workflows/",
        )

        assert is_error_result(result)
        data = get_result_data(result)
        assert "Platform administrator privileges are required" in data["error"]


class TestSearchContent:
    """Tests for the search_content MCP tool."""

    @pytest.mark.asyncio
    async def test_search_workflow_content(self, platform_admin_context):
        """Should find matches in file content with context."""
        from src.services.mcp_server.tools.code_editor import search_content

        code = '''from bifrost import workflow

@workflow(name="Sync Tickets")
async def sync_tickets(client_id: str) -> dict:
    """Sync tickets from HaloPSA."""
    return {"synced": True}
'''

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            # search_content does: select(FileIndex.path, FileIndex.content) -> result.all()
            mock_fi_result = MagicMock()
            mock_fi_row = MagicMock()
            mock_fi_row.path = "workflows/sync_tickets.py"
            mock_fi_row.content = code
            mock_fi_result.all.return_value = [mock_fi_row]
            mock_session.execute.return_value = mock_fi_result

            result = await search_content(
                context=platform_admin_context,
                pattern="async def",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert "matches" in data
            assert len(data["matches"]) == 1
            assert data["matches"][0]["line_number"] == 4
            assert "sync_tickets" in data["matches"][0]["match"]

    @pytest.mark.asyncio
    async def test_search_invalid_regex(self, platform_admin_context):
        """Should return error for invalid regex pattern."""
        from src.services.mcp_server.tools.code_editor import search_content

        result = await search_content(
            context=platform_admin_context,
            pattern="[invalid",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "Invalid regex" in data["error"]

    @pytest.mark.asyncio
    async def test_search_empty_pattern(self, platform_admin_context):
        """Should return error for empty pattern."""
        from src.services.mcp_server.tools.code_editor import search_content

        result = await search_content(
            context=platform_admin_context,
            pattern="",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data


class TestReadContentLines:
    """Tests for the read_content_lines MCP tool."""

    @pytest.mark.asyncio
    async def test_read_line_range(self, platform_admin_context):
        """Should read specific line range from a file."""
        from src.services.mcp_server.tools.code_editor import read_content_lines

        code = "line 1\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\nline 8\nline 9\nline 10"

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            result = await read_content_lines(
                context=platform_admin_context,
                path="workflows/sync.py",
                start_line=3,
                end_line=6,
            )

        assert isinstance(result, ToolResult)
        data = get_result_data(result)
        assert data["start_line"] == 3
        assert data["end_line"] == 6
        assert data["total_lines"] == 10
        assert "3: line 3" in data["content"]
        assert "6: line 6" in data["content"]
        assert "line 2" not in data["content"]

    @pytest.mark.asyncio
    async def test_read_requires_path(self, platform_admin_context):
        """Should return error if path not provided."""
        from src.services.mcp_server.tools.code_editor import read_content_lines

        result = await read_content_lines(
            context=platform_admin_context,
            path="",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "path" in data["error"]


class TestGetContent:
    """Tests for the get_content MCP tool."""

    @pytest.mark.asyncio
    async def test_get_full_content(self, platform_admin_context):
        """Should return full file content with metadata."""
        from src.services.mcp_server.tools.code_editor import get_content

        code = "line 1\nline 2\nline 3"

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            result = await get_content(
                context=platform_admin_context,
                path="workflows/sync.py",
            )

        assert isinstance(result, ToolResult)
        data = get_result_data(result)
        assert data["path"] == "workflows/sync.py"
        assert data["total_lines"] == 3
        assert "line 1" in data["content"]
        assert "line 3" in data["content"]

    @pytest.mark.asyncio
    async def test_get_content_not_found(self, platform_admin_context):
        """Should return error if file not found."""
        from src.services.mcp_server.tools.code_editor import get_content

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await get_content(
                context=platform_admin_context,
                path="workflows/nonexistent.py",
            )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "not found" in data["error"].lower()


class TestPatchContent:
    """Tests for the patch_content MCP tool."""

    @pytest.mark.asyncio
    async def test_patch_unique_string(self, platform_admin_context):
        """Should replace unique string successfully."""
        from src.services.mcp_server.tools.code_editor import patch_content

        code = '''async def sync_tickets():
    return {"status": "old"}
'''

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            with patch(
                "src.services.mcp_server.tools.code_editor._replace_workspace_file",
                new_callable=AsyncMock,
            ) as mock_write:
                from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult
                mock_write.return_value = WorkspaceWriteResult(created=False)

                result = await patch_content(
                    context=platform_admin_context,
                    path="workflows/test.py",
                    old_string='return {"status": "old"}',
                    new_string='return {"status": "new"}',
                )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["success"] is True

    @pytest.mark.asyncio
    async def test_patch_non_unique_string_fails(self, platform_admin_context):
        """Should fail when old_string matches multiple locations."""
        from src.services.mcp_server.tools.code_editor import patch_content

        code = '''def func1():
    return "duplicate"

def func2():
    return "duplicate"
'''

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            result = await patch_content(
                context=platform_admin_context,
                path="workflows/sync.py",
                old_string='return "duplicate"',
                new_string='return "new_value"',
            )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "match_locations" in data or "matches" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_patch_string_not_found(self, platform_admin_context):
        """Should fail when old_string not found."""
        from src.services.mcp_server.tools.code_editor import patch_content

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value="some code here",
        ):
            result = await patch_content(
                context=platform_admin_context,
                path="workflows/sync.py",
                old_string="nonexistent string",
                new_string="replacement",
            )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "not found" in data["error"]

    @pytest.mark.asyncio
    async def test_patch_requires_old_string(self, platform_admin_context):
        """Should return error if old_string not provided."""
        from src.services.mcp_server.tools.code_editor import patch_content

        result = await patch_content(
            context=platform_admin_context,
            path="workflows/sync.py",
            old_string="",
            new_string="replacement",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "old_string" in data["error"]


class TestReplaceContent:
    """Tests for the replace_content MCP tool."""

    @pytest.mark.asyncio
    async def test_replace_existing_workflow(self, platform_admin_context):
        """Should replace entire file content."""
        from src.services.mcp_server.tools.code_editor import replace_content

        with patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
        ) as mock_write:
            from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult
            mock_write.return_value = WorkspaceWriteResult(created=False)

            result = await replace_content(
                context=platform_admin_context,
                path="workflows/test.py",
                content='''from bifrost import workflow

@workflow(name="Sync")
async def sync():
    return {"done": True}
''',
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["success"] is True
            assert data["path"] == "workflows/test.py"
            assert data["created"] is False

    @pytest.mark.asyncio
    async def test_replace_requires_content(self, platform_admin_context):
        """Should return error if content not provided."""
        from src.services.mcp_server.tools.code_editor import replace_content

        result = await replace_content(
            context=platform_admin_context,
            path="workflows/sync.py",
            content="",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "content" in data["error"]

    @pytest.mark.asyncio
    async def test_replace_requires_path(self, platform_admin_context):
        """Should return error if path not provided."""
        from src.services.mcp_server.tools.code_editor import replace_content

        result = await replace_content(
            context=platform_admin_context,
            path="",
            content="some content",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "path" in data["error"]

    @pytest.mark.asyncio
    async def test_replace_workflow_missing_decorator(self, platform_admin_context):
        """Should succeed when writing to workflow path without decorator (path-based, no validation)."""
        from src.services.mcp_server.tools.code_editor import replace_content

        with patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
        ) as mock_write:
            from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult
            mock_write.return_value = WorkspaceWriteResult(created=True)

            result = await replace_content(
                context=platform_admin_context,
                path="workflows/sync.py",
                content='''def regular_function():
    return {"done": True}
''',
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            # Path-based system no longer validates entity_type vs content
            assert data["success"] is True

    @pytest.mark.asyncio
    async def test_replace_app_file_creates_new(self, platform_admin_context):
        """Should create new app file using full path."""
        from src.services.mcp_server.tools.code_editor import replace_content

        with patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
        ) as mock_write:
            from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult
            mock_write.return_value = WorkspaceWriteResult(created=True)

            result = await replace_content(
                context=platform_admin_context,
                path="apps/test-app/pages/new.tsx",
                content="export default function NewComponent() { return <div>New</div>; }",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["success"] is True
            assert data["created"] is True
            assert data["path"] == "apps/test-app/pages/new.tsx"


class TestDeleteContent:
    """Tests for the delete_content MCP tool."""

    @pytest.mark.asyncio
    async def test_delete_workflow(self, platform_admin_context):
        """Should delete a workflow file via FileStorageService."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                with patch(
                    "src.services.mcp_server.tools.code_editor.FileStorageService"
                ) as mock_fs_cls:
                    mock_fs_instance = MagicMock()
                    mock_fs_instance.delete_file = AsyncMock()
                    mock_fs_cls.return_value = mock_fs_instance

                    result = await delete_content(
                        context=platform_admin_context,
                        path="workflows/test.py",
                    )

                    assert isinstance(result, ToolResult)
                    data = get_result_data(result)
                    assert data["success"] is True
                    assert data["path"] == "workflows/test.py"
                    mock_fs_instance.delete_file.assert_called_once_with("workflows/test.py")

    @pytest.mark.asyncio
    async def test_delete_module(self, platform_admin_context):
        """Should delete a module file via FileStorageService."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                with patch(
                    "src.services.mcp_server.tools.code_editor.FileStorageService"
                ) as mock_fs_cls:
                    mock_fs_instance = MagicMock()
                    mock_fs_instance.delete_file = AsyncMock()
                    mock_fs_cls.return_value = mock_fs_instance

                    result = await delete_content(
                        context=platform_admin_context,
                        path="modules/test.py",
                    )

                    assert isinstance(result, ToolResult)
                    data = get_result_data(result)
                    assert data["success"] is True
                    assert data["path"] == "modules/test.py"
                    mock_fs_instance.delete_file.assert_called_once_with("modules/test.py")

    @pytest.mark.asyncio
    async def test_delete_app_file(self, platform_admin_context):
        """Should delete an app file using full path."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                with patch(
                    "src.services.mcp_server.tools.code_editor.FileStorageService"
                ) as mock_fs_cls:
                    mock_fs_instance = MagicMock()
                    mock_fs_instance.delete_file = AsyncMock()
                    mock_fs_cls.return_value = mock_fs_instance

                    result = await delete_content(
                        context=platform_admin_context,
                        path="apps/test-app/pages/index.tsx",
                    )

                    assert isinstance(result, ToolResult)
                    data = get_result_data(result)
                    assert data["success"] is True
                    assert data["path"] == "apps/test-app/pages/index.tsx"
                    mock_fs_instance.delete_file.assert_called_once_with("apps/test-app/pages/index.tsx")

    @pytest.mark.asyncio
    async def test_delete_not_found(self, platform_admin_context):
        """Should return error if file not found."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=False)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                result = await delete_content(
                    context=platform_admin_context,
                    path="workflows/nonexistent.py",
                )

            assert isinstance(result, ToolResult)
            assert is_error_result(result)
            data = get_result_data(result)
            assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_delete_requires_path(self, platform_admin_context):
        """Should return error if path not provided."""
        from src.services.mcp_server.tools.code_editor import delete_content

        result = await delete_content(
            context=platform_admin_context,
            path="",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "path" in data["error"]

    @pytest.mark.asyncio
    async def test_delete_content_denies_non_admin(self, org_user_context):
        """Non-admin users must not delete global workspace paths."""
        from src.services.mcp_server.tools.code_editor import delete_content

        result = await delete_content(
            context=org_user_context,
            path="workflows/test.py",
        )

        assert is_error_result(result)
        data = get_result_data(result)
        assert "Platform administrator privileges are required" in data["error"]


class TestCodeEditorAuthorization:
    """All path-based code editor MCP tools are platform-admin only."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_name", "args"),
        [
            ("list_content", ()),
            ("search_content", ("needle",)),
            ("read_content_lines", ("workflows/test.py",)),
            ("get_content", ("workflows/test.py",)),
            ("patch_content", ("workflows/test.py", "old", "new")),
            ("replace_content", ("workflows/test.py", "content")),
            ("delete_content", ("workflows/test.py",)),
        ],
    )
    async def test_non_admin_code_editor_tools_return_error(
        self,
        org_user_context,
        tool_name,
        args,
    ):
        import src.services.mcp_server.tools.code_editor as code_editor

        result = await getattr(code_editor, tool_name)(org_user_context, *args)

        assert is_error_result(result)
        data = get_result_data(result)
        assert "Platform administrator privileges are required" in data["error"]


class TestMultiFunctionWorkflows:
    """Tests for multi-function workflow file handling."""

    @pytest.mark.asyncio
    async def test_get_content_multi_function_file(self, platform_admin_context):
        """Should return content when reading a multi-function file."""
        from src.services.mcp_server.tools.code_editor import get_content

        code = '''from bifrost import workflow, tool

@workflow(name="Sync Tickets")
async def sync_tickets():
    return {"synced": True}

@tool(name="Get Ticket")
async def get_ticket(ticket_id: str):
    return {"id": ticket_id}
'''

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            result = await get_content(
                context=platform_admin_context,
                path="workflows/multi.py",
            )

        assert isinstance(result, ToolResult)
        assert not is_error_result(result)
        data = get_result_data(result)
        assert data["path"] == "workflows/multi.py"
        assert "sync_tickets" in data["content"]
        assert "get_ticket" in data["content"]

    @pytest.mark.asyncio
    async def test_delete_multi_function_file(self, platform_admin_context):
        """Should delete a multi-function file via FileStorageService."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                with patch(
                    "src.services.mcp_server.tools.code_editor.FileStorageService"
                ) as mock_fs_cls:
                    mock_fs_instance = MagicMock()
                    mock_fs_instance.delete_file = AsyncMock()
                    mock_fs_cls.return_value = mock_fs_instance

                    result = await delete_content(
                        context=platform_admin_context,
                        path="workflows/multi.py",
                    )

                    assert isinstance(result, ToolResult)
                    data = get_result_data(result)
                    assert data["success"] is True
                    assert data["path"] == "workflows/multi.py"
                    mock_fs_instance.delete_file.assert_called_once_with("workflows/multi.py")

    @pytest.mark.asyncio
    async def test_search_deduplicates_multi_function_results(self, platform_admin_context):
        """Should not produce duplicate search results from multi-function files."""
        from src.services.mcp_server.tools.code_editor import search_content

        code = '''from bifrost import workflow

@workflow(name="Sync")
async def sync():
    return {"done": True}

@workflow(name="Cleanup")
async def cleanup():
    return {"done": True}
'''

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            # search_content queries FileIndex directly, one row per file
            mock_fi_result = MagicMock()
            mock_fi_row = MagicMock()
            mock_fi_row.path = "workflows/multi.py"
            mock_fi_row.content = code
            mock_fi_result.all.return_value = [mock_fi_row]
            mock_session.execute.return_value = mock_fi_result

            result = await search_content(
                context=platform_admin_context,
                pattern="return",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            # Should have exactly 2 matches (one per "return" line), NOT 4
            assert data["total_matches"] == 2


class TestFormatDeactivationResult:
    """Tests for _format_deactivation_result."""

    def _get_text(self, result: ToolResult) -> str:
        """Extract text from ToolResult content (handles TextContent list)."""
        content = result.content
        if isinstance(content, str):
            return content
        if isinstance(content, list) and len(content) > 0:
            return content[0].text
        return ""

    def test_format_without_schedule_key(self):
        """Should not crash when pending deactivation dict lacks 'schedule' key."""
        from src.services.mcp_server.tools.code_editor import _format_deactivation_result

        pending = [
            {
                "function_name": "sync_tickets",
                "decorator_type": "workflow",
                "has_executions": False,
                "last_execution_at": None,
                "endpoint_enabled": False,
                "affected_entities": [],
            }
        ]

        result = _format_deactivation_result(
            path="workflows/sync.py",
            pending_deactivations=pending,
            available_replacements=None,
        )

        assert isinstance(result, ToolResult)
        text = self._get_text(result)
        assert "sync_tickets" in text
        assert "workflow" in text

    def test_format_with_affected_entities(self):
        """Should list affected entities in deactivation result."""
        from src.services.mcp_server.tools.code_editor import _format_deactivation_result

        pending = [
            {
                "function_name": "sync_tickets",
                "decorator_type": "workflow",
                "has_executions": True,
                "last_execution_at": "2026-01-15T12:00:00Z",
                "endpoint_enabled": True,
                "affected_entities": [
                    {
                        "entity_type": "form",
                        "name": "Ticket Sync Form",
                        "reference_type": "workflow_id",
                    }
                ],
            }
        ]

        result = _format_deactivation_result(
            path="workflows/sync.py",
            pending_deactivations=pending,
            available_replacements=None,
        )

        text = self._get_text(result)
        assert "execution history" in text
        assert "API endpoint" in text
        assert "Ticket Sync Form" in text
