"""
File management SDK for Bifrost.

Provides async Python API for file operations with two storage modes:
- local: Local filesystem (CWD, /tmp/bifrost/temp, /tmp/bifrost/uploads)
- cloud: S3 storage (default)

Location options:
- "workspace": Persistent workspace files (CWD in local mode, _repo/ in cloud mode)
- "temp": Temporary files (_tmp/ prefix in cloud, /tmp/bifrost/temp in local)
- "uploads": Files uploaded via form file fields (uploads/ prefix in cloud, /tmp/bifrost/uploads in local)
- Custom names like "reports" or "exports": scoped user-defined storage locations

Internal bucket prefixes "_repo", "_tmp", and "_apps" are blocked as
custom location names.

Usage:
    from bifrost import files

    # Write to workspace (cloud mode by default)
    await files.write("exports/report.csv", data)

    # Write to workspace (local mode)
    await files.write("exports/report.csv", data, mode="local")

    # Read from temp location
    content = await files.read("temp-data.txt", location="temp")

    # Read uploaded file
    content = await files.read("form_id/uuid/filename.txt", location="uploads")
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

from .client import get_client, raise_for_status_with_detail
from ._context import resolve_scope, get_caller_solution, get_effective_solution

Mode = Literal["local", "cloud"]
# `location` is a free string. Special names: "workspace", "temp", "uploads".
# Anything else is a freeform user-defined location (e.g. "reports", "exports"),
# except blocked internal prefixes such as "_repo", "_tmp", and "_apps".


def _current_context():
    from ._context import _execution_context

    return _execution_context.get()


def _solution_query(solution: str | None = None) -> str:
    params: dict[str, str] = {}
    solution_id = get_effective_solution(solution)
    if solution_id:
        params["solution"] = str(solution_id)
    caller = get_caller_solution()
    if caller:
        params["caller_solution"] = str(caller)
    return f"?{urlencode(params)}" if params else ""


class files:
    """
    File management operations (async).

    Provides safe file access with two storage modes:
    - local: Local filesystem (for CLI usage)
    - cloud: S3 storage (for platform execution, default)

    Every operation sends the ordinary HTTP request through the shared
    ``BifrostClient``: over the worker's private Unix socket when the
    engine injected one, and over the network API otherwise. The worker
    parent owns the pooled database and protected storage credentials;
    an engine child holds neither, and a local attempt never falls back to
    the network API after a failure.

    All operations are performed via HTTP API endpoints.
    """

    @staticmethod
    async def read(
        path: str,
        location: str = "workspace",
        mode: Mode = "cloud",
        scope: str | None = None,
        solution: str | None = None,
    ) -> str:
        """
        Read a text file.

        Args:
            path: File path relative to location root
            location: Storage location. Special: "workspace", "temp", "uploads".
                Freeform names (e.g. "reports") are also allowed; internal
                prefixes "_repo", "_tmp", and "_apps" are blocked.
            mode: Storage mode (local or cloud, default: cloud)
            scope: Org scope. Defaults to the current execution's org.
                Provider orgs may pass an explicit scope to read from another org.

        Example:
            >>> from bifrost import files
            >>> content = await files.read("data/customers.csv")
            >>> uploaded = await files.read("form_id/uuid/file.txt", location="uploads")
        """
        effective_scope = resolve_scope(scope)
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/files/read{_solution_query(solution)}",
            json={"path": path, "location": location, "mode": mode, "binary": False, "scope": effective_scope},
        )
        raise_for_status_with_detail(response)
        return response.json()["content"]

    @staticmethod
    async def read_bytes(
        path: str,
        location: str = "workspace",
        mode: Mode = "cloud",
        scope: str | None = None,
    ) -> bytes:
        """
        Read a binary file.

        Args:
            path: File path relative to location root
            location: Storage location (special or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            scope: Org scope; provider-org override allowed.
        """
        effective_scope = resolve_scope(scope)
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/files/read{_solution_query()}",
            json={"path": path, "location": location, "mode": mode, "binary": True, "scope": effective_scope},
        )
        raise_for_status_with_detail(response)
        import base64
        return base64.b64decode(response.json()["content"])

    @staticmethod
    async def write(
        path: str,
        content: str,
        location: str = "workspace",
        mode: Mode = "cloud",
        expected_version: str | None = None,
        create_only: bool = False,
        scope: str | None = None,
    ) -> None:
        """
        Write text to a file.

        Args:
            path: File path relative to location root
            content: Text content to write
            location: Storage location (special or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            expected_version: Opaque version from ``files.stat`` required for
                a guarded replacement.
            create_only: Create a new file and fail if the path already exists.
            scope: Org scope; provider-org override allowed.
        """
        effective_scope = resolve_scope(scope)
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/files/write{_solution_query()}",
            json={
                "path": path,
                "content": content,
                "location": location,
                "mode": mode,
                "binary": False,
                "expected_version": expected_version,
                "create_only": create_only,
                "scope": effective_scope,
            },
        )
        raise_for_status_with_detail(response)

    @staticmethod
    async def write_bytes(
        path: str,
        content: bytes,
        location: str = "workspace",
        mode: Mode = "cloud",
        expected_version: str | None = None,
        create_only: bool = False,
        scope: str | None = None,
    ) -> None:
        """
        Write binary data to a file.

        Args:
            path: File path relative to location root
            content: Binary content to write
            location: Storage location (special or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            expected_version: Opaque version from ``files.stat`` required for
                a guarded replacement.
            create_only: Create a new file and fail if the path already exists.
            scope: Org scope; provider-org override allowed.
        """
        import base64
        encoded_content = base64.b64encode(content).decode('utf-8')
        effective_scope = resolve_scope(scope)
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/files/write{_solution_query()}",
            json={
                "path": path,
                "content": encoded_content,
                "location": location,
                "mode": mode,
                "binary": True,
                "expected_version": expected_version,
                "create_only": create_only,
                "scope": effective_scope,
            },
        )
        raise_for_status_with_detail(response)

    @staticmethod
    async def list(
        directory: str = "",
        location: str = "workspace",
        mode: Mode = "cloud",
        scope: str | None = None,
    ) -> list[str]:
        """
        List files in a directory.

        Args:
            directory: Directory path relative to location root (default: root)
            location: Storage location (special or freeform)
            mode: Storage mode (local or cloud, default: cloud)

        Returns:
            list[str]: List of file and directory names

        Raises:
            ValueError: If path is outside allowed directories

        Example:
            >>> from bifrost import files
            >>> items = await files.list("uploads")
            >>> for item in items:
            ...     print(item)
        """
        effective_scope = resolve_scope(scope)
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/files/list{_solution_query()}",
            json={"directory": directory, "location": location, "mode": mode, "scope": effective_scope},
        )
        raise_for_status_with_detail(response)
        return response.json()["files"]

    @staticmethod
    async def delete(
        path: str,
        location: str = "workspace",
        mode: Mode = "cloud",
        expected_version: str | None = None,
        scope: str | None = None,
    ) -> None:
        """
        Delete a file.

        Args:
            path: File path relative to location root
            location: Storage location (special or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            expected_version: Opaque version from ``files.stat`` required for
                a guarded delete.
            scope: Org scope; provider-org override allowed.

        Example:
            >>> from bifrost import files
            >>> await files.delete("temp/old_file.txt", location="temp")
        """
        effective_scope = resolve_scope(scope)
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/files/delete{_solution_query()}",
            json={
                "path": path,
                "location": location,
                "mode": mode,
                "expected_version": expected_version,
                "scope": effective_scope,
            },
        )
        raise_for_status_with_detail(response)

    @staticmethod
    async def stat(
        path: str,
        location: str = "workspace",
        mode: Mode = "cloud",
        scope: str | None = None,
    ) -> dict:
        """
        Fetch file metadata for conflict-safe workflows.

        Returns:
            dict with keys: path, exists, version, size, last_modified, updated_by
        """
        effective_scope = resolve_scope(scope)
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/files/stat{_solution_query()}",
            json={"path": path, "location": location, "mode": mode, "scope": effective_scope},
        )
        raise_for_status_with_detail(response)
        return response.json()

    @staticmethod
    async def exists(
        path: str,
        location: str = "workspace",
        mode: Mode = "cloud",
        scope: str | None = None,
    ) -> bool:
        """
        Check if a file exists.

        Args:
            path: File path relative to location root
            location: Storage location (special or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            scope: Org scope; provider-org override allowed.
        """
        effective_scope = resolve_scope(scope)
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/files/exists{_solution_query()}",
            json={"path": path, "location": location, "mode": mode, "scope": effective_scope},
        )
        raise_for_status_with_detail(response)
        return response.json()["exists"]

    @staticmethod
    async def get_signed_url(
        path: str,
        method: Literal["PUT", "GET"] = "PUT",
        content_type: str = "application/octet-stream",
        location: str = "uploads",
        scope: str | None = None,
        expires_in: int = 600,
    ) -> dict:
        """
        Generate a presigned S3 URL for direct file upload or download.

        Args:
            path: File path relative to location root (NOT including scope segment)
            method: "PUT" for upload, "GET" for download
            content_type: MIME type (only used for PUT)
            location: Storage location. Defaults to "uploads" for backwards
                compatibility with form upload flows. Use "workspace" to sign
                URLs for files written via `files.write_bytes(..., location="workspace")`.
            scope: Org scope; provider-org override allowed.
            expires_in: URL lifetime in seconds, from 1 second through 7 days.

        Returns:
            dict with keys: url, path, expires_in

        Example:
            >>> # Generate a download URL for a file written to workspace
            >>> await files.write_bytes("report.pdf", pdf_bytes, location="workspace")
            >>> signed = await files.get_signed_url(
            ...     "report.pdf", method="GET", location="workspace",
            ...     content_type="application/pdf",
            ... )
        """
        effective_scope = resolve_scope(scope)
        client = get_client()
        response = await client.engine_request(
            "POST",
            f"/api/files/signed-url{_solution_query()}",
            json={
                "path": path,
                "method": method,
                "content_type": content_type,
                "location": location,
                "scope": effective_scope,
                "expires_in": expires_in,
            },
        )
        raise_for_status_with_detail(response)
        return response.json()

    @staticmethod
    async def search(
        query: str,
        *,
        is_regex: bool = False,
        case_sensitive: bool = False,
        include_pattern: str | None = None,
        source: str = "all",
        solution_id: str | None = None,
        output_mode: str = "content",
        context_lines: int = 1,
        limit: int = 25,
        cursor: str | None = None,
    ) -> dict:
        """
        Search workspace and Solution source like grep, one page at a time.

        Note:
            Unlike the other ``files`` methods, ``search`` has no ``scope`` parameter.
            It searches source, not runtime file bytes: the instance ``_repo/``
            workspace and every Solution install's deployed source.

        Args:
            query: Literal text, or a Python ``re`` pattern when ``is_regex`` is true.
            is_regex: Treat query as a regular expression (default: False).
            case_sensitive: Case-sensitive matching (default: False).
            include_pattern: ripgrep-style glob, e.g. ``"*.py"`` or ``"workflows/**"``.
            source: ``"all"`` (default), ``"workspace"``, or ``"solutions"``.
            solution_id: Restrict to one Solution install's source.
            output_mode: ``"content"`` (matching lines) or ``"files"`` (one entry per file).
            context_lines: Lines of context around each match (0-5, default 1).
            limit: Results per page (1-200, default 25).
            cursor: ``next_cursor`` from the previous page of this exact search.

        Returns:
            dict with keys: query, output_mode, matches, files, returned,
            has_more_matches, response_complete, next_cursor, guidance,
            search_time_ms. Each match has file_path, line, column, text,
            context_before, context_after, and source (kind, solution_slug,
            editable).

        Example:
            >>> from bifrost import files
            >>> cursor = None
            >>> while True:
            ...     page = await files.search("TODO", include_pattern="*.py", cursor=cursor)
            ...     for m in page["matches"]:
            ...         print(f"{m['file_path']}:{m['line']}: {m['text']}")
            ...     if page["response_complete"]:
            ...         break
            ...     cursor = page["next_cursor"]
        """
        body: dict = {
            "query": query,
            "is_regex": is_regex,
            "case_sensitive": case_sensitive,
            "source": source,
            "output_mode": output_mode,
            "context_lines": context_lines,
            "limit": limit,
        }
        if include_pattern is not None:
            body["include_pattern"] = include_pattern
        if solution_id is not None:
            body["solution_id"] = solution_id
        if cursor is not None:
            body["cursor"] = cursor
        client = get_client()
        response = await client.engine_request("POST", "/api/files/search", json=body)
        raise_for_status_with_detail(response)
        return response.json()
