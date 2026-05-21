"""Binding protocol — domain-agnostic reference resolution at compile time.

Domains with a SQL surface (tables) implement a Binding that maps
`{<namespace>: path}` references to SQLAlchemy column expressions. Domains
without a SQL surface (files — list operations are S3 prefix-bound; the
evaluator filters in Python) do not implement Binding.
"""
from __future__ import annotations

from typing import Any, ClassVar, Protocol, runtime_checkable

from sqlalchemy.sql import ColumnElement


@runtime_checkable
class Binding(Protocol):
    """Resolves `{<namespace>: path}` references to SQLAlchemy columns."""

    namespace: ClassVar[str] = ""
    """Must match the matching Resolver's namespace for the same domain.

    The default empty string satisfies Protocol structural checks (a bare
    `ClassVar` annotation on a Protocol is not visible to `hasattr` or
    `isinstance`). Concrete bindings must override with a non-empty domain
    string.
    """

    def resolve_reference(self, path: str) -> ColumnElement[Any]:
        """Map a dot-path into a SQLAlchemy column expression."""
        ...
