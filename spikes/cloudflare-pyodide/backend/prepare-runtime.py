"""Prepare current upstream worker closure separately from historical spike copies."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import argparse

here = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--manifest", default="current-runtime-source.json")
args = parser.parse_args()
if Path(args.manifest).name != args.manifest:
    raise ValueError("Manifest must be a file name")
repo = here.parents[2]
destination = here / "runtime-source"
paths = list((repo / "api/bifrost").glob("*.py"))
paths += [repo / "api" / (name + ".py") for name in (
    "src/services/execution/worker", "src/services/execution/engine",
    "src/services/execution/virtual_import", "src/services/execution/workspace_modules",
    "src/services/execution/module_loader", "src/services/execution/type_inference",
    "src/core/module_cache_sync", "src/core/module_cache_contract",
    "src/core/error_messages", "src/core/secret_string", "src/models/enums",
    "src/sdk/context", "src/sdk/errors", "src/sdk/error_handling", "src/sdk/decorators",
)]
manifest = {}
for source in paths:
    relative = source.relative_to(repo / "api")
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    manifest[relative.as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
for folder in ("src", "src/core", "src/sdk", "src/models", "src/services", "src/services/execution"):
    (destination / folder / "__init__.py").write_text("# Runtime namespace; excludes server package initialization.\n")
report = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
          "source_files": manifest,
          "note": "Working source hashes bind uncommitted changes too; historical spike manifest is unchanged."}
(here.parent / "results" / args.manifest).write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8",newline="\n")
print(f"Prepared {len(paths)} current upstream source files")
