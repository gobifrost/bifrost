#!/usr/bin/env python3
"""Require reviewed Product Updates material before rendering a release body.

This has no network dependency; source eligibility uses the local Git graph.
The frozen inventory is the release range, normal ``entries/`` are the only publishable entries, and
``release-review.json`` records the human security and breaking-change review.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.product_updates import _entry_eligible, read_entry, schema_errors


@dataclass(frozen=True)
class GateResult:
    errors: list[str]
    markdown: str


def _load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing required review record: {path}")
        return {}
    except json.JSONDecodeError as error:
        errors.append(f"invalid JSON in {path}: {error.msg}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"expected an object in {path}")
        return {}
    return value


def _entry_metadata(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value, _ = read_entry(path)
    except ValueError as error:
        errors.append(str(error))
        return {}
    return value


def _is_approved(record: Any) -> bool:
    return isinstance(record, dict) and record.get("status") == "approved"


def _covers_pr(entry: dict[str, Any], pr_number: int) -> bool:
    return any(
        isinstance(source, dict) and source.get("pr") == pr_number
        for source in entry.get("sources", [])
    )


def _release_material(review: dict[str, Any], errors: list[str]) -> str:
    if not _is_approved(review.get("review")):
        errors.append("release review must be approved")

    security = review.get("security")
    if not isinstance(security, dict) or not _is_approved(security):
        errors.append("security/CVE review must be approved")
        security = {}
    elif not security.get("evidence"):
        errors.append("security/CVE review must retain evidence")
    fixed_cves = security.get("fixed_cves") if isinstance(security, dict) else None
    if (
        not isinstance(fixed_cves, list)
        or not fixed_cves
        or not all(isinstance(item, str) and item for item in fixed_cves)
    ):
        errors.append(
            "security/CVE review must record fixed_cves, including an explicit none finding"
        )
        fixed_cves = []

    breaking = review.get("breaking_changes")
    if not isinstance(breaking, dict) or not _is_approved(breaking):
        errors.append("breaking-change review must be approved")
        breaking = {}
    elif not breaking.get("evidence"):
        errors.append("breaking-change review must retain evidence")
    summary = breaking.get("summary") if isinstance(breaking, dict) else None
    if not isinstance(summary, str) or not summary:
        errors.append(
            "breaking-change review must record a summary, including an explicit none finding"
        )
        summary = ""

    cve_lines = "\n".join(f"- {item}" for item in fixed_cves)
    return f"## Fixed CVEs\n\n{cve_lines}\n\n## Breaking Changes\n\n{summary}\n"


def validate(content_dir: Path, target: str) -> GateResult:
    """Validate frozen-release review material and return deterministic sections."""
    errors: list[str] = []
    inventory = _load_json(content_dir / "inventory.json", errors)
    dispositions = _load_json(content_dir / "dispositions.json", errors)
    release_review = _load_json(content_dir / "release-review.json", errors)

    # A release cannot proceed without a durable human review record.  Stop
    # here rather than printing one draft error for every historic source.
    if not release_review:
        return GateResult(errors=errors, markdown="")
    schema_path = content_dir / "schema.json"
    if not schema_path.is_file():
        errors.append(f"missing Product Updates schema: {schema_path}")
    else:
        errors.extend(
            schema_errors(
                schema_path,
                "releaseReview",
                release_review,
                str(content_dir / "release-review.json"),
            )
        )

    if inventory.get("target_ref") != target:
        errors.append("release target does not match inventory target_ref")
    if release_review.get("target_ref") != target:
        errors.append("release review target does not match the frozen target")

    entries: dict[str, dict[str, Any]] = {}
    entries_dir = content_dir / "entries"
    if entries_dir.exists():
        for path in sorted(entries_dir.glob("*.md")):
            entry = _entry_metadata(path, errors)
            entry_id = entry.get("id")
            if isinstance(entry_id, str):
                entries[entry_id] = entry

    item_values = dispositions.get("items")
    items = item_values if isinstance(item_values, dict) else {}
    covered_prs = sorted(
        {
            pr
            for commit in inventory.get("commits", [])
            if isinstance(commit, dict)
            for pr in commit.get("prs", [])
            if isinstance(pr, int)
        }
    )
    for pr_number in covered_prs:
        key = f"pr:{pr_number}"
        disposition = items.get(key)
        if not isinstance(disposition, dict):
            errors.append(f"{key}: missing disposition for frozen release coverage")
            continue
        if not _is_approved(disposition.get("review")):
            errors.append(f"{key}: release disposition review must be approved")
            continue
        if (
            disposition.get("security_review", {}).get("status") == "required"
            or disposition.get("action_required") is True
        ) and disposition.get("classification") != "highlight":
            errors.append(
                f"{key}: security or action-required source must use a canonical highlight entry"
            )
            continue
        if disposition.get("classification") != "highlight":
            continue

        entry_ids = disposition.get("entry_ids")
        if not isinstance(entry_ids, list) or not entry_ids:
            errors.append(f"{key}: highlight disposition must name a canonical entry")
            continue
        matched_entries: list[dict[str, Any]] = []
        for entry_id in entry_ids:
            entry = entries.get(entry_id) if isinstance(entry_id, str) else None
            if entry is None:
                errors.append(
                    f"{key}: canonical approved entry {entry_id!r} is missing from entries/"
                )
                continue
            if not _is_approved(entry.get("review")):
                errors.append(
                    f"{key}: canonical entry {entry_id} review must be approved"
                )
                continue
            if not _covers_pr(entry, pr_number):
                errors.append(
                    f"{key}: canonical entry {entry_id} must cite this PR as a source"
                )
                continue
            if _entry_eligible(entry, target, inventory):
                matched_entries.append(entry)

        if disposition.get("security_review", {}).get(
            "status"
        ) == "required" and not any(
            entry.get("security_review", {}).get("status") == "required"
            for entry in matched_entries
        ):
            errors.append(
                f"{key}: required security review needs a canonical security-reviewed entry"
            )
        if disposition.get("action_required") is True and not any(
            entry.get("action_required") is True for entry in matched_entries
        ):
            errors.append(
                f"{key}: action-required source needs a canonical action-required entry"
            )

    for commit in inventory.get("commits", []):
        if not isinstance(commit, dict) or commit.get("prs"):
            continue
        sha = commit.get("sha")
        if not isinstance(sha, str):
            errors.append("frozen inventory has a direct commit without a SHA")
            continue
        key = f"commit:{sha}"
        disposition = items.get(key)
        if not isinstance(disposition, dict):
            errors.append(
                f"{key}: missing disposition for frozen direct-commit coverage"
            )
        elif not _is_approved(disposition.get("review")):
            errors.append(f"{key}: release disposition review must be approved")

    material = _release_material(release_review, errors)
    return GateResult(errors=errors, markdown=material)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Require reviewed Product Updates material for a release body."
    )
    parser.add_argument("--content-dir", required=True, type=Path)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = validate(args.content_dir, args.target)
    if result.errors:
        for error in result.errors:
            print(f"release material gate: {error}", file=sys.stderr)
        return 1
    args.output.write_text(result.markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
