"""Server-side source of truth for the CLI contract version.

This integer is returned to the CLI at ``GET /api/version`` as
``contract_version`` and compared against the CLI's baked-in value to decide
whether a CLI is contract-compatible with this server.

**Bump this (and the CLI mirror in ``api/bifrost/contract_version.py``) only on
a BREAKING change to the contract surface the CLI consumes** — a request/response
DTO field removed/renamed/retyped, or a route the CLI calls renamed. Additive
or cosmetic changes do NOT bump it. The tripwire in
``tests/unit/test_contract_version.py`` forces this decision at PR time.
"""

#: Breaking-change counter for the CLI <-> server contract. See module docstring.
CONTRACT_VERSION: int = 1


def get_contract_version() -> int:
    """Return the server's CLI contract version.

    Prefer this accessor over importing the bare constant: it gives the value a
    single resolved read site (callers go through a function, not a module
    global) and keeps this module symmetric with the rest of ``shared/``.
    """
    return CONTRACT_VERSION
