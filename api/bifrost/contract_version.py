"""CLI-side mirror of the contract version (baked into the wheel).

Must equal ``api/shared/contract_version.py::CONTRACT_VERSION``. The runtime
gate in ``cli.py`` compares this baked value against the ``contract_version``
the server reports at ``GET /api/version``; a mismatch hard-blocks every
command until the user upgrades.

**Bump this together with the server constant on a BREAKING contract change
only.** The tripwire in ``tests/unit/test_contract_version.py`` asserts the two
integers agree and fails if a CLI-consumed contract changed without a decision.
"""

#: Must equal shared.contract_version.CONTRACT_VERSION. See module docstring.
CONTRACT_VERSION: int = 1


def get_contract_version() -> int:
    """Return the CLI's baked contract version.

    Mirrors ``shared.contract_version.get_contract_version``. The runtime gate in
    ``cli.py`` uses this so the value has a single read site rather than a bare
    global reference.
    """
    return CONTRACT_VERSION
