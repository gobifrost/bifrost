"""Copy exact upstream sources, retaining hashes; never rewrite platform files."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEST = HERE / "src" / "vendor"
SEEDS = ["src.services.execution.engine", "src.services.execution.async_executor",
         "src.services.execution.worker_sdk_http", "src.services.execution.virtual_import",
         "src.jobs.consumers.workflow_execution", "src.worker.pool_main"]


def graph():
    nodes = {}
    queue = list(SEEDS)
    while queue:
        name = queue.pop()
        if name in nodes:
            continue
        path = ROOT / "api" / (name.replace(".", "/") + ".py")
        if not path.exists():
            path = path.with_suffix("") / "__init__.py"
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        deps = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                deps.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    package = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
                    prefix = package[:len(package)-node.level+1]
                    deps.add(".".join(prefix + ([node.module] if node.module else [])))
                elif node.module:
                    deps.add(node.module)
        nodes[name] = {"path": str(path.relative_to(ROOT)).replace("\\", "/"), "imports": sorted(deps)}
        queue.extend(d for d in deps if d.startswith(("bifrost.", "src.", "shared.")))
    return nodes


def prepare():
    DEST.mkdir(parents=True, exist_ok=True)
    # Full standalone SDK preserves its actual eager import behavior, without CLI dependencies.
    paths = list((ROOT / "api/bifrost").glob("*.py"))
    paths += [ROOT / "api" / p for p in (
        "src/services/execution/engine.py", "src/sdk/context.py", "src/sdk/errors.py",
        "src/sdk/error_handling.py", "src/core/error_messages.py", "src/core/secret_string.py",
        "src/models/enums.py")]
    hashes = {}
    for path in paths:
        relative = path.relative_to(ROOT / "api")
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[str(relative).replace("\\", "/")] = hashlib.sha256(path.read_bytes()).hexdigest()
    # Namespace scaffolding deliberately avoids src.models.__init__'s eager ORM imports.
    for folder in ("src", "src/services", "src/services/execution", "src/sdk", "src/core", "src/models"):
        (DEST / folder / "__init__.py").write_text("# Prototype namespace: excludes platform package initialization.\n")
    report = {"head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "files": hashes, "bytes": sum(p.stat().st_size for p in paths),
              "graph_kind": "conservative AST imports including optional/function-local/TYPE_CHECKING; not runtime closure",
              "graph": graph()}
    (HERE / "results/source-manifest.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps({"files": len(hashes), "source_bytes": report["bytes"], "graph_nodes": len(report["graph"])}))


if __name__ == "__main__":
    prepare()
