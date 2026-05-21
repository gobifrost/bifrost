"""Resolver protocol — domain-agnostic reference resolution at evaluate time.

Each domain (tables, files, ...) implements a Resolver that knows how to look
up `{<namespace>: path}` references against its context shape. The evaluator
walker treats Resolver opaquely; the namespace string is the only piece of
domain knowledge the walker carries.
"""
from __future__ import annotations

from typing import Any, ClassVar, Protocol, runtime_checkable


@runtime_checkable
class Resolver(Protocol):
    """Resolves `{<namespace>: path}` references against a domain context."""

    namespace: ClassVar[str]
    """The reference namespace key this resolver handles. E.g. "row" for tables,
    "file" for files. The walker compares against `set(node.keys()) == {namespace}`.
    """

    def resolve(self, path: str, ctx: Any) -> Any:
        """Resolve a dot-path against the domain ctx. Missing returns None."""
        ...
