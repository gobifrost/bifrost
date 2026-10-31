"""Bundle exact prepared engine and locked wasm dependencies for fresh Workers.

Run in the existing tools container. Source/provenance only; never reads credentials.
"""
import base64
import argparse
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--current-runtime", action="store_true")
parser.add_argument("--manifest")
args = parser.parse_args()
if args.manifest and Path(args.manifest).name != args.manifest:
    raise ValueError("Manifest must be a file name")
source = root / "backend/runtime-source" if args.current_runtime else root / "src"
modules = {}
hashes = {}
for folder in (source, root / "python_modules"):
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if folder == source:
            if path.suffix != ".py":
                continue
            name = path.relative_to(folder).as_posix()
            value = {"py": path.read_text(encoding="utf-8")}
        else:
            name = "python_modules/" + path.relative_to(folder).as_posix()
            if name.startswith("python_modules/workers/") and path.suffix == ".js":
                value = {"js": path.read_text(encoding="utf-8")}
            else:
                value = {"base64": base64.b64encode(path.read_bytes()).decode()}
        modules[name] = value
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()

if args.current_runtime:
    runner = (root / "backend/runtime-runner.py").read_bytes()
    modules["runtime_runner.py"] = {"py": runner.decode("utf-8")}
    hashes["runtime_runner.py"] = hashlib.sha256(runner).hexdigest()
manifest = {"module_count": len(modules), "files": hashes,
            "payload_sha256": hashlib.sha256(json.dumps(modules,sort_keys=True).encode()).hexdigest(),
            "note": "Prepared source and wasm package bytes; authority/context of probe runner remains synthetic"}
name = "runtime" if args.current_runtime else "engine"
output = "generated-runtime.mjs" if args.current_runtime else "generated-modules.mjs"
(root / "backend" / output).write_text("export default " + json.dumps(modules) + ";\n")
(root / "results" / (args.manifest or f"dynamic-{name}-manifest.json")).write_text(json.dumps(manifest,indent=2)+"\n")
print(json.dumps({"modules":len(modules), "payload_sha256":manifest["payload_sha256"]}))
