"""Shared lifecycle limits for asynchronous Solution jobs."""

from datetime import timedelta


DEPLOY_JOB_TIMEOUT_SECONDS = 15 * 60
DEPLOY_JOB_TIMEOUT = timedelta(seconds=DEPLOY_JOB_TIMEOUT_SECONDS)
DEPLOY_JOB_TIMEOUT_ERROR = (
    "Solution job exceeded the 15-minute timeout. Re-run it; Solution deploys "
    "and installs are idempotent."
)
