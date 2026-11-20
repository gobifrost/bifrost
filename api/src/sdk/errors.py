"""
Custom exception classes for Bifrost workflows and scripts.

The workflow-facing classes live in ``bifrost._errors`` so the standalone SDK
and the platform raise and catch the same classes.
"""

from bifrost._errors import (
    ConfigurationError,
    IntegrationError,
    UserError,
    ValidationError,
    WorkflowError,
)

__all__ = [
    "ConfigurationError",
    "IntegrationError",
    "UserError",
    "ValidationError",
    "WorkflowError",
    "WorkflowExecutionException",
]


class WorkflowExecutionException(Exception):
    """
    Wrapper exception that preserves captured variables and logs.

    This is used internally by the Bifrost engine to capture execution context
    when an exception occurs. Do not use this directly in workflows.
    """
    def __init__(self, original_exception: Exception, captured_vars: dict, logs: list):
        self.original_exception = original_exception
        self.captured_vars = captured_vars
        self.logs = logs
        super().__init__(str(original_exception))
