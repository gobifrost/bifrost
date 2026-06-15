"""Validate the CODE examples in skill markdown against the real SDK so a stale
snippet that would crash at runtime (or fail to build) is caught in CI, not
rediscovered in a validation run.

It does NOT type-check arbitrary code — it catches the specific, deterministic
drift classes the build-skill validation loop kept hitting:

1. **Subscript on an SDK model** — ``doc["id"]`` where ``doc`` was assigned from a
   ``tables.insert/get/query/update/upsert(...)`` call. Those return pydantic
   models (``DocumentData`` / ``DocumentList``) that are attribute-access only;
   subscript raises ``'DocumentData' object is not subscriptable``.
2. **Nonexistent SDK method** — ``tables.<x>(`` / ``ai.<x>(`` / … where ``<x>`` is
   not a real public method on that introspected SDK class.
3. **A ``@workflow`` / ``@tool`` / ``@data_provider`` function with a ``ctx``
   parameter** — workflows take their inputs as parameters; there is no ``ctx``.
4. **A v2 SDK symbol imported from an internal path** in a TS/TSX block — e.g.
   ``import { tables } from "@/lib/app-sdk/tables"`` instead of ``from "bifrost"``.

Run directly::

    python lint_examples.py path/to/reference.md [...]

Or import ``lint_examples`` / ``lint_example_paths`` from a pytest wrapper.
"""
from __future__ import annotations

import importlib
import inspect
import re
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# SDK namespace modules — each exposes a class named like the module. The method
# set is introspected live so a renamed/removed method fails the gate.
_SDK_MODULES = [
    "agents", "ai", "config", "events", "executions", "files", "forms",
    "integrations", "knowledge", "organizations", "roles", "tables",
    "users", "workflows",
]

# SDK calls whose return value is a pydantic model (attribute-access only). A var
# assigned from one of these must never be subscripted.
_MODEL_RETURNING = re.compile(
    r"\b(\w+)\s*=\s*(?:await\s+)?tables\.(insert|get|query|update|upsert)\s*\("
)

FENCE = re.compile(r"```(?P<info>[^\n]*)\n(?P<body>.*?)```", re.DOTALL)

# v2 SDK symbols that must come from "bifrost", never an internal app-sdk path.
_V2_SYMBOLS = {
    "tables", "TableAccessDeniedError", "TableNotFoundError",
    "BifrostProvider", "BifrostHeader", "useWorkflow", "useWorkflowQuery",
    "useWorkflowMutation", "useTable", "useInfiniteTable", "useBifrostContext",
}
_INTERNAL_IMPORT = re.compile(
    r'import\s*\{([^}]*)\}\s*from\s*["\'](@/lib/app-sdk/[^"\']+|\.{1,2}/[^"\']*tables[^"\']*)["\']'
)


@dataclass
class Finding:
    filename: str
    message: str


def _sdk_methods() -> dict[str, set[str]]:
    """Introspect each SDK class → its set of public method names."""
    out: dict[str, set[str]] = {}
    for name in _SDK_MODULES:
        try:
            mod = importlib.import_module(f"bifrost.{name}")
        except ImportError:
            continue
        cls = getattr(mod, name, None)
        if cls is None or not inspect.isclass(cls):
            continue
        methods = {
            m
            for m, _ in inspect.getmembers(cls, predicate=inspect.isfunction)
            if not m.startswith("_")
        }
        if methods:
            out[name] = methods
    return out


def _block_lang(info: str) -> str:
    return (info or "").strip().split()[0].lower() if info.strip() else ""


def _lint_python_block(body: str, filename: str, methods: dict[str, set[str]]) -> list[Finding]:
    findings: list[Finding] = []

    # 1. Subscript on a model-returning var.
    model_vars: set[str] = set()
    for m in _MODEL_RETURNING.finditer(body):
        model_vars.add(m.group(1))
    for var in model_vars:
        # `var[` with a string/!int key → subscript on a pydantic model.
        if re.search(rf"\b{re.escape(var)}\[", body):
            findings.append(Finding(
                filename,
                f"subscript on SDK model var '{var}' (assigned from a tables.* call) "
                f"— DocumentData/DocumentList are attribute-access only "
                f"(use {var}.id / {var}.data / .documents), '{var}[...]' raises "
                f"'not subscriptable'",
            ))

    # 2. Nonexistent SDK method.
    for ns, real in methods.items():
        for m in re.finditer(rf"\b{ns}\.(\w+)\s*\(", body):
            meth = m.group(1)
            if meth not in real:
                findings.append(Finding(
                    filename,
                    f"unknown SDK method '{ns}.{meth}()' — not a public method of "
                    f"the {ns} SDK (have: {', '.join(sorted(real))})",
                ))

    # 3. @workflow/@tool/@data_provider function with a ctx parameter.
    for m in re.finditer(
        r"@(?:workflow|tool|data_provider)[^\n]*\n\s*(?:async\s+)?def\s+\w+\s*\(([^)]*)\)",
        body,
    ):
        params = [p.strip() for p in m.group(1).split(",") if p.strip()]
        first = params[0].split(":")[0].strip() if params else ""
        if first == "ctx":
            findings.append(Finding(
                filename,
                "workflow function declares a 'ctx' parameter — workflows take "
                "their inputs as parameters; there is no ctx (the platform calls "
                "the function with the input kwargs only)",
            ))

    return findings


def _lint_ts_block(body: str, filename: str) -> list[Finding]:
    findings: list[Finding] = []
    for m in _INTERNAL_IMPORT.finditer(body):
        names = {n.strip() for n in m.group(1).split(",") if n.strip()}
        bad = names & _V2_SYMBOLS
        if bad:
            findings.append(Finding(
                filename,
                f"v2 SDK symbol(s) {sorted(bad)} imported from internal path "
                f"'{m.group(2)}' — import them from \"bifrost\" (the internal "
                f"app-sdk path does not resolve in a v2 app project)",
            ))
    return findings


def lint_examples(text: str, filename: str, methods: dict[str, set[str]] | None = None) -> list[Finding]:
    """Lint every fenced code example in *text*."""
    if methods is None:
        methods = _sdk_methods()
    findings: list[Finding] = []
    for m in FENCE.finditer(text):
        lang = _block_lang(m.group("info"))
        body = m.group("body")
        if lang in ("python", "py"):
            findings.extend(_lint_python_block(body, filename, methods))
        elif lang in ("ts", "tsx", "typescript", "javascript", "js", "jsx"):
            findings.extend(_lint_ts_block(body, filename))
    return findings


def lint_example_paths(paths: list[Path]) -> list[Finding]:
    methods = _sdk_methods()
    out: list[Finding] = []
    for p in paths:
        rel = str(p.relative_to(REPO)) if p.is_absolute() and p.is_relative_to(REPO) else str(p)
        out.extend(lint_examples(p.read_text(encoding="utf-8"), rel, methods))
    return out


if __name__ == "__main__":
    import sys

    args = [Path(a) for a in sys.argv[1:]]
    if not args:
        print("usage: python lint_examples.py <file.md> [...]", file=sys.stderr)
        raise SystemExit(2)
    results = lint_example_paths(args)
    for f in results:
        print(f"{f.filename}: {f.message}")
    print(f"\n{len(results)} finding(s).")
    raise SystemExit(1 if results else 0)
