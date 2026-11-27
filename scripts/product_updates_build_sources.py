"""Collect and check Product Updates sources for a protected merge-group build."""

import argparse
import json
import subprocess
from pathlib import Path

from scripts.product_updates import (
    SHA,
    collect_inventory,
    read_json,
    retain_contribution_evidence,
    schema_errors,
)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def require_ancestor(base: str, target: str) -> None:
    if not SHA.fullmatch(base) or not SHA.fullmatch(target):
        raise ValueError("Product Updates provenance requires full commit SHAs")
    if subprocess.run(["git", "merge-base", "--is-ancestor", base, target]).returncode:
        raise ValueError(
            f"Product Updates source {base} is not an ancestor of {target}"
        )


def resolve_build_inventory(
    metadata: dict, candidate_inventory: dict, target: str, schema: Path
) -> dict:
    group = metadata["merge_group"]
    base = group["base_sha"]
    if (
        group["head_sha"] != target
        or group["base_ref"] != "refs/heads/main"
        or base == target
    ):
        raise ValueError(
            "Product Updates metadata does not describe this main candidate"
        )
    require_ancestor(base, target)

    # The candidate can refresh the frozen ledger, but cannot provide its own
    # history or queued membership to the image builder.
    frozen = json.loads(git("show", f"{base}:product-updates/inventory.json"))
    require_ancestor(frozen["target_ref"], base)
    delta = metadata["landed"]
    errors = schema_errors(schema, "inventory", delta, "landed Product Updates sources")
    expected_commits = git("rev-list", f"{frozen['target_ref']}..{base}").splitlines()
    commits = delta["commits"]
    if errors:
        raise ValueError("\n".join(errors))
    if (
        delta["base_ref"] != frozen["target_ref"]
        or delta["target_ref"] != base
        or delta["repository"] != frozen["repository"]
        or sorted(row["sha"] for row in commits) != sorted(expected_commits)
    ):
        raise ValueError(
            "Product Updates landed sources do not cover the exact main interval"
        )

    landed = {row["number"]: row for row in delta["prs"]}
    associations = {
        (number, commit["sha"]) for commit in commits for number in commit["prs"]
    }
    if len(landed) != len(delta["prs"]) or associations != {
        (number, row["merge_commit"]) for number, row in landed.items()
    }:
        raise ValueError(
            "Product Updates landed PRs lack exact merge-commit associations"
        )

    queued = metadata["queued"]
    queue_logins = {
        row["number"]: row["user"]["login"] for row in group["pull_requests"]
    }
    if (
        not queued
        or len(queue_logins) != len(queued)
        or queue_logins != {row["number"]: row["author"]["login"] for row in queued}
        or len(queue_logins) != len(group["pull_requests"])
    ):
        raise ValueError(
            "Product Updates queue sources differ from the verified merge group"
        )

    all_landed = {row["number"]: row for row in frozen["prs"]}
    if (
        all_landed.keys() & landed.keys()
        or (all_landed.keys() | landed.keys()) & queue_logins.keys()
    ):
        raise ValueError(
            "Product Updates sources overlap between history and the queue"
        )
    all_landed.update(landed)
    # Apply reviewed credit only to an independently verified landed PR and SHA.
    # Keep copies so the frozen metadata and candidate evidence are never mutated.
    credited = {"prs": [dict(row) for row in all_landed.values()]}
    retain_contribution_evidence(credited, candidate_inventory)

    # A queued PR is eligible on the exact candidate only. Its effective
    # revision is used in memory and is never persisted as a landed merge.
    return {
        **frozen,
        "prs": credited["prs"] + [{**row, "merge_commit": target} for row in queued],
        "commits": frozen["commits"]
        + commits
        + [
            {
                "sha": target,
                "subject": git("show", "-s", "--format=%s", target),
                "prs": list(queue_logins),
            }
        ],
    }


def collect(event: dict, repository: str, target: str) -> dict:
    group = event["merge_group"]
    base = group["base_sha"]
    if (
        group["head_sha"] != target
        or group["base_ref"] != "refs/heads/main"
        or base == target
    ):
        raise ValueError(
            "Product Updates metadata does not describe this main candidate"
        )
    require_ancestor(base, target)
    frozen = json.loads(git("show", f"{base}:product-updates/inventory.json"))
    if frozen["repository"] != repository:
        raise ValueError("Product Updates metadata belongs to another repository")
    require_ancestor(frozen["target_ref"], base)
    landed = collect_inventory(
        frozen["target_ref"], base, repository, landed_branch="main"
    )
    queued = []
    for row in group["pull_requests"]:
        source = f"repos/{repository}/pulls/{row['number']}"
        pr = json.loads(subprocess.check_output(["gh", "api", source], text=True))
        if (
            pr["number"] != row["number"]
            or pr["base"]["ref"] != "main"
            or pr["base"]["repo"]["full_name"] != repository
            or pr["user"]["login"] != row["user"]["login"]
            or pr["state"] != "open"
        ):
            raise ValueError(
                f"Product Updates queue PR {row['number']} changed during collection"
            )
        queued.append(
            {
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["html_url"],
                "author": {"login": pr["user"]["login"], "type": pr["user"]["type"]},
            }
        )
    return {"merge_group": group, "landed": landed, "queued": queued}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata = collect(read_json(args.event), args.repository, args.target)
    args.output.write_text(json.dumps(metadata) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
