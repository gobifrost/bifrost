"""
Exception classes that workflows raise.

The SDK and the platform share these classes: ``src.sdk.errors`` re-exports
them, so ``bifrost.UserError`` is the class the engine catches in both modes.
"""


class UserError(Exception):
    """
    Exception that displays its message to end users.

    Use this for validation errors, business logic failures, or any error
    that users should see and understand. The error message will be visible
    to all users (not just platform admins).

    For internal/technical errors, use standard Python exceptions (ValueError,
    RuntimeError, etc.) which will show generic error messages to regular users
    while platform admins can see the full details.

    Examples:
        >>> from bifrost import UserError
        >>>
        >>> # Validation error
        >>> if not customer_id:
        >>>     raise UserError("Customer ID is required")
        >>>
        >>> # Business logic error
        >>> customer = get_customer(customer_id)
        >>> if not customer:
        >>>     raise UserError(f"Customer not found: {customer_id}")
        >>>
        >>> # API integration error
        >>> response = api.create_ticket(data)
        >>> if response.status_code != 200:
        >>>     raise UserError(f"Failed to create ticket: {response.error}")
    """
    pass


class WorkflowError(Exception):
    """
    Base class for workflow-related errors.

    This is used internally by the Bifrost engine for workflow-specific errors.
    Workflows should typically use UserError or standard Python exceptions instead.
    """
    pass


class ValidationError(WorkflowError):
    """Raised when workflow input validation fails."""
    pass


class IntegrationError(WorkflowError):
    """Raised when integration with external service fails."""
    pass


class ConfigurationError(WorkflowError):
    """Raised when workflow configuration is invalid."""
    pass
