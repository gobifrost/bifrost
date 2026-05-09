"""Shared Pydantic models used by API routers and clients."""

from pydantic import BaseModel


class VersionResponse(BaseModel):
    version: str
