#!/usr/bin/env python3
"""Validate and render offline product updates; collection is explicit only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker


AREAS = {
    "Agents",
    "Apps & Forms",
    "Workflows",
    "Integrations",
    "Administration",
    "Platform",
    "Developer Tools",
}
TYPES = {"New", "Improved", "Fixed", "Security"}
SHA = re.compile(r"^[0-9a-f]{40}$")
RAW_HTML = re.compile(r"<[A-Za-z][^>]*>")
REMOTE_IMAGE = re.compile(r"!\[[^]]*\]\(https?://", re.I)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object")
    return value


def read_entry(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---json\n"):
        raise ValueError(f"{path}: first line must be ---json")
    try:
        frontmatter, markdown = text[len("---json\n") :].split("\n---\n", 1)
        metadata = json.loads(frontmatter)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid JSON frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"{path}: frontmatter must be an object")
    return metadata, markdown.rstrip("\n")


def schema_errors(
    schema_path: Path, definition: str, value: dict[str, Any], label: str
) -> list[str]:
    """Use the checked-in JSON Schema for every persisted source object."""
    schema = read_json(schema_path)

    def refs(node: Any) -> list[str]:
        if isinstance(node, dict):
            return ([node["$ref"]] if "$ref" in node else []) + [
                ref for child in node.values() for ref in refs(child)
            ]
        if isinstance(node, list):
            return [ref for child in node for ref in refs(child)]
        return []

    if any(not ref.startswith("#/") for ref in refs(schema)):
        return [f"{schema_path}: external $ref values are forbidden"]
    wrapper = {
        "$schema": schema["$schema"],
        "$defs": schema["$defs"],
        "$ref": f"#/$defs/{definition}",
    }
    validator = Draft202012Validator(wrapper, format_checker=FormatChecker())
    errors = []
    for error in sorted(
        validator.iter_errors(value), key=lambda item: list(item.absolute_path)
    ):
        path = ".".join(str(part) for part in error.absolute_path) or "root"
        errors.append(f"{label}: {path}: {error.message}")
    return errors


def entry_paths(content_dir: Path, draft_dir: Path | None = None) -> list[Path]:
    paths = sorted((content_dir / "entries").glob("*.md"))
    if draft_dir:
        paths.extend(sorted((draft_dir / "entries").glob("*.md")))
    return paths


def _url(value: Any) -> bool:
    parsed = urlparse(value) if isinstance(value, str) else None
    return bool(parsed and parsed.scheme == "https" and parsed.netloc)


def unsafe_markdown(markdown: str) -> bool:
    prose = re.sub(r"`[^`]*`", "", markdown)
    return bool(RAW_HTML.search(prose) or REMOTE_IMAGE.search(prose))


def markdown_asset_errors(entry: dict[str, Any], label: str) -> list[str]:
    assets = entry.get("assets", [])
    errors: list[str] = []
    declared_assets = (
        {asset.get("path") for asset in assets if isinstance(asset, dict)}
        if isinstance(assets, list)
        else set()
    )
    for image_path in re.findall(r"!\[[^]]*\]\(([^)\s]+)\)", entry["markdown"]):
        if image_path not in declared_assets:
            errors.append(
                f"{label}: Markdown image must reference a declared local asset: {image_path}"
            )
    return errors


def _review_errors(value: Any, label: str, allow_draft: bool) -> list[str]:
    if not isinstance(value, dict) or value.get("status") not in {"draft", "approved"}:
        return [f"{label}: review.status must be draft or approved"]
    if not isinstance(value.get("evidence"), list) or not value["evidence"]:
        return [f"{label}: review.evidence is required"]
    if value["status"] == "draft" and not allow_draft:
        return [f"{label}: draft review is not eligible"]
    return []


def _security_errors(value: Any, label: str, required: bool) -> list[str]:
    if not isinstance(value, dict) or value.get("status") not in {
        "required",
        "not_required",
    }:
        return [f"{label}: security_review.status must be required or not_required"]
    if required and value.get("status") != "required":
        return [f"{label}: Security entries require explicit review"]
    if value.get("status") == "required" and not _url(value.get("review_ref")):
        return [f"{label}: required security review needs an https review_ref"]
    return []


def load_entries(
    content_dir: Path, draft_dir: Path | None = None
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in entry_paths(content_dir, draft_dir):
        metadata, markdown = read_entry(path)
        metadata = {**metadata, "markdown": markdown, "_path": str(path)}
        entries.append(metadata)
    return entries


def _asset_error(content_dir: Path, asset: dict[str, Any]) -> str | None:
    raw = asset.get("path")
    if not isinstance(raw, str):
        return "asset path is required"
    relative = PurePosixPath(raw)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or not raw.startswith("assets/")
        or relative.suffix.lower() not in IMAGE_EXTENSIONS
        or ":" in raw
    ):
        return f"unsafe asset path {raw!r}"
    resolved = content_dir / Path(*relative.parts)
    assets_root = (content_dir / "assets").resolve()
    if (
        not resolved.is_file()
        or resolved.is_symlink()
        or any(
            parent.is_symlink()
            for parent in [resolved, *resolved.parents]
            if parent != content_dir.parent
        )
        or not resolved.resolve().is_relative_to(assets_root)
    ):
        return f"asset is missing or not a regular file: {raw}"
    return None


def _metadata_indexes(
    inventory: dict[str, Any],
) -> tuple[dict[int, dict[str, Any]], set[str]]:
    prs = {
        item["number"]: item
        for item in inventory.get("prs", [])
        if isinstance(item, dict) and isinstance(item.get("number"), int)
    }
    commits = {
        item["sha"]
        for item in inventory.get("commits", [])
        if isinstance(item, dict) and isinstance(item.get("sha"), str)
    }
    return prs, commits


def _reachable(commit: str, target: str, inventory: dict[str, Any]) -> bool:
    """Use Git ancestry when available; the frozen inventory is a full-target fallback."""
    if target == inventory.get("target_ref"):
        return True
    if commit == inventory.get("base_ref"):
        return False
    try:
        return (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", commit, target],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    except OSError:
        return False


def _in_interval(
    commit: str, base: str, target: str, inventory: dict[str, Any]
) -> bool:
    if commit == base or not _reachable(commit, target, inventory):
        return False
    if base == inventory.get("base_ref") and target == inventory.get("target_ref"):
        return True
    try:
        return (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", base, commit],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )
    except OSError as exc:
        raise ValueError("Git is required to evaluate a release subrange") from exc


def validate_entry(
    entry: dict[str, Any],
    content_dir: Path,
    prs: dict[int, dict[str, Any]],
    commits: set[str],
    allow_draft: bool,
    commit_prs: dict[str, set[int]] | None = None,
    allow_pending_sources: bool = False,
) -> list[str]:
    label = entry.get("_path", "entry")
    errors: list[str] = []
    required = {
        "id",
        "revision",
        "published_at",
        "title",
        "visibility",
        "area",
        "type",
        "action_required",
        "security_review",
        "sources",
        "eligibility",
        "assets",
        "review",
        "markdown",
    }
    missing = sorted(required - entry.keys())
    if missing:
        return [f"{label}: missing {', '.join(missing)}"]
    try:
        parsed_id = uuid.UUID(entry["id"])
        if str(parsed_id) != entry["id"]:
            errors.append(f"{label}: id must be canonical UUID")
    except (ValueError, TypeError):
        errors.append(f"{label}: id must be a UUID")
    if not isinstance(entry["revision"], int) or entry["revision"] < 1:
        errors.append(f"{label}: revision must be a positive integer")
    if not isinstance(entry["published_at"], str) or not entry["published_at"].endswith(
        "Z"
    ):
        errors.append(f"{label}: published_at must be an ISO-8601 UTC timestamp")
    else:
        try:
            datetime.fromisoformat(entry["published_at"].replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"{label}: published_at must be an ISO-8601 UTC timestamp")
    if not isinstance(entry["title"], str) or not entry["title"].strip():
        errors.append(f"{label}: title is required")
    if entry["visibility"] != "highlight":
        errors.append(f"{label}: entry visibility must be highlight")
    if (
        entry["area"] not in AREAS
        or entry["type"] not in TYPES
        or not isinstance(entry["action_required"], bool)
    ):
        errors.append(f"{label}: invalid area, type, or action_required")
    staged = (
        bool(entry.get("eligibility", {}).get("staged", False))
        if isinstance(entry.get("eligibility"), dict)
        else False
    )
    errors.extend(_review_errors(entry["review"], label, allow_draft))
    errors.extend(
        _security_errors(entry["security_review"], label, entry["type"] == "Security")
    )
    sources = entry["sources"]
    if not isinstance(sources, list) or (not sources and not staged):
        errors.append(f"{label}: non-staged entries require at least one source")
        sources = []
    source_prs: set[int] = set()
    for source in sources:
        if not isinstance(source, dict) or (
            source.get("pr") is None and source.get("commit") is None
        ):
            errors.append(
                f"{label}: source must reference a cached PR or direct commit"
            )
            continue
        if (
            source.get("pr") is not None
            and source["pr"] not in prs
            and not allow_pending_sources
        ):
            errors.append(f"{label}: source PR must be in the cached range")
        elif source.get("pr") is not None:
            source_prs.add(source["pr"])
        commit = source.get("commit")
        if commit is not None and (
            not isinstance(commit, str)
            or (commit not in commits and not allow_pending_sources)
        ):
            errors.append(f"{label}: source commit must be in the cached range")
        elif (
            commit is not None
            and commit in commits
            and source.get("pr") in prs
            and commit_prs is not None
            and source["pr"] not in commit_prs.get(commit, set())
        ):
            errors.append(
                f"{label}: source PR #{source['pr']} is not associated with commit {commit}"
            )
    eligibility = entry["eligibility"]
    if (
        not isinstance(eligibility, dict)
        or not isinstance(eligibility.get("requires_prs"), list)
        or not isinstance(eligibility.get("requires_commits"), list)
    ):
        errors.append(
            f"{label}: eligibility requires requires_prs and requires_commits lists"
        )
    else:
        for pr in eligibility["requires_prs"]:
            if pr not in prs and not allow_pending_sources:
                errors.append(f"{label}: requires PR #{pr} not landed in cached target")
        for commit in eligibility["requires_commits"]:
            if commit not in commits and not allow_pending_sources:
                errors.append(
                    f"{label}: requires commit {commit} not landed in cached target"
                )
        if staged and (
            sources or eligibility["requires_prs"] or eligibility["requires_commits"]
        ):
            errors.append(f"{label}: staged entries cannot claim landed sources")
        if staged and entry.get("review", {}).get("status") != "draft":
            errors.append(f"{label}: staged entries must remain draft")
    prose = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", entry["markdown"])
    prose = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", prose)
    word_limit = 100 if entry["action_required"] or entry["type"] == "Security" else 60
    if len(prose.split()) > word_limit:
        errors.append(
            f"{label}: update prose exceeds {word_limit} words; shorten it or link to details"
        )
    if len(entry["title"].split()) > 10:
        errors.append(f"{label}: update title exceeds 10 words")
    if unsafe_markdown(entry["markdown"]):
        errors.append(f"{label}: raw HTML or executable embeds are not allowed")
    assets = entry["assets"]
    if not isinstance(assets, list):
        errors.append(f"{label}: assets must be a list")
    else:
        for asset in assets:
            if (
                not isinstance(asset, dict)
                or not isinstance(asset.get("alt"), str)
                or not asset["alt"].strip()
            ):
                errors.append(f"{label}: every asset needs alt text")
                continue
            error = _asset_error(content_dir, asset)
            if error:
                errors.append(f"{label}: {error}")
    errors.extend(markdown_asset_errors(entry, label))
    for contributor in entry.get("contributors", []):
        if (
            not isinstance(contributor, dict)
            or contributor.get("source_pr") not in source_prs
            or contributor.get("profile_url")
            != f"https://github.com/{contributor.get('login')}"
        ):
            errors.append(
                f"{label}: contributor must have a source PR and https profile URL"
            )
            continue
        if contributor["source_pr"] not in prs:
            continue
        pr = prs[contributor["source_pr"]]
        allowed = {pr.get("author", {}).get("login")}
        allowed.update(
            c.get("login") for c in pr.get("contributions", []) if isinstance(c, dict)
        )
        if contributor.get("login") not in allowed:
            errors.append(
                f"{label}: contributor {contributor.get('login')!r} is not verified by cached metadata"
            )
    return errors


def coverage_report(
    inventory: dict[str, Any],
    dispositions: dict[str, Any],
    entries: list[dict[str, Any]],
) -> dict[str, list[str]]:
    items = (
        dispositions.get("items", {})
        if isinstance(dispositions.get("items"), dict)
        else {}
    )
    entry_ids = {entry.get("id") for entry in entries}
    unclassified: list[str] = []
    uncovered: list[str] = []
    unrepresented: list[str] = []
    for commit in inventory.get("commits", []):
        if not isinstance(commit, dict):
            continue
        keys = [f"pr:{pr}" for pr in commit.get("prs", [])] or [
            f"commit:{commit.get('sha')}"
        ]
        missing_keys = [key for key in keys if key not in items]
        if missing_keys:
            unclassified.extend(missing_keys)
            uncovered.append(str(commit.get("sha")))
    for key, disposition in items.items():
        if not isinstance(disposition, dict):
            continue
        if disposition.get("classification") == "highlight":
            for entry_id in disposition.get("entry_ids", []):
                if entry_id not in entry_ids:
                    unrepresented.append(f"{key}:{entry_id}")
    return {
        "unclassified": sorted(unclassified),
        "unrepresented_highlights": sorted(unrepresented),
        "uncovered_commits": sorted(uncovered),
    }


def validate_content(
    content_dir: Path,
    inventory_path: Path,
    dispositions_path: Path,
    target: str,
    *,
    schema_path: Path | None = None,
    base: str | None = None,
    draft_dir: Path | None = None,
    allow_draft: bool = False,
) -> list[str]:
    errors: list[str] = []
    try:
        inventory = read_json(inventory_path)
        dispositions = read_json(dispositions_path)
    except ValueError as exc:
        return [str(exc)]
    schema_path = schema_path or content_dir / "schema.json"
    errors.extend(
        schema_errors(schema_path, "inventory", inventory, str(inventory_path))
    )
    errors.extend(
        schema_errors(schema_path, "dispositions", dispositions, str(dispositions_path))
    )
    known_commits = {
        item.get("sha")
        for item in inventory.get("commits", [])
        if isinstance(item, dict)
    }
    if not SHA.fullmatch(target):
        errors.append("target must be a 40-character commit SHA")
    if (
        base is not None
        and base != inventory.get("base_ref")
        and base not in known_commits
    ):
        errors.append(
            "base must be the frozen inventory base_ref or a cached commit in that range"
        )
    if (
        target != inventory.get("target_ref")
        and target not in known_commits
        and not _reachable(str(inventory.get("target_ref", "")), target, inventory)
    ):
        errors.append(
            "target must be a cached commit or a verified descendant of the frozen inventory target_ref"
        )
    if dispositions.get("inventory") != {
        "base_ref": inventory.get("base_ref"),
        "target_ref": inventory.get("target_ref"),
    }:
        errors.append("dispositions inventory range does not match inventory.json")
    prs, commits = _metadata_indexes(inventory)
    commit_prs = {
        item["sha"]: set(item.get("prs", []))
        for item in inventory.get("commits", [])
        if isinstance(item, dict) and isinstance(item.get("sha"), str)
    }
    try:
        entries = load_entries(content_dir, draft_dir)
    except ValueError as exc:
        return errors + [str(exc)]
    seen: set[str] = set()
    for entry in entries:
        schema_value = {
            key: value
            for key, value in entry.items()
            if key not in {"markdown", "_path"}
        }
        errors.extend(
            schema_errors(schema_path, "entry", schema_value, str(entry["_path"]))
        )
        errors.extend(
            validate_entry(
                entry,
                content_dir,
                prs,
                commits,
                allow_draft,
                commit_prs,
                allow_pending_sources=True,
            )
        )
        entry_id = entry.get("id")
        if entry_id in seen:
            errors.append(f"duplicate entry id {entry_id}")
        if isinstance(entry_id, str):
            seen.add(entry_id)
    report = coverage_report(inventory, dispositions, entries)
    errors.extend(
        f"unclassified landed source {item}" for item in report["unclassified"]
    )
    for item in report["unrepresented_highlights"] if draft_dir is not None else []:
        source = item.split(":", 2)[:2]
        disposition = dispositions.get("items", {}).get(":".join(source), {})
        if allow_draft or disposition.get("review", {}).get("status") == "approved":
            errors.append(f"highlight disposition has no entry {item}")
    return errors


def _entry_eligible(
    entry: dict[str, Any], target: str, inventory: dict[str, Any]
) -> bool:
    eligibility = entry["eligibility"]
    prs, commits = _metadata_indexes(inventory)
    required_prs = set(eligibility.get("requires_prs", []))
    required_commits = set(eligibility.get("requires_commits", []))
    for source in entry.get("sources", []):
        if source.get("pr") is not None:
            required_prs.add(source["pr"])
        if source.get("commit") is not None:
            required_commits.add(source["commit"])
    if not required_prs.issubset(prs) or not required_commits.issubset(commits):
        return False
    required_commits.update(prs[pr]["merge_commit"] for pr in required_prs)
    return all(_reachable(commit, target, inventory) for commit in required_commits)


def _contributors(
    entry: dict[str, Any], prs: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for source in entry.get("sources", []):
        pr = prs.get(source.get("pr"), {})
        author = pr.get("author", {})
        login = author.get("login")
        if login:
            found.append(
                {
                    "login": login,
                    "profile_url": f"https://github.com/{login}",
                    "source_pr": source["pr"],
                    "account_type": author.get("type", "User"),
                }
            )
        for contribution in pr.get("contributions", []):
            if isinstance(contribution, dict) and contribution.get("login"):
                found.append(
                    {
                        key: contribution[key]
                        for key in ("login", "profile_url", "source_pr", "role")
                        if key in contribution
                    }
                )
    for contributor in entry.get("contributors", []):
        if isinstance(contributor, dict) and contributor.get("login"):
            found.append(
                {
                    key: contributor[key]
                    for key in ("login", "profile_url", "source_pr", "role")
                    if key in contributor
                }
            )
    unique = {
        (item["login"].lower(), item.get("source_pr"), item.get("role", "")): item
        for item in found
    }
    return sorted(
        unique.values(),
        key=lambda item: (
            item["login"].lower(),
            item.get("source_pr", 0),
            item.get("role", ""),
        ),
    )


def build_bundle(
    content_dir: Path,
    inventory_path: Path,
    dispositions_path: Path,
    target: str,
    asset_base_url: str,
    *,
    schema_path: Path | None = None,
    base: str | None = None,
    content_ref: str | None = None,
    draft_dir: Path | None = None,
    allow_draft: bool = False,
    include_staged: bool = False,
) -> dict[str, Any]:
    errors = validate_content(
        content_dir,
        inventory_path,
        dispositions_path,
        target,
        schema_path=schema_path,
        base=base,
        draft_dir=draft_dir,
        allow_draft=allow_draft,
    )
    if errors:
        raise ValueError("\n".join(errors))
    if not asset_base_url.endswith("/") or (
        not asset_base_url.startswith("/") and not _url(asset_base_url)
    ):
        raise ValueError(
            "asset_base_url must be an absolute site path or https URL ending in /"
        )
    content_ref = content_ref or target
    if not SHA.fullmatch(content_ref):
        raise ValueError("content_ref must be a 40-character commit SHA")
    inventory = read_json(inventory_path)
    base = base or str(inventory["base_ref"])
    dispositions = read_json(dispositions_path)
    prs, _ = _metadata_indexes(inventory)
    entries: list[dict[str, Any]] = []
    for entry in load_entries(content_dir, draft_dir):
        staged = entry["eligibility"].get("staged", False)
        if staged and not include_staged:
            continue
        if entry["review"]["status"] != "approved" and not allow_draft:
            continue
        if not _entry_eligible(entry, target, inventory):
            continue
        source_keys = {
            f"{kind}:{source[kind]}"
            for source in entry["sources"]
            for kind in ("pr", "commit")
            if source.get(kind) is not None
            and (kind == "pr" or source.get("pr") is None)
        }
        if not staged and any(
            dispositions["items"].get(key, {}).get("classification") != "highlight"
            or entry["id"]
            not in dispositions["items"].get(key, {}).get("entry_ids", [])
            or (
                not allow_draft
                and dispositions["items"].get(key, {}).get("review", {}).get("status")
                != "approved"
            )
            for key in source_keys
        ):
            continue
        source_commits = [
            source.get("commit") or prs.get(source.get("pr"), {}).get("merge_commit")
            for source in entry["sources"]
        ]
        if not staged and not any(
            isinstance(commit, str) and _in_interval(commit, base, target, inventory)
            for commit in source_commits
        ):
            continue
        output = {
            key: value
            for key, value in entry.items()
            if key
            not in {"_path", "eligibility", "review", "security_review", "visibility"}
        }
        output["contributors"] = _external_contributors(_contributors(entry, prs))
        output["assets"] = [
            {**asset, "url": f"{asset_base_url}{asset['path']}"}
            for asset in entry["assets"]
        ]
        entries.append(output)
    entries.sort(key=lambda entry: (entry["published_at"], entry["id"]), reverse=True)
    represented = {
        f"{kind}:{source[kind]}"
        for entry in entries
        for source in entry["sources"]
        for kind in ("pr", "commit")
        if source.get(kind) is not None
    }
    other: list[dict[str, Any]] = []
    for key, item in dispositions.get("items", {}).items():
        if (
            not isinstance(item, dict)
            or item.get("classification") == "omit"
            or key in represented
        ):
            continue
        if item.get("review", {}).get("status") != "approved" and not allow_draft:
            continue
        if key.startswith("commit:"):
            commit_sha = key.removeprefix("commit:")
            commit = next(
                (row for row in inventory["commits"] if row["sha"] == commit_sha), None
            )
            if commit and _in_interval(commit_sha, base, target, inventory):
                other.append(
                    {
                        "source": key,
                        "title": item.get("summary") or commit["subject"],
                        "url": f"https://github.com/gobifrost/bifrost/commit/{commit_sha}",
                        "contributors": [],
                        **(
                            {"category": item["category"]} if "category" in item else {}
                        ),
                    }
                )
            continue
        if not key.startswith("pr:"):
            continue
        pr = prs.get(int(key[3:]))
        if pr and _in_interval(pr["merge_commit"], base, target, inventory):
            other.append(
                {
                    "source": key,
                    "title": item.get("summary") or pr["title"],
                    "url": pr["url"],
                    **({"category": item["category"]} if "category" in item else {}),
                    "contributors": _external_contributors(
                        _contributors({"sources": [{"pr": pr["number"]}]}, prs)
                    ),
                }
            )
    other.sort(key=lambda item: (item["title"].lower(), item["source"]))
    result = {
        "schema_version": 1,
        "target_ref": target,
        "content_ref": content_ref,
        "entries": entries,
        "other_changes": other,
    }
    output_errors = schema_errors(
        schema_path or content_dir / "schema.json", "bundle", result, "bundle"
    )
    if output_errors:
        raise ValueError("\n".join(output_errors))
    return result


def _external_contributors(contributors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in c.items() if key != "account_type"}
        for c in contributors
        if c.get("login", "").lower() != "jackmusick"
        and not c.get("login", "").lower().endswith("[bot]")
        and c.get("login", "").lower() != "dependabot"
        and c.get("account_type") != "Bot"
    ]


def render_release(bundle: dict[str, Any], asset_base_url: str) -> str:
    if not SHA.fullmatch(str(bundle.get("target_ref", ""))):
        raise ValueError("bundle target_ref must be a 40-character SHA")
    content_ref = bundle.get("content_ref", bundle["target_ref"])
    if not SHA.fullmatch(str(content_ref)):
        raise ValueError("bundle content_ref must be a 40-character SHA")
    expected = f"https://raw.githubusercontent.com/gobifrost/bifrost/{content_ref}/"
    if asset_base_url != expected:
        raise ValueError(
            "release asset_base_url must be the pinned GitHub raw URL for bundle target_ref"
        )
    sections: dict[str, list[dict[str, Any]]] = {
        "Security": [],
        "Action Required": [],
        "What's New": [],
    }
    for entry in bundle.get("entries", []):
        section = (
            "Security"
            if entry.get("type") == "Security"
            else "Action Required"
            if entry.get("action_required")
            else "What's New"
        )
        sections[section].append(entry)
    lines = ["# Bifrost release notes", ""]
    credits: dict[str, dict[str, Any]] = {}
    for title in ("Security", "Action Required", "What's New"):
        if not sections[title]:
            continue
        lines.extend([f"## {title}", ""])
        for entry in sections[title]:
            # Installation-relative app destinations have no universal GitHub URL.
            markdown = re.sub(
                r"(?<!!)\[([^\]]+)\]\((/[^)\s]+)\)",
                r"**\1** (in Bifrost)",
                entry["markdown"],
            )
            inline_assets: set[str] = set()
            for asset in entry.get("assets", []):
                marker = f"]({asset['path']})"
                if marker in markdown:
                    inline_assets.add(asset["path"])
                markdown = markdown.replace(
                    marker, f"]({asset_base_url}product-updates/{asset['path']})"
                )
            lines.extend([f"### {entry['title']}", "", markdown, ""])
            for asset in entry.get("assets", []):
                if asset["path"] in inline_assets:
                    if asset.get("caption"):
                        lines.extend([asset["caption"], ""])
                    continue
                url = f"{asset_base_url}product-updates/{asset['path']}"
                lines.extend([f"![{asset['alt']}]({url})", ""])
                if asset.get("caption"):
                    lines.extend([asset["caption"], ""])
            source_links = [
                f"[#{source['pr']}](https://github.com/gobifrost/bifrost/pull/{source['pr']})"
                if "pr" in source
                else f"[`{source['commit'][:12]}`](https://github.com/gobifrost/bifrost/commit/{source['commit']})"
                for source in entry.get("sources", [])
            ]
            if source_links:
                lines.extend([f"Sources: {', '.join(source_links)}", ""])
            people = _external_contributors(entry.get("contributors", []))
            if people:
                lines.extend(
                    [
                        "Credits: "
                        + ", ".join(
                            f"[{p['login']}]({p['profile_url']})"
                            + (f" (#{p['source_pr']})" if p.get("source_pr") else "")
                            for p in people
                        ),
                        "",
                    ]
                )
            for contributor in people:
                credits[contributor["login"]] = contributor
    if bundle.get("other_changes"):
        lines.extend(["## Other changes", ""])
        for other in bundle["other_changes"]:
            people = _external_contributors(other.get("contributors", []))
            suffix = ""
            if people:
                suffix = " — " + ", ".join(
                    f"[{p['login']}]({p['profile_url']})" for p in people
                )
            lines.append(f"- [{other['title']}]({other['url']}){suffix}")
            for contributor in people:
                credits[contributor["login"]] = contributor
        lines.append("")
    if credits:
        lines.extend(
            [
                "## Contributors",
                "",
                ", ".join(
                    f"[{c['login']}]({c['profile_url']})"
                    for _, c in sorted(
                        credits.items(), key=lambda item: item[0].lower()
                    )
                ),
                "",
            ]
        )
    return "\n".join(lines)


def validate_event(
    event: dict[str, Any],
    dispositions: dict[str, Any],
    entries: list[dict[str, Any]] | None = None,
    inventory: dict[str, Any] | None = None,
) -> list[str]:
    items = (
        dispositions.get("items", {})
        if isinstance(dispositions.get("items"), dict)
        else {}
    )
    numbers: list[int] = []
    authors: dict[int, str] = {}
    if isinstance(event.get("pull_request"), dict) and isinstance(
        event["pull_request"].get("number"), int
    ):
        numbers = [event["pull_request"]["number"]]
        login = event["pull_request"].get("user", {}).get("login")
        if isinstance(login, str):
            authors[numbers[0]] = login
    elif isinstance(event.get("merge_group"), dict):
        source = event["merge_group"].get("pull_requests")
        if not isinstance(source, list) or not source:
            return [
                "merge_group event must include normalized merge_group.pull_requests"
            ]
        for item in source:
            number = item.get("number") if isinstance(item, dict) else item
            if not isinstance(number, int):
                return ["merge_group.pull_requests must contain PR numbers"]
            numbers.append(number)
            if isinstance(item, dict) and isinstance(
                item.get("user", {}).get("login"), str
            ):
                authors[number] = item["user"]["login"]
    else:
        return [
            "event must contain pull_request.number or normalized merge_group.pull_requests"
        ]
    cached_prs, cached_commits = (
        _metadata_indexes(inventory) if inventory else ({}, set())
    )
    require_approved = "merge_group" in event or bool(
        event.get("pull_request", {}).get("merged")
    )
    errors: list[str] = []
    for number in sorted(set(numbers)):
        item = items.get(f"pr:{number}")
        if not isinstance(item, dict):
            errors.append(f"missing canonical disposition for pr:{number}")
            continue
        if item.get("classification") not in {"highlight", "other", "omit"}:
            errors.append(f"pr:{number}: invalid canonical classification")
        if item.get("classification") == "omit" and not item.get("reason"):
            errors.append(f"pr:{number}: omit requires a durable reason")
        errors.extend(
            _review_errors(
                item.get("review"), f"pr:{number}", allow_draft=not require_approved
            )
        )
        if item.get("security_review", {}).get("status") == "required" and not item[
            "security_review"
        ].get("review_ref"):
            errors.append(f"pr:{number}: required security review needs review_ref")
        if entries is not None and item.get("classification") == "highlight":
            if number not in authors:
                errors.append(f"pr:{number}: verified event author is missing")
            by_id = {entry.get("id"): entry for entry in entries}
            for entry_id in item.get("entry_ids", []):
                entry = by_id.get(entry_id)
                if not entry:
                    errors.append(f"pr:{number}: highlight entry {entry_id} is missing")
                    continue
                errors.extend(
                    _review_errors(
                        entry.get("review"),
                        f"entry {entry_id}",
                        allow_draft=not require_approved,
                    )
                )
                errors.extend(
                    _security_errors(
                        entry.get("security_review"),
                        f"entry {entry_id}",
                        entry.get("type") == "Security",
                    )
                )
                if (
                    item.get("security_review", {}).get("status") == "required"
                    and entry.get("security_review", {}).get("status") != "required"
                ):
                    errors.append(
                        f"pr:{number}: required security material is absent from entry {entry_id}"
                    )
                if item.get("action_required") and not entry.get("action_required"):
                    errors.append(
                        f"pr:{number}: action-required material is absent from entry {entry_id}"
                    )
                if not any(
                    source.get("pr") == number for source in entry.get("sources", [])
                ):
                    errors.append(
                        f"pr:{number}: highlight entry {entry_id} does not cite the active PR"
                    )
                if inventory is not None:
                    for source in entry.get("sources", []):
                        if (
                            source.get("pr") is not None
                            and source["pr"] not in numbers
                            and source["pr"] not in cached_prs
                        ):
                            errors.append(
                                f"entry {entry_id}: source PR #{source['pr']} is not verified by the event or cached inventory"
                            )
                        if (
                            source.get("commit") is not None
                            and source["commit"] not in cached_commits
                        ):
                            errors.append(
                                f"entry {entry_id}: source commit is absent from verified inventory"
                            )
                source_prs = {source.get("pr") for source in entry.get("sources", [])}
                for contributor in entry.get("contributors", []):
                    if (
                        contributor.get("source_pr") not in source_prs
                        or contributor.get("profile_url")
                        != f"https://github.com/{contributor.get('login')}"
                    ):
                        errors.append(
                            f"entry {entry_id}: contributor profile/source association is invalid"
                        )
                if number in authors and not any(
                    c.get("source_pr") == number and c.get("login") == authors[number]
                    for c in entry.get("contributors", [])
                ):
                    errors.append(
                        f"pr:{number}: entry {entry_id} lacks verified event author {authors[number]}"
                    )
    return errors


def typescript(schema_path: Path) -> str:
    schema = read_json(schema_path)
    defs = schema.get("$defs", {})
    names = [
        "bundleSource",
        "bundleContributor",
        "bundleAsset",
        "otherChange",
        "bundleEntry",
        "bundle",
    ]

    def ts(node: dict[str, Any]) -> str:
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return name[0].upper() + name[1:] if name in names else ts(defs[name])
        if "const" in node:
            return json.dumps(node["const"])
        if "enum" in node:
            return " | ".join(json.dumps(value) for value in node["enum"])
        if node.get("type") == "array":
            return f"{ts(node.get('items', {}))}[]"
        if node.get("type") == "object":
            required = set(node.get("required", []))
            return (
                "{ "
                + " ".join(
                    f"{name}{'' if name in required else '?'}: {ts(value)};"
                    for name, value in node.get("properties", {}).items()
                )
                + " }"
            )
        kind = node.get("type")
        return (
            {
                "integer": "number",
                "number": "number",
                "boolean": "boolean",
                "string": "string",
            }.get(kind, "unknown")
            if isinstance(kind, str)
            else "unknown"
        )

    aliases = "\n".join(
        f"export type {name[0].upper() + name[1:]} = {ts(defs[name])};"
        for name in names
    )
    return f"// Generated from {schema_path.as_posix()}; do not edit.\n{aliases}\nexport type ProductUpdateSource = BundleSource;\nexport type ProductUpdateContributor = BundleContributor;\nexport type ProductUpdateAsset = BundleAsset;\nexport type ProductUpdateEntry = BundleEntry;\nexport type ProductUpdateOtherChange = OtherChange;\nexport type ProductUpdatesBundle = Bundle;\n"


def asset_map(content_dir: Path) -> str:
    """Generate Vite URL imports after the build copies assets beside this map."""
    assets = sorted(
        path.relative_to(content_dir).as_posix()
        for path in (content_dir / "assets").rglob("*")
        if path.is_file()
        and _asset_error(
            content_dir, {"path": path.relative_to(content_dir).as_posix()}
        )
        is None
    )
    imports = [
        f'import asset_{index} from "./product-updates-assets/{path.removeprefix("assets/")}?url";'
        for index, path in enumerate(assets)
    ]
    mappings = [
        f"  {json.dumps(path)}: asset_{index}," for index, path in enumerate(assets)
    ]
    return (
        "// Generated by scripts/product_updates.py; do not edit.\n"
        + "\n".join(imports)
        + "\n\nexport const productUpdateAssetUrls: Record<string, string> = {\n"
        + "\n".join(mappings)
        + "\n};\n"
    )


def collect_inventory(base: str, target: str, repository: str) -> dict[str, Any]:
    """Explicit preparation command; rendering never invokes GitHub or ``gh``."""
    commits = subprocess.run(
        ["git", "rev-list", "--reverse", f"{base}..{target}"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.splitlines()
    rows: list[dict[str, Any]] = []
    numbers: set[int] = set()
    for sha in commits:
        subject = subprocess.run(
            ["git", "log", "-1", "--format=%s", sha],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        linked = json.loads(
            subprocess.run(
                ["gh", "api", f"repos/{repository}/commits/{sha}/pulls"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
        )
        prs = sorted({item["number"] for item in linked})
        numbers.update(prs)
        rows.append({"sha": sha, "subject": subject, "prs": prs})
    details: list[dict[str, Any]] = []
    for number in sorted(numbers):
        raw = json.loads(
            subprocess.run(
                ["gh", "api", f"repos/{repository}/pulls/{number}"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
        )
        details.append(
            {
                "number": number,
                "title": raw["title"],
                "url": raw["html_url"],
                "author": {
                    "login": raw["user"]["login"],
                    "type": raw["user"].get("type"),
                },
                "merged_at": raw.get("merged_at"),
                "merge_commit": raw.get("merge_commit_sha"),
                "body": raw.get("body") or "",
                "metadata_ref": f"https://api.github.com/repos/{repository}/pulls/{number}",
            }
        )
    return {
        "schema_version": 1,
        "repository": repository,
        "base_ref": base,
        "target_ref": target,
        "collected_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "source_ref": f"https://api.github.com/repos/{repository}/compare/{base}...{target}",
        "commits": rows,
        "prs": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--content-dir", type=Path, default=Path("product-updates")
        )
        command.add_argument("--schema", type=Path)
        command.add_argument(
            "--inventory", type=Path, default=Path("product-updates/inventory.json")
        )
        command.add_argument(
            "--dispositions",
            type=Path,
            default=Path("product-updates/dispositions.json"),
        )
        command.add_argument("--base")
        command.add_argument("--target", required=True)
        command.add_argument("--draft-dir", type=Path)
        command.add_argument("--allow-draft", action="store_true")

    validate = sub.add_parser("validate")
    common(validate)
    coverage = sub.add_parser("coverage")
    common(coverage)
    bundle = sub.add_parser("bundle")
    common(bundle)
    bundle.add_argument("--asset-base-url", default="/product-updates/")
    bundle.add_argument("--content-ref")
    bundle.add_argument("--output", type=Path, required=True)
    bundle.add_argument("--include-staged", action="store_true")
    preview = sub.add_parser("preview")
    common(preview)
    preview.add_argument("--asset-base-url", default="/product-updates/")
    preview.add_argument("--content-ref")
    preview.add_argument("--output", type=Path, required=True)
    preview.add_argument("--include-staged", action="store_true")
    render = sub.add_parser("render-release")
    render.add_argument("--bundle", type=Path, required=True)
    render.add_argument("--asset-base-url", required=True)
    render.add_argument("--output", type=Path, required=True)
    event = sub.add_parser("validate-event")
    event.add_argument("--event-path", type=Path, required=True)
    event.add_argument("--content-dir", type=Path, default=Path("product-updates"))
    event.add_argument("--schema", type=Path)
    event.add_argument(
        "--dispositions", type=Path, default=Path("product-updates/dispositions.json")
    )
    emit = sub.add_parser("types")
    emit.add_argument(
        "--schema", type=Path, default=Path("product-updates/schema.json")
    )
    emit.add_argument("--output", type=Path, required=True)
    assets = sub.add_parser("asset-map")
    assets.add_argument("--content-dir", type=Path, default=Path("product-updates"))
    assets.add_argument("--output", type=Path, required=True)
    collect = sub.add_parser("collect-inventory")
    collect.add_argument("--base", required=True)
    collect.add_argument("--target", required=True)
    collect.add_argument("--repository", default="gobifrost/bifrost")
    collect.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command in {"validate", "coverage", "bundle", "preview"}:
            errors = validate_content(
                args.content_dir,
                args.inventory,
                args.dispositions,
                args.target,
                schema_path=args.schema,
                base=args.base,
                draft_dir=args.draft_dir,
                allow_draft=args.allow_draft,
            )
            if errors:
                raise ValueError("\n".join(errors))
            if args.command == "validate":
                return 0
            entries = load_entries(args.content_dir, args.draft_dir)
            if args.command == "coverage":
                print(
                    json.dumps(
                        coverage_report(
                            read_json(args.inventory),
                            read_json(args.dispositions),
                            entries,
                        ),
                        indent=2,
                        sort_keys=True,
                    )
                )
                return 0
            result = build_bundle(
                args.content_dir,
                args.inventory,
                args.dispositions,
                args.target,
                args.asset_base_url,
                schema_path=args.schema,
                base=args.base,
                content_ref=args.content_ref,
                draft_dir=args.draft_dir,
                allow_draft=args.allow_draft,
                include_staged=args.include_staged,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            return 0
        if args.command == "render-release":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                render_release(read_json(args.bundle), args.asset_base_url),
                encoding="utf-8",
            )
            return 0
        if args.command == "validate-event":
            dispositions = read_json(args.dispositions)
            schema_path = args.schema or args.content_dir / "schema.json"
            errors = schema_errors(
                schema_path, "dispositions", dispositions, str(args.dispositions)
            )
            inventory = read_json(args.content_dir / "inventory.json")
            errors.extend(
                schema_errors(schema_path, "inventory", inventory, "inventory")
            )
            entries = load_entries(args.content_dir)
            for entry in entries:
                errors.extend(
                    schema_errors(
                        schema_path,
                        "entry",
                        {
                            key: value
                            for key, value in entry.items()
                            if key not in {"markdown", "_path"}
                        },
                        str(entry["_path"]),
                    )
                )
                if unsafe_markdown(entry["markdown"]):
                    errors.append(
                        f"{entry['_path']}: raw HTML or executable embeds are not allowed"
                    )
                errors.extend(markdown_asset_errors(entry, str(entry["_path"])))
                for asset in entry.get("assets", []):
                    error = (
                        _asset_error(args.content_dir, asset)
                        if isinstance(asset, dict)
                        else "asset must be an object"
                    )
                    if error:
                        errors.append(f"{entry['_path']}: {error}")
            errors.extend(
                validate_event(
                    read_json(args.event_path), dispositions, entries, inventory
                )
            )
            if errors:
                raise ValueError("\n".join(errors))
            return 0
        if args.command == "collect-inventory":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(
                    collect_inventory(args.base, args.target, args.repository), indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            return 0
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            asset_map(args.content_dir)
            if args.command == "asset-map"
            else typescript(args.schema),
            encoding="utf-8",
        )
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
