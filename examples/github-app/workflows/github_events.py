"""Read-only examples. Subscription input mapping: {"event": "{{ payload }}"}."""

from bifrost import workflow


@workflow(name="github_pr_triage")
async def github_pr_triage(event: dict) -> dict:
    """Summarize an opened PR for a tenant's existing triage workflow."""
    if event["event"] != "pull_request" or event["action"] != "opened":
        raise ValueError("Subscribe this workflow only to github.pull_request.opened")
    pull_request = event["payload"]["pull_request"]
    return {"delivery_id": event["delivery_id"], "repository_id": event["repository_id"],
            "summary": f"PR #{pull_request['number']}: {pull_request['title']}",
            "url": pull_request["html_url"]}


@workflow(name="github_failed_check")
async def github_failed_check(event: dict) -> dict:
    """Return a failed-check notification payload without writing to GitHub."""
    if event["event"] != "check_run" or event["action"] != "completed":
        raise ValueError("Subscribe this workflow only to github.check_run.completed")
    check = event["payload"]["check_run"]
    failed = check["conclusion"] in {"failure", "timed_out", "action_required", "startup_failure"}
    return {"delivery_id": event["delivery_id"], "repository_id": event["repository_id"],
            "notify": failed, "summary": f"{check['name']}: {check['conclusion']}",
            "url": check["html_url"]}


@workflow(name="github_release_notice")
async def github_release_notice(event: dict) -> dict:
    """Return a release announcement payload for an existing notification step."""
    if event["event"] != "release" or event["action"] != "published":
        raise ValueError("Subscribe this workflow only to github.release.published")
    release = event["payload"]["release"]
    return {"delivery_id": event["delivery_id"], "repository_id": event["repository_id"],
            "summary": f"Release {release['tag_name']}", "url": release["html_url"]}
