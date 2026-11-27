"""Prepare the approved feed and its static assets for every container image."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from scripts.product_updates import build_bundle, read_json
from scripts.product_updates_build_sources import resolve_build_inventory


def prepare(root: Path, target: str, *, sources: Path | None = None) -> dict:
    content = root / "product-updates"
    inventory = content / "inventory.json"
    build_inventory = None
    if sources is not None:
        build_inventory = resolve_build_inventory(
            read_json(sources), read_json(inventory), target, content / "schema.json"
        )
    # Runtime history uses the cumulative ledger, independent of release intervals.
    bundle = build_bundle(
        content,
        inventory,
        content / "dispositions.json",
        target,
        "/product-updates/",
        content_ref=target,
        build_inventory=build_inventory,
    )
    destination = root / "client/public/product-updates"
    if destination.exists():
        shutil.rmtree(destination)
    for entry in bundle["entries"]:
        if entry.get("in_app") is False:
            continue
        for asset in entry["assets"]:
            source = content / asset["path"]
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            asset_path = Path(asset["path"])
            immutable_path = asset_path.with_name(f"{digest}-{asset_path.name}")
            asset["url"] = f"/product-updates/{immutable_path.as_posix()}"
            output = destination / immutable_path
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
    (root / "api/product-updates.bundle.json").write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--build-sources", type=Path)
    args = parser.parse_args()
    prepare(args.root, args.target, sources=args.build_sources)


if __name__ == "__main__":
    main()
