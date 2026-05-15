"""
File management SDK for Bifrost.

Provides async Python API for file operations with two storage modes:
- local: Local filesystem (CWD, /tmp/bifrost/temp, /tmp/bifrost/uploads)
- cloud: S3 storage (default)

Location Options:
- "workspace": Persistent workspace files (CWD in local mode, S3 bucket root in cloud mode)
- "temp": Temporary files (_tmp/ prefix in cloud, /tmp/bifrost/temp in local)
- "uploads": Files uploaded via form file fields (uploads/ prefix in cloud, /tmp/bifrost/uploads in local)

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

from .client import get_client, raise_for_status_with_detail
from ._context import resolve_scope

Mode = Literal["local", "cloud"]
# `location` is a free string. Reserved names: "workspace", "temp", "uploads".
# Anything else is a freeform user-defined location (e.g. "reports", "exports").


class files:
    """
    File management operations (async).

    Provides safe file access with two storage modes:
    - local: Local filesystem (for CLI usage)
    - cloud: S3 storage (for platform execution, default)

    All operations are performed via HTTP API endpoints.
    """

    @staticmethod
    async def read(
        path: str,
        location: str = "workspace",
        mode: Mode = "cloud",
        scope: str | None = None,
    ) -> str:
        """
        Read a text file.

        Args:
            path: File path relative to location root
            location: Storage location. Reserved: "workspace", "temp", "uploads".
                Freeform names (e.g. "reports") are also allowed.
            mode: Storage mode (local or cloud, default: cloud)
            scope: Org scope. Defaults to the current execution's org.
                Provider orgs may pass an explicit scope to read from another org.

        Example:
            >>> from bifrost import files
            >>> content = await files.read("data/customers.csv")
            >>> uploaded = await files.read("form_id/uuid/file.txt", location="uploads")
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/files/read",
            json={"path": path, "location": location, "mode": mode, "binary": False, "scope": effective_scope}
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
            location: Storage location (reserved or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            scope: Org scope; provider-org override allowed.
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/files/read",
            json={"path": path, "location": location, "mode": mode, "binary": True, "scope": effective_scope}
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
        scope: str | None = None,
    ) -> None:
        """
        Write text to a file.

        Args:
            path: File path relative to location root
            content: Text content to write
            location: Storage location (reserved or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            scope: Org scope; provider-org override allowed.
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/files/write",
            json={"path": path, "content": content, "location": location, "mode": mode, "binary": False, "scope": effective_scope}
        )
        raise_for_status_with_detail(response)

    @staticmethod
    async def write_bytes(
        path: str,
        content: bytes,
        location: str = "workspace",
        mode: Mode = "cloud",
        scope: str | None = None,
    ) -> None:
        """
        Write binary data to a file.

        Args:
            path: File path relative to location root
            content: Binary content to write
            location: Storage location (reserved or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            scope: Org scope; provider-org override allowed.
        """
        client = get_client()
        import base64
        encoded_content = base64.b64encode(content).decode('utf-8')
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/files/write",
            json={"path": path, "content": encoded_content, "location": location, "mode": mode, "binary": True, "scope": effective_scope}
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
            location: Storage location (workspace or temp)
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
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/files/list",
            json={"directory": directory, "location": location, "mode": mode, "scope": effective_scope}
        )
        raise_for_status_with_detail(response)
        return response.json()["files"]

    @staticmethod
    async def delete(
        path: str,
        location: str = "workspace",
        mode: Mode = "cloud",
        scope: str | None = None,
    ) -> None:
        """
        Delete a file.

        Args:
            path: File path relative to location root
            location: Storage location (reserved or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            scope: Org scope; provider-org override allowed.

        Example:
            >>> from bifrost import files
            >>> await files.delete("temp/old_file.txt", location="temp")
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/files/delete",
            json={"path": path, "location": location, "mode": mode, "scope": effective_scope}
        )
        raise_for_status_with_detail(response)

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
            location: Storage location (reserved or freeform)
            mode: Storage mode (local or cloud, default: cloud)
            scope: Org scope; provider-org override allowed.
        """
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/files/exists",
            json={"path": path, "location": location, "mode": mode, "scope": effective_scope}
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
        client = get_client()
        effective_scope = resolve_scope(scope)
        response = await client.post(
            "/api/files/signed-url",
            json={
                "path": path,
                "method": method,
                "content_type": content_type,
                "location": location,
                "scope": effective_scope,
            }
        )
        raise_for_status_with_detail(response)
        return response.json()

    @staticmethod
    async def search(
        query: str,
        case_sensitive: bool = False,
        is_regex: bool = False,
        include_pattern: str = "**/*",
        max_results: int = 1000,
    ) -> dict:
        """
        Search workspace file contents.

        Note:
            Unlike the other ``files`` methods, ``search`` has no ``scope`` parameter.
            The server scopes results by the caller's identity; a provider-org cannot
            cross-search another org's workspace through this endpoint.

        Args:
            query: Text or regex pattern to search for.
            case_sensitive: Case-sensitive matching (default: False).
            is_regex: Treat query as a regex (default: False; literal substring).
            include_pattern: Glob restricting which files to search (default: ``**/*``).
                The SDK does not expose the server's nullable form; callers always send
                a pattern, defaulting to "match all files".
            max_results: Maximum results returned (default: 1000, max: 10000).

        Returns:
            dict with keys: query, total_matches, files_searched, results,
            truncated, search_time_ms. ``results`` is a list of dicts with
            keys: file_path, line, column, match_text, context_before,
            context_after.

        Example:
            >>> from bifrost import files
            >>> hits = await files.search("TODO", include_pattern="**/*.py")
            >>> for r in hits["results"]:
            ...     print(f"{r['file_path']}:{r['line']}: {r['match_text']}")
        """
        client = get_client()
        response = await client.post(
            "/api/files/search",
            json={
                "query": query,
                "case_sensitive": case_sensitive,
                "is_regex": is_regex,
                "include_pattern": include_pattern,
                "max_results": max_results,
            },
        )
        raise_for_status_with_detail(response)
        return response.json()
