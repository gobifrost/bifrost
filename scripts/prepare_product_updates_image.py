"""Prepare the approved feed and its static assets for every container image."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from scripts.product_updates import build_bundle


def prepare(root: Path, target: str) -> dict:
    content = root / "product-updates"
    # Runtime history uses the cumulative ledger, independent of release intervals.
    bundle = build_bundle(
        content,
        content / "inventory.json",
        content / "dispositions.json",
        target,
        "/product-updates/",
        content_ref=target,
    )
    destination = root / "client/public/product-updates"
    if destination.exists():
        shutil.rmtree(destination)
    for entry in bundle["entries"]:
        if entry.get("in_app") is False:
            continue
        for asset in entry["assets"]:
            output = destination / asset["path"]
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(content / asset["path"], output)
    (root / "api/product-updates.bundle.json").write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    prepare(args.root, args.target)


if __name__ == "__main__":
    main()
