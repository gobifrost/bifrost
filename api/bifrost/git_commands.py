"""
Bifrost CLI Git Commands

Subcommands for `bifrost git` that mirror the UI's source control panel.
Each command queues a job via the API and polls for results.
"""

import shlex
import sys
import time

from .client import BifrostClient

# Exit codes
EXIT_CLEAN = 0      # Operation completed successfully
EXIT_CONFLICTS = 1  # Conflicts need resolution
EXIT_ERROR = 2      # Error occurred

# Map CLI-friendly names to API resolution strategies
RESOLUTION_MAP = {
    "keep_local": "ours",
    "keep_remote": "theirs",
}


def poll_job(client: BifrostClient, job_id: str, label: str = "Working", timeout: int = 120) -> dict:
    """
    Poll job status endpoint until completion or timeout.

    Shows phase-by-phase progress when the server provides it.

    Args:
        client: BifrostClient instance (uses sync HTTP methods)
        job_id: Job ID to poll
        label: Label to display while polling
        timeout: Max seconds to wait

    Returns:
        Job result dict

    Raises:
        TimeoutError: If job doesn't complete within timeout
    """
    start = time.time()
    current_phase = ""
    print(label, end="", flush=True)

    while time.time() - start < timeout:
        response = client.get_sync(f"/api/jobs/{job_id}")

        if response.status_code != 200:
            raise RuntimeError(f"Failed to check job status: {response.status_code}")

        result = response.json()

        if result["status"] == "pending":
            # Show phase progress if available
            phase = result.get("message") or ""
            if phase and phase != current_phase:
                if current_phase:
                    print()  # Newline after previous phase
                print(f"  {phase}", end="", flush=True)
                current_phase = phase
            else:
                print(".", end="", flush=True)
            time.sleep(1)
            continue

        print()  # Newline after progress
        return result

    print()
    raise TimeoutError(f"{label} timed out. Check the platform UI for status.")


def _post_and_poll(client: BifrostClient, endpoint: str, label: str, json_body: dict | None = None, timeout: int = 120) -> dict:
    """POST to an endpoint, extract job_id, and poll for result."""
    response = client.post_sync(endpoint, json=json_body or {})
    if response.status_code != 200:
        print(f"Error: {response.status_code} - {response.text}", file=sys.stderr)
        sys.exit(EXIT_ERROR)

    job_id = response.json()["job_id"]

    try:
        return poll_job(client, job_id, label=label, timeout=timeout)
    except (TimeoutError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(EXIT_ERROR)


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
        symbol = status_symbols.get(f.get("status", ""), "?")
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
    """Format sync/push result data into human-readable lines."""
    lines: list[str] = []
    status = result.get("status", "unknown")

    if status in ("success", "completed"):
        pulled = result.get("pulled", 0)
        pushed = result.get("pushed", 0)
        commit_sha = result.get("commit_sha")
        parts = []
        if pulled:
            parts.append(f"pulled {pulled} change{'s' if pulled != 1 else ''}")
        if pushed:
            parts.append(f"pushed {pushed} commit{'s' if pushed != 1 else ''}")
        summary = ", ".join(parts) if parts else "no changes"
        sha_info = f" (commit {commit_sha[:7]})" if commit_sha else ""
        lines.append(f"Push complete: {summary}{sha_info}")

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
            lines.append(
                "  "
                + shlex.join(["bifrost", "git", "resolve", f"{path}=keep_remote"])
            )
            lines.append(
                "  "
                + shlex.join(["bifrost", "git", "resolve", f"{path}=keep_local"])
            )
        lines.append("")
        lines.append(
            "Or manage this in the Code Editor's Source Control at your Bifrost instance."
        )
        return lines

    # Failed or unknown
    error = result.get("error") or result.get("message") or "Unknown error"
    lines.append(f"Push failed: {error}")
    return lines


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def run_git_fetch(client: BifrostClient) -> int:
    """Regenerate manifest from DB, git fetch, show ahead/behind."""
    result = _post_and_poll(client, "/api/github/fetch", label="Fetching")

    if result.get("status") != "success":
        error = result.get("error") or "Fetch failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    data = result.get("data") or {}
    _format_ahead_behind(data)
    _format_changed_files(data)

    # Show preflight issues if any
    preflight = data.get("preflight")
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
    result = _post_and_poll(client, "/api/github/changes", label="Checking status")

    if result.get("status") != "success":
        error = result.get("error") or "Status check failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    data = result.get("data") or {}
    _format_ahead_behind(data)
    _format_changed_files(data)

    conflicts = data.get("conflicts") or []
    if conflicts:
        print(f"\n{len(conflicts)} merge conflict(s):")
        for c in conflicts:
            print(f"  ! {c.get('path', 'unknown')}")

    return EXIT_CLEAN


def run_git_commit(client: BifrostClient, message: str) -> int:
    """Regenerate manifest, stage, preflight, commit."""
    result = _post_and_poll(client, "/api/github/commit", label="Committing", json_body={"message": message})

    if result.get("status") != "success":
        error = result.get("error") or "Commit failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    data = result.get("data") or {}
    commit_sha = data.get("commit_sha")
    files_committed = data.get("files_committed", 0)

    if commit_sha:
        print(f"Committed {commit_sha[:7]}")
    else:
        print("Nothing to commit")

    if files_committed:
        print(f"  {files_committed} file(s) committed")

    # Show preflight results
    preflight = data.get("preflight")
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


def run_git_push(client: BifrostClient) -> int:
    """Pull + push + S3 sync + entity import."""
    result = _post_and_poll(client, "/api/github/sync", label="Pushing")

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


def run_git_resolve(client: BifrostClient, resolutions: dict[str, str]) -> int:
    """Resolve merge conflicts."""
    # Map CLI names to API names
    api_resolutions = {
        path: RESOLUTION_MAP[resolution]
        for path, resolution in resolutions.items()
    }

    result = _post_and_poll(
        client, "/api/github/resolve", label="Resolving",
        json_body={"resolutions": api_resolutions},
    )

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
    result = _post_and_poll(
        client, "/api/github/diff", label="Diffing",
        json_body={"path": path},
    )

    if result.get("status") != "success":
        error = result.get("error") or "Diff failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    data = result.get("data") or {}
    diff_text = data.get("diff")
    if diff_text:
        print(diff_text)
    else:
        # Show head vs working content if no unified diff
        head = data.get("head_content")
        working = data.get("working_content")
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
    result = _post_and_poll(
        client, "/api/github/discard", label="Discarding",
        json_body={"paths": paths},
    )

    if result.get("status") != "success":
        error = result.get("error") or "Discard failed"
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    data = result.get("data") or {}
    discarded = data.get("discarded_paths") or []
    if discarded:
        print(f"Discarded changes to {len(discarded)} file(s):")
        for p in discarded:
            print(f"  {p}")
    else:
        print("No changes discarded")

    return EXIT_CLEAN
