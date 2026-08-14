"""CLI mirror of the frozen legacy contract bridge.

Version 10 makes CLIs released before server-controlled minimum gating upgrade
once. CLIs containing this module no longer compare the value at runtime; they
honor ``min_cli_version`` instead. Keep this mirror frozen and equal to
``api/shared/contract_version.py`` while the server exposes the bridge field.
"""

#: Frozen legacy bridge; must equal shared.contract_version.CONTRACT_VERSION.
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
#     callers poll SolutionDeployJobStatus (install_id now nullable) for the
#     solution_id (2026-07-02)
# v8: Application publish is async: POST /api/applications/{id}/publish returns
#     202 + PlatformJobAccepted (was 200 + ApplicationPublic); callers poll the
#     standardized PlatformJobPublic contract (2026-07-28)
# v9: PlatformJobStatus gained the waiting state used by durable parent jobs;
#     stale CLIs cannot parse that enum value and must upgrade (2026-08-07)
# v10: restore the server-controlled minimum CLI gate. The contract bump is the
#      one-time bridge that forces CLIs shipped while min_cli_version was absent
#      to upgrade to a build that honors the restored floor (2026-08-14).
CONTRACT_VERSION: int = 10


def get_contract_version() -> int:
    """Return the CLI's frozen legacy bridge version.

    Mirrors ``shared.contract_version.get_contract_version`` for packaging and
    transition tests. New CLI runtime gating does not consume this value.
    """
    return CONTRACT_VERSION
