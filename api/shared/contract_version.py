"""Legacy transition marker for CLIs released without minimum-version gating.

Version 10 is returned by ``GET /api/version`` so already-installed CLIs that
still treat ``contract_version`` as a hard gate upgrade once into a CLI that
honors the server's ``min_cli_version``. New CLIs ignore this value at runtime.

Keep this marker frozen at 10. The ongoing compatibility mechanism is
``MIN_CLI_VERSION`` in ``shared/version.py``; the contract fingerprint test is
the development-time tripwire that prompts an explicit minimum-version decision.
"""

#: Frozen one-release bridge for legacy CLIs. See module docstring.
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
    """Return the server's frozen legacy bridge version.

    Prefer this accessor over importing the bare constant: it gives the value a
    single resolved read site (callers go through a function, not a module
    global) and keeps this module symmetric with the rest of ``shared/``.
    """
    return CONTRACT_VERSION
