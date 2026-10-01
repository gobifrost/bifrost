"""
Contract tests for editor API endpoints

Tests file operations, search, and editor-related models.
"""

import pytest
from pydantic import ValidationError
from src.models import (
    FileMetadata,
    FileType,
    FileContentRequest,
    FileContentResponse,
    SearchFileHit,
    SearchMatch,
    SearchRequest,
    SearchResponse,
    SearchSource,
)


class TestEditorModelContracts:
    """Contract tests for editor API models"""

    def test_file_metadata_model_structure(self):
        """Test that FileMetadata model has required fields"""
        file = FileMetadata(
            path="workflows/test.py",
            name="test.py",
            type=FileType.FILE,
            size=1024,
            extension=".py",
            modified="2025-10-23T14:30:00Z",
        )

        assert file.path == "workflows/test.py"
        assert file.name == "test.py"
        assert file.type == FileType.FILE
        assert file.size == 1024
        assert file.extension == ".py"
        assert file.modified == "2025-10-23T14:30:00Z"

    def test_file_metadata_folder_type(self):
        """Test FileMetadata for folder type"""
        folder = FileMetadata(
            path="workflows",
            name="workflows",
            type=FileType.FOLDER,
            size=None,
            extension=None,
            modified="2025-10-23T14:30:00Z",
        )

        assert folder.type == FileType.FOLDER
        assert folder.size is None
        assert folder.extension is None

    def test_file_content_request_model(self):
        """Test FileContentRequest model structure"""
        request = FileContentRequest(
            path="workflows/test.py",
            content="import bifrost\n\ndef run(context):\n    pass",
            encoding="utf-8",
        )

        assert request.path == "workflows/test.py"
        assert "import bifrost" in request.content
        assert request.encoding == "utf-8"

    def test_file_content_request_default_encoding(self):
        """Test FileContentRequest uses utf-8 by default"""
        request = FileContentRequest(
            path="workflows/test.py", content="test content"
        )

        assert request.encoding == "utf-8"

    def test_file_content_response_model(self):
        """Test FileContentResponse model structure"""
        response = FileContentResponse(
            path="workflows/test.py",
            content="import bifrost",
            encoding="utf-8",
            size=14,
            etag="abc123",
            modified="2025-10-23T14:30:00Z",
        )

        assert response.path == "workflows/test.py"
        assert response.content == "import bifrost"
        assert response.encoding == "utf-8"
        assert response.size == 14
        assert response.etag == "abc123"
        assert response.modified == "2025-10-23T14:30:00Z"

    def test_search_request_defaults(self):
        """A bare query searches everything, literally, one small page at a time."""
        request = SearchRequest(query="test")

        assert (request.is_regex, request.case_sensitive, request.include_pattern) == (False, False, None)
        assert (request.source, request.solution_id, request.output_mode) == ("all", None, "content")
        assert (request.limit, request.context_lines, request.cursor) == (25, 1, None)

    def test_search_request_validates_query_not_empty(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="")

    @pytest.mark.parametrize("field,value", [("limit", 0), ("limit", 201), ("context_lines", 6), ("source", "nope")])
    def test_search_request_rejects_out_of_range(self, field, value):
        with pytest.raises(ValidationError):
            SearchRequest(query="test", **{field: value})

    def test_search_match_and_source(self):
        match = SearchMatch(
            file_path="functions/sync.py",
            source=SearchSource(kind="solution", solution_slug="covi-psa", editable=False),
            line=42,
            column=4,
            text="def run():",
            context_before=["# Sync"],
        )
        assert (match.source.kind, match.source.editable, match.context_after) == ("solution", False, [])
        with pytest.raises(ValidationError):
            SearchMatch(file_path="a.py", source=match.source, line=0, column=0, text="")

    def test_search_response_page(self):
        response = SearchResponse(
            query="def run",
            output_mode="files",
            matches=[],
            files=[SearchFileHit(
                file_path="a.py", source=SearchSource(kind="workspace", editable=True),
                match_count=3, first_line=7,
            )],
            returned=1,
            has_more_matches=True,
            response_complete=False,
            next_cursor="abc",
            guidance="Showing files 1-1; more results exist.",
            search_time_ms=5,
        )
        assert (response.matches, response.files[0].match_count, response.next_cursor) == ([], 3, "abc")

    def test_file_type_enum_values(self):
        """Test FileType enum has correct values"""
        assert FileType.FILE == "file"
        assert FileType.FOLDER == "folder"

        # Enum should only have these two values
        assert len(list(FileType)) == 2
