"""
bifrost/forms.py - Forms SDK

Provides Python API for form operations (read-only).

Inside an engine child these reads go through the worker's private Unix
socket (the parent serves the real HTTP endpoints); external callers use HTTP.
"""

from __future__ import annotations

from .client import get_client, raise_for_status_with_detail
from .models import FormPublic


class forms:
    """
    Form operations (read-only).

    Allows workflows to list and get form definitions.

    All methods are async - await is required.
    """

    @staticmethod
    async def list() -> list[FormPublic]:
        """
        List all forms available to the current user.

        Returns:
            list[FormPublic]: List of form objects with attributes:
                - id: str - Form ID
                - name: str - Form name
                - description: str | None - Form description
                - workflow_id: str | None - Linked workflow ID
                - launch_workflow_id: str | None - Workflow to launch on submit
                - default_launch_params: dict | None - Default params for launch
                - allowed_query_params: list[str] | None - Allowed URL query params
                - form_schema: dict | None - Form field schema
                - access_level: str - Access level
                - organization_id: str | None - Organization ID
                - is_active: bool - Whether form is active
                - file_path: str | None - Workspace file path
                - created_at, updated_at: datetime | None

        Raises:
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import forms
            >>> all_forms = await forms.list()
            >>> for form in all_forms:
            ...     print(f"{form.id}: {form.name}")
        """
        client = get_client()
        response = await client.engine_request("GET", "/api/forms")
        raise_for_status_with_detail(response)
        data = response.json()
        return [FormPublic.model_validate(form) for form in data]

    @staticmethod
    async def get(form_id: str) -> FormPublic:
        """
        Get a form definition by ID.

        Args:
            form_id: Form ID

        Returns:
            FormPublic: Form object with attributes:
                - id: str - Form ID
                - name: str - Form name
                - description: str | None - Form description
                - workflow_id: str | None - Linked workflow ID
                - launch_workflow_id: str | None - Workflow to launch on submit
                - default_launch_params: dict | None - Default params for launch
                - allowed_query_params: list[str] | None - Allowed URL query params
                - form_schema: dict | None - Form field schema
                - access_level: str - Access level
                - organization_id: str | None - Organization ID
                - is_active: bool - Whether form is active
                - file_path: str | None - Workspace file path
                - created_at, updated_at: datetime | None

        Raises:
            ValueError: If form not found
            PermissionError: If user doesn't have access to the form
            RuntimeError: If not authenticated

        Example:
            >>> from bifrost import forms
            >>> form = await forms.get("form-123")
            >>> print(form.name)
        """
        client = get_client()
        response = await client.engine_request("GET", f"/api/forms/{form_id}")
        if response.status_code == 404:
            raise ValueError(f"Form not found: {form_id}")
        elif response.status_code == 403:
            raise PermissionError(f"Access denied to form: {form_id}")
        raise_for_status_with_detail(response)
        return FormPublic.model_validate(response.json())
