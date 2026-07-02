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
# v2: claims organization_id widened to nullable for global/solution-managed claims (2026-06-13)
# v4: unified --org standard — SolutionCreate/SolutionBase drop `scope` (install
#     kind is derived from organization_id); SolutionRepoPreviewRequest gains
#     organization_id; descriptor no longer carries scope (2026-06-15)
# v5: Solution deploy is async: POST /deploy returns 202 + deploy_job_id and
#     callers poll SolutionDeployJobStatus for the deploy summary (2026-06-17)
# v6: Solution deploy uploads a workspace zip as multipart/form-data instead of
#     the legacy JSON bundle request body (2026-06-21)
# v7: Solution install (zip + from-repo) is async: POST /install and
#     /install/from-repo return 202 + deploy_job_id (was 200/201 + Solution);
#     callers poll SolutionDeployJobStatus (whose install_id is now nullable —
#     a zip install resolves its target inside the job) for the solution_id
#     (2026-07-02)
CONTRACT_VERSION: int = 7


def get_contract_version() -> int:
    """Return the server's CLI contract version.

    Prefer this accessor over importing the bare constant: it gives the value a
    single resolved read site (callers go through a function, not a module
    global) and keeps this module symmetric with the rest of ``shared/``.
    """
    return CONTRACT_VERSION
