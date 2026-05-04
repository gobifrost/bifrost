"""Function registry for policy expressions.

Each registered function provides BOTH a per-row evaluator and a SQL
compiler. Both forms must be supplied at registration so they cannot
drift. A function whose semantics cannot be expressed as a SQL literal
at request time (e.g., needs a DB lookup) cannot be registered here —
denormalize the relationship into a row field instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Final, Protocol


class _PolicyUser(Protocol):
    """Attributes the policy function registry reads off the user.

    Mirrors a subset of ``shared.types.Principal``. Kept narrow so pyright
    catches drift if Principal evolves.
    """

    role_names: list[str]
    role_ids: list[Any]  # UUIDs at runtime, but tests pass either UUID or str


@dataclass(frozen=True)
class FunctionDef:
    """A registered policy function."""

    evaluate: Callable[[list[Any], _PolicyUser, dict], bool]
    """Per-row evaluator. Args: (resolved_args, user, row) -> bool."""

    compile: Callable[[list[Any], _PolicyUser], bool]
    """SQL compiler. Args: (resolved_args, user) -> bool literal.

    Returns a Python bool because the compiler resolves these at compile
    time (no SQL CASE expressions for function calls).
    """

    arg_types: list[type]
    """Expected types of args for the validator at table create/update."""


def _has_role_evaluate(args: list, user: _PolicyUser, row: dict) -> bool:
    target = args[0]
    if target in user.role_names:
        return True
    return target in [str(r) for r in user.role_ids]


def _has_role_compile(args: list, user: _PolicyUser) -> bool:
    return _has_role_evaluate(args, user, row={})


# Closed catalog. To add a function: review the no-DB-lookup constraint above
# and ensure both `evaluate` and `compile` paths agree.
FUNCTIONS: Final[dict[str, FunctionDef]] = {
    "has_role": FunctionDef(
        evaluate=_has_role_evaluate,
        compile=_has_role_compile,
        arg_types=[str],
    ),
}
