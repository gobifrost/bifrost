#!/usr/bin/env python3
"""Regenerate the bundled models.dev snapshot shipped with the API.

The running platform refreshes the catalog on its own; this snapshot is the
floor used before the first refresh and whenever models.dev is unreachable.
Refresh it when cutting a release:

    python api/scripts/update_model_catalog_snapshot.py
    python api/scripts/update_model_catalog_snapshot.py --source /tmp/api.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.model_catalog import MODELS_DEV_URL, SNAPSHOT_PATH, trim_catalog  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, help="Local api.json instead of downloading")
    args = parser.parse_args()

    if args.source:
        raw = json.loads(args.source.read_text(encoding="utf-8"))
    else:
        with urlopen(MODELS_DEV_URL, timeout=60) as response:  # noqa: S310 - fixed https URL
            raw = json.load(response)
    payload = trim_catalog(raw)
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    # mtime=0 keeps the archive byte-identical for identical catalogs.
    SNAPSHOT_PATH.write_bytes(gzip.compress(encoded, mtime=0))
    models = sum(len(p["models"]) for p in payload.values())
    print(f"{SNAPSHOT_PATH}: {len(payload)} providers, {models} models")


if __name__ == "__main__":
    main()
