"""
Bifrost CLI Git Commands

Subcommands for `bifrost git` that mirror the UI's source control panel.
Each command queues a job via the API and polls for results.
"""

import asyncio
import sys
from typing import Any

import click

from .client import BifrostClient
from .platform_jobs import poll_platform_job

# Exit codes
EXIT_CLEAN = 0      # Operation completed successfully
EXIT_CONFLICTS = 1  # Conflicts need resolution
EXIT_ERROR = 2      # Error occurred

# Map CLI-friendly names to API resolution strategies
RESOLUTION_MAP = {
    "keep_local": "ours",
    "keep_remote": "theirs",
}


def _post_platform_job(
    client: BifrostClient,
    endpoint: str,
    *,
    label: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Queue a durable operation and poll its shared PlatformJob status."""

    async def _run() -> dict[str, Any]:
        response = await client.post(endpoint, json=body)
        if response.status_code != 202:
            raise RuntimeError(
                f"{label} was not accepted ({response.status_code}): {response.text[:200]}"
            )
        accepted = response.json()
        job_id = accepted.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise RuntimeError(f"{label} response did not include a platform job id")
        return await poll_platform_job(
            client, job_id, label=label, allow_requires_action=True
        )

    return asyncio.run(_run())


def _preview_connect(
    client: BifrostClient, repository_url: str, branch: str
) -> dict[str, Any]:
    """Request a non-mutating first-connect reconciliation preview."""

    async def _run() -> dict[str, Any]:
        response = await client.post(
            "/api/github/connect/preview",
            json={"repository_url": repository_url, "branch": branch},
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Git connection preview failed ({response.status_code}): {response.text[:200]}"
            )
        preview = response.json()
        if not isinstance(preview.get("token"), str):
            raise RuntimeError("Git connection preview did not include a preview token")
        return preview

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_changed_files(data: dict) -> None:
    """Print changed files from a status/fetch result."""
    changed_files = data.get("changed_files") or []
    if not changed_files:
        print("No changed files")
        return

    print(f"{len(changed_files)} changed file(s):")
    status_symbols = {"added": "+", "modified": "~", "deleted": "-", "renamed": "R"}
    for f in changed_files:
        symbol = status_symbols.get(f.get("change_type", ""), "?")
        print(f"  {symbol} {f.get('path', 'unknown')}")


def _format_ahead_behind(data: dict) -> None:
    """Print ahead/behind counts."""
    ahead = data.get("commits_ahead", 0)
    behind = data.get("commits_behind", 0)
    parts = []
    if ahead:
        parts.append(f"{ahead} ahead")
    if behind:
        parts.append(f"{behind} behind")
    if parts:
        print(f"  {', '.join(parts)}")


def _format_sync_result(result: dict) -> list[str]:
    """Format the structured workspace sync result for people."""
    lines: list[str] = []
    status = result.get("status", "unknown")

    if status in ("success", "completed"):
        pulled = result.get("pulled", 0)
        pushed = result.get("pushed_commits", 0)
        commit_sha = result.get("commit_sha")
        parts = []
        if pulled:
            parts.append(f"pulled {pulled} change{'s' if pulled != 1 else ''}")
        if pushed:
            parts.append(f"pushed {pushed} commit{'s' if pushed != 1 else ''}")
        summary = ", ".join(parts) if parts else "no changes"
        sha_info = f" (commit {commit_sha[:7]})" if commit_sha else ""
        lines.append(f"Sync complete: {summary}{sha_info}")

        # Display entity-level changes
        entity_changes = (result.get("data") or {}).get("entity_changes") or result.get("entity_changes") or []
        if entity_changes:
            added = [c for c in entity_changes if c.get("action") == "added"]
            updated = [c for c in entity_changes if c.get("action") == "updated"]
            removed = [c for c in entity_changes if c.get("action") == "removed"]
            count_parts = []
            if added:
                count_parts.append(f"{len(added)} added")
            if updated:
                count_parts.append(f"{len(updated)} updated")
            if removed:
                count_parts.append(f"{len(removed)} removed")
            lines.append(f"  {len(entity_changes)} entity change(s): {', '.join(count_parts)}")
            symbols = {"added": "+", "updated": "~", "removed": "-"}
            for change in entity_changes:
                action = change.get("action", "")
                symbol = symbols.get(action, "?")
                etype = change.get("entity_type", "")
                name = change.get("name", "")
                reason = change.get("reason")
                suffix = f"  ({reason})" if reason else ""
                lines.append(f"    {symbol} {etype:<14} {name}{suffix}")

        return lines

    if status == "conflict":
        conflicts = result.get("conflicts") or []
        lines.append(f"{len(conflicts)} conflict{'s' if len(conflicts) != 1 else ''} detected:")
        lines.append("")
        for conflict in conflicts:
            name = conflict.get("display_name") or conflict.get("path", "unknown")
            entity_type = conflict.get("entity_type", "file")
            path = conflict.get("path", "unknown")
            lines.append(f"  {path} ({entity_type}: {name})")
        lines.append("")
        lines.append("To resolve conflicts, run:")
        for conflict in conflicts:
            path = conflict.get("path", "unknown")
            ours = conflict.get("ours_available", True)
            theirs = conflict.get("theirs_available", True)
            if ours:
                lines.append(f"  bifrost git resolve {path}=keep_local")
            if theirs:
                lines.append(f"  bifrost git resolve {path}=keep_remote")
        lines.append("")
        lines.append("To discard this merge and return to its pre-pull state, run:")
        lines.append("  bifrost git abort-merge")
        return lines

    action = result.get("requires_action")
    if action == "confirm_deletes":
        pending = result.get("pending_deletes") or (result.get("data") or {}).get("pending_deletes") or []
        lines.append("Sync requires confirmation before deleting:")
        for item in pending:
            path = item.get("path", item) if isinstance(item, dict) else item
            lines.append(f"  - {path}")
        lines.append("Review these paths, then rerun: bifrost git sync --confirm-deletes")
        return lines

    # Failed or unknown
    error = result.get("error") or result.get("message") or "Unknown error"
    lines.append(f"Sync failed: {error}")
    return lines


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def run_git_fetch(client: BifrostClient) -> int:
    """Regenerate manifest from DB, git fetch, show ahead/behind."""
    result = _run_platform_git_operation(
        client, "/api/github/fetch", label="Fetching", body={}
    )
    if result is None:
        return EXIT_ERROR

    if result.get("status") != "success":
        error = result.get("error") or "Fetch failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    _format_ahead_behind(result)
    _format_changed_files(result)

    # Show preflight issues if any
    preflight = result.get("preflight")
    if preflight and not preflight.get("valid", True):
        issues = preflight.get("issues") or []
        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]
        if errors:
            print(f"\n{len(errors)} preflight error(s):")
            for issue in errors:
                print(f"  x {issue.get('message', '')}")
        if warnings:
            print(f"\n{len(warnings)} preflight warning(s):")
            for issue in warnings:
                print(f"  ! {issue.get('message', '')}")

    return EXIT_CLEAN


def run_git_status(client: BifrostClient) -> int:
    """Show changed files and commits ahead/behind."""
    result = _run_platform_git_operation(
        client, "/api/github/changes", label="Checking status", body={}
    )
    if result is None:
        return EXIT_ERROR

    if result.get("status") != "success":
        error = result.get("error") or "Status check failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    _format_ahead_behind(result)
    _format_changed_files(result)

    conflicts = result.get("conflicts") or []
    if conflicts:
        print(f"\n{len(conflicts)} merge conflict(s):")
        for c in conflicts:
            print(f"  ! {c.get('path', 'unknown')}")

    return EXIT_CLEAN


def run_git_commit(client: BifrostClient, message: str) -> int:
    """Regenerate manifest, stage, preflight, commit."""
    result = _run_platform_git_operation(
        client,
        "/api/github/commit",
        label="Committing",
        body={"message": message},
    )
    if result is None:
        return EXIT_ERROR

    if result.get("status") != "success":
        error = result.get("error") or "Commit failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    commit_sha = result.get("commit_sha")
    files_committed = result.get("files_committed", 0)

    if commit_sha:
        print(f"Committed {commit_sha[:7]}")
    else:
        print("Nothing to commit")

    if files_committed:
        print(f"  {files_committed} file(s) committed")

    # Show preflight results
    preflight = result.get("preflight")
    if preflight and not preflight.get("valid", True):
        issues = preflight.get("issues") or []
        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]
        if errors:
            print(f"\n{len(errors)} preflight error(s) — commit blocked:")
            for issue in errors:
                print(f"  x {issue.get('message', '')}")
            return EXIT_ERROR
        if warnings:
            print(f"\n{len(warnings)} preflight warning(s):")
            for issue in warnings:
                print(f"  ! {issue.get('message', '')}")

    return EXIT_CLEAN


def _sync_result_from_platform_job(job: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a platform-job outcome without inventing a second result shape."""
    result = job.get("result")
    if isinstance(result, dict):
        normalized = dict(result)
        if (
            "status" not in normalized
            and not normalized.get("requires_action")
            and isinstance(normalized.get("success"), bool)
        ):
            normalized["status"] = "success" if normalized["success"] else "failed"
        return normalized
    return job


def _run_platform_git_operation(
    client: BifrostClient,
    endpoint: str,
    *,
    label: str,
    body: dict[str, Any],
) -> dict[str, Any] | None:
    """Queue a Git operation and return its shared PlatformJob terminal result."""
    try:
        job = _post_platform_job(client, endpoint, label=label, body=body)
    except (click.ClickException, RuntimeError, TimeoutError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None
    return _sync_result_from_platform_job(job)


def run_git_sync(client: BifrostClient, *, confirm_deletes: bool = False) -> int:
    """Fetch, reconcile, publish, and import workspace Git changes."""
    result = _run_platform_git_operation(
        client,
        "/api/github/sync",
        label="Syncing",
        body={"confirm_deletes": confirm_deletes},
    )
    if result is None:
        return EXIT_ERROR

    lines = _format_sync_result(result)
    for line in lines:
        print(line)

    status = result.get("status", "unknown")
    if status in ("success", "completed"):
        return EXIT_CLEAN
    elif status == "conflict":
        return EXIT_CONFLICTS
    else:
        return EXIT_ERROR


def run_git_push(client: BifrostClient) -> int:
    """Deprecated compatibility alias for :func:`run_git_sync`."""
    print("Warning: `bifrost git push` is deprecated; use `bifrost git sync`.", file=sys.stderr)
    return run_git_sync(client)


def run_git_abort_merge(client: BifrostClient) -> int:
    """Restore the workspace to its state before the current merge."""
    result = _run_platform_git_operation(
        client, "/api/github/abort-merge", label="Aborting merge", body={}
    )
    if result is None:
        return EXIT_ERROR
    if result.get("status") in {"success", "completed", "succeeded"}:
        print("Merge aborted; the workspace is restored to its pre-merge state.")
        return EXIT_CLEAN
    print(f"Error: {result.get('error') or result.get('message') or 'Unable to abort merge'}", file=sys.stderr)
    return EXIT_ERROR


def _format_connect_preview(preview: dict[str, Any]) -> None:
    """Render classified content so a caller can make an informed choice."""
    labels = {
        "local_only": "Local only",
        "remote_only": "Remote only",
        "identical": "Identical",
        "conflict": "Conflict",
    }
    for item in preview.get("items") or []:
        classification = item.get("classification", "unknown")
        print(f"{labels.get(classification, classification)}: {item.get('path', 'unknown')}")


def run_git_connect(
    client: BifrostClient,
    repository_url: str,
    *,
    branch: str,
    strategy: str | None,
    decisions: dict[str, str],
    confirm_destructive: bool,
) -> int:
    """Preview then apply a first Git connection without implicit reconciliation."""
    try:
        preview = _preview_connect(client, repository_url, branch)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    _format_connect_preview(preview)

    if strategy is None:
        if not sys.stdin.isatty():
            print(
                "Error: connect needs --strategy in non-interactive mode "
                "(publish-local, start-from-remote, or reconcile).",
                file=sys.stderr,
            )
            return EXIT_ERROR
        strategy = input("Strategy [publish-local/start-from-remote/reconcile]: ").strip()
    strategy = strategy.replace("-", "_")
    if strategy not in {"publish_local", "start_from_remote", "reconcile"}:
        print("Error: invalid connection strategy", file=sys.stderr)
        return EXIT_ERROR

    discards_local = any(
        item.get("classification") in {"local_only", "conflict"}
        for item in preview.get("items") or []
    )
    if strategy == "start_from_remote" and discards_local and not confirm_destructive:
        if not sys.stdin.isatty():
            print(
                "Error: start-from-remote would discard local content; "
                "pass --confirm-destructive to continue.",
                file=sys.stderr,
            )
            return EXIT_ERROR
        confirmation = input(
            "Starting from remote will discard reviewed local content. "
            "Type 'yes' to continue: "
        ).strip().lower()
        if confirmation != "yes":
            print("Connection cancelled; local content was not discarded.", file=sys.stderr)
            return EXIT_ERROR
        confirm_destructive = True

    conflicts = [
        item.get("path") for item in preview.get("items") or []
        if item.get("classification") == "conflict" and isinstance(item.get("path"), str)
    ]
    if strategy == "reconcile":
        missing = [path for path in conflicts if path not in decisions]
        if missing:
            if not sys.stdin.isatty():
                print(
                    "Error: reconcile needs --decision PATH=local|remote for each conflict: "
                    + ", ".join(missing),
                    file=sys.stderr,
                )
                return EXIT_ERROR
            for path in missing:
                choice = input(f"Keep local or remote for {path} [local/remote]: ").strip()
                if choice in {"local", "remote"}:
                    decisions[path] = choice
        invalid = [path for path in conflicts if decisions.get(path) not in {"local", "remote"}]
        if invalid:
            print("Error: invalid or missing reconciliation decisions", file=sys.stderr)
            return EXIT_ERROR

    body: dict[str, Any] = {
        "preview_token": preview["token"],
        "strategy": strategy,
        "decisions": decisions,
    }
    if strategy == "start_from_remote":
        body["confirm_destructive"] = confirm_destructive

    result = _run_platform_git_operation(
        client,
        "/api/github/connect",
        label="Connecting",
        body=body,
    )
    if result is None:
        return EXIT_ERROR
    for line in _format_sync_result(result):
        print(line)
    return EXIT_CLEAN if result.get("status") in {"success", "completed", "succeeded"} else EXIT_ERROR


def run_git_resolve(client: BifrostClient, resolutions: dict[str, str]) -> int:
    """Resolve merge conflicts."""
    # Map CLI names to API names
    api_resolutions = {
        path: RESOLUTION_MAP[resolution]
        for path, resolution in resolutions.items()
    }

    result = _run_platform_git_operation(
        client,
        "/api/github/resolve",
        label="Resolving",
        body={"resolutions": api_resolutions},
    )
    if result is None:
        return EXIT_ERROR

    lines = _format_sync_result(result)
    for line in lines:
        print(line)

    status = result.get("status", "unknown")
    if status in ("success", "completed"):
        return EXIT_CLEAN
    elif status == "conflict":
        return EXIT_CONFLICTS
    else:
        return EXIT_ERROR


def run_git_diff(client: BifrostClient, path: str) -> int:
    """Show file diff."""
    result = _run_platform_git_operation(
        client, "/api/github/diff", label="Diffing", body={"path": path}
    )
    if result is None:
        return EXIT_ERROR

    if result.get("status") != "success":
        error = result.get("error") or "Diff failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    diff_text = result.get("diff")
    if diff_text:
        print(diff_text)
    else:
        # Show head vs working content if no unified diff
        head = result.get("head_content")
        working = result.get("working_content")
        if head is None and working is not None:
            print(f"New file: {path}")
            print(working)
        elif head is not None and working is None:
            print(f"Deleted file: {path}")
        elif head == working:
            print("No changes")
        else:
            print(f"--- {path} (HEAD)")
            print(f"+++ {path} (working)")
            if head:
                for line in head.splitlines():
                    print(f"- {line}")
            if working:
                for line in working.splitlines():
                    print(f"+ {line}")

    return EXIT_CLEAN


def run_git_discard(client: BifrostClient, paths: list[str]) -> int:
    """Discard working tree changes."""
    result = _run_platform_git_operation(
        client, "/api/github/discard", label="Discarding", body={"paths": paths}
    )
    if result is None:
        return EXIT_ERROR

    if result.get("status") != "success":
        error = result.get("error") or "Discard failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    discarded = result.get("discarded") or []
    if discarded:
        print(f"Discarded changes to {len(discarded)} file(s):")
        for p in discarded:
            print(f"  {p}")
    else:
        print("No changes discarded")

    return EXIT_CLEAN
