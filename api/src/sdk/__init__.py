"""
Bifrost SDK - Decorators for workflows, data providers, and AI tools.

Usage:
    from src.sdk import workflow, tool, data_provider

    @workflow
    async def my_workflow(name: str) -> dict:
        '''A regular workflow.'''
        return {"message": f"Hello {name}"}

    @tool
    async def get_user(email: str) -> dict:
        '''Get user information - available as an AI agent tool.'''
        return {"email": email, "name": "John Doe"}

    @data_provider
    async def get_users() -> list[dict]:
        '''Get list of users for form dropdowns.'''
        return [{"value": "user1", "label": "User 1"}]
"""

from src.sdk.decorators import data_provider, service, tool, workflow

__all__ = [
    "workflow",
    "tool",
    "data_provider",
    "service",
]
