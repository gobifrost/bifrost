"""Conservative static analysis for workflow least-privilege conversion.

The converter must inspect workspace source without importing it: workflow code
has import-time effects and may not be trusted.  ``summarize_source`` therefore
turns one file into a small JSON call graph.  ``analyze_workflow`` follows that
graph through a caller-supplied repository resolver, retaining only the source
hashes that influenced its result.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ANALYZER_VERSION = "4"
MAX_SOURCE_BYTES = 512_000
MAX_AST_NODES = 20_000
MAX_EVENTS_PER_SCOPE = 2_048
MAX_DEPENDENCIES = 256
MAX_GRAPH_STEPS = 10_000
MAX_SDK_CALLS = 2_048
MAX_EVIDENCE = 4_096
_MODULE_INIT = "<import>"
_BUILTINS = {
    "abs",
    "all",
    "any",
    "bool",
    "bytes",
    "dict",
    "enumerate",
    "filter",
    "float",
    "frozenset",
    "int",
    "isinstance",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "object",
    "open",
    "range",
    "reversed",
    "set",
    "sorted",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "zip",
}
_THIRD_PARTY_ROOTS = {
    "aiohttp",
    "bs4",
    "celery",
    "dateutil",
    "fastapi",
    "httpx",
    "numpy",
    "openai",
    "pydantic",
    "requests",
    "sqlalchemy",
    "starlette",
    "yaml",
}
_RAW_HTTP_ROOTS = {"aiohttp", "httpx", "requests", "urllib"}


def _is_sdk_module(module: str) -> bool:
    return module == "bifrost" or module.startswith("bifrost.")


_SAFE_BIFROST_DECORATORS = {"bifrost.service", "bifrost.workflow"}


@dataclass(frozen=True)
class SourceFile:
    """A repository source file supplied by the conversion service."""

    key: str
    path: str
    source: str
    content_hash: str


def _dotted(
    node: ast.AST, bindings: dict[str, dict[str, Any]], handles: dict[str, str]
) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in handles:
            return handles[node.id]
        binding = bindings.get(node.id)
        if binding and _is_sdk_module(binding["module"]):
            symbol = binding.get("symbol")
            return f"{binding['module']}.{symbol}" if symbol else binding["module"]
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted(node.value, bindings, handles)
        return f"{parent}.{node.attr}" if parent else None
    if isinstance(node, ast.Call):
        return _dotted(node.func, bindings, handles)
    if isinstance(node, ast.Await):
        return _dotted(node.value, bindings, handles)
    return None


def _sdk_call(target: str | None) -> str | None:
    if not target or not target.startswith("bifrost."):
        return None
    parts = target.split(".")
    if len(parts) < 3:
        return None
    module = parts[1]
    method = parts[-1]
    if method == module and len(parts) == 3:
        return None
    return f"bifrost.{module}.{method}"


def _binding_for_expression(
    node: ast.AST, bindings: dict[str, dict[str, Any]], handles: dict[str, str]
) -> dict[str, Any] | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    attributes: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        binding = bindings.get(current.id)
        if binding and not binding.get("symbol"):
            attributes.reverse()
            module_tail = binding["module"].split(".")[1:]
            if attributes[: len(module_tail)] == module_tail:
                attributes = attributes[len(module_tail) :]
            if attributes:
                return {**binding, "symbol": ".".join(attributes)}
    return None


def _higher_order_callback(
    node: ast.Call, target: str | None
) -> tuple[str, ast.AST] | None:
    if target in {"map", "filter"} and node.args:
        callback = node.args[0]
    elif target in {"sorted", "min", "max"}:
        callback = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "key"), None
        )
    else:
        return None
    if (
        callback is None
        or isinstance(callback, ast.Constant)
        and callback.value is None
    ):
        return None
    return target, callback


def _branch_binding_updates(statements: list[ast.stmt]) -> dict[str, tuple[Any, ...]]:
    """Return direct binding changes that make an ``if`` branch ambiguous."""
    updates: dict[str, tuple[Any, ...]] = {}
    for statement in statements:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                updates[alias.asname or alias.name.split(".")[0]] = (
                    "import",
                    alias.name,
                    0,
                    None,
                )
        elif isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                if alias.name != "*":
                    updates[alias.asname or alias.name] = (
                        "import",
                        statement.module or "",
                        statement.level,
                        alias.name,
                    )
        elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            value = getattr(statement, "value", None)
            signature = (
                "assignment",
                ast.dump(value, include_attributes=False)
                if value is not None
                else None,
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    updates[target.id] = signature
    return updates


def _binding_signature(visitor: "_ScopeSummary", name: str) -> tuple[Any, ...] | None:
    if name in visitor.bindings:
        binding = visitor.bindings[name]
        return (
            "import",
            binding["module"],
            binding["level"],
            binding.get("symbol"),
        )
    if name in visitor.handles:
        return ("handle", visitor.handles[name])
    if name in visitor.local_aliases:
        return ("local", visitor.local_aliases[name])
    return None


class _ScopeSummary(ast.NodeVisitor):
    """Summarize executable statements in a module or one function body."""

    def __init__(
        self, bindings: dict[str, dict[str, Any]], local_functions: set[str]
    ) -> None:
        self.bindings = {name: dict(binding) for name, binding in bindings.items()}
        self.local_functions = local_functions
        self.handles: dict[str, str] = {}
        self.local_aliases: dict[str, str] = {}
        self.imports: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.truncated = False

    def _emit(self, event: dict[str, Any]) -> None:
        if len(self.events) < MAX_EVENTS_PER_SCOPE:
            self.events.append(event)
        else:
            self.truncated = True

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            binding = {"module": alias.name, "level": 0, "symbol": None}
            self.bindings[local] = binding
            self.imports.append(binding)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                self._emit(
                    {"kind": "unknown", "line": node.lineno, "reason": "star import"}
                )
                continue
            binding = {
                "module": module,
                "level": node.level,
                "symbol": alias.name,
            }
            self.bindings[alias.asname or alias.name] = binding
            self.imports.append(binding)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.visit(node.value)
        target = _dotted(node.value, self.bindings, self.handles)
        sdk = _sdk_call(target)
        binding = _binding_for_expression(node.value, self.bindings, self.handles)
        local_name = node.value.id if isinstance(node.value, ast.Name) else None
        if sdk:
            for assignment in node.targets:
                if isinstance(assignment, ast.Name):
                    self.handles[assignment.id] = ".".join(sdk.split(".")[:2])
        for assignment in node.targets:
            if not isinstance(assignment, ast.Name):
                continue
            self.handles.pop(assignment.id, None)
            self.local_aliases.pop(assignment.id, None)
            self.bindings.pop(assignment.id, None)
            if sdk:
                self.handles[assignment.id] = ".".join(sdk.split(".")[:2])
            elif binding:
                self.bindings[assignment.id] = dict(binding)
            elif local_name and local_name in (
                self.local_functions | set(self.local_aliases)
            ):
                self.local_aliases[assignment.id] = self.local_aliases.get(
                    local_name, local_name
                )

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is None:
            return
        self.visit(node.value)
        target = _dotted(node.value, self.bindings, self.handles)
        sdk = _sdk_call(target)
        binding = _binding_for_expression(node.value, self.bindings, self.handles)
        local_name = node.value.id if isinstance(node.value, ast.Name) else None
        if isinstance(node.target, ast.Name):
            self.handles.pop(node.target.id, None)
            self.local_aliases.pop(node.target.id, None)
            self.bindings.pop(node.target.id, None)
            if sdk:
                self.handles[node.target.id] = ".".join(sdk.split(".")[:2])
            elif binding:
                self.bindings[node.target.id] = dict(binding)
            elif local_name and local_name in (
                self.local_functions | set(self.local_aliases)
            ):
                self.local_aliases[node.target.id] = self.local_aliases.get(
                    local_name, local_name
                )

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self.handles.pop(node.target.id, None)
            self.local_aliases.pop(node.target.id, None)
            self.bindings.pop(node.target.id, None)

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        self.visit(node.test)
        body_updates = _branch_binding_updates(node.body)
        else_updates = _branch_binding_updates(node.orelse)
        prior = {
            name: _binding_signature(self, name)
            for name in set(body_updates) | set(else_updates)
        }
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)
        for name in set(body_updates) | set(else_updates):
            if body_updates.get(name, prior[name]) == else_updates.get(
                name, prior[name]
            ):
                continue
            self._emit(
                {
                    "kind": "unknown",
                    "line": node.lineno,
                    "reason": f"conditional binding for {name}",
                }
            )
            self.handles.pop(name, None)
            self.local_aliases.pop(name, None)
            self.bindings.pop(name, None)

    def visit_Try(self, node: ast.Try | ast.TryStar) -> None:  # noqa: N802
        # Exception paths can leave a different callable/import binding alive.
        # Until those paths have a merged binding graph, retain uncertainty
        # instead of treating the final textual handler as the only outcome.
        if any(
            isinstance(child, (ast.Import, ast.ImportFrom))
            or isinstance(child, (ast.Assign, ast.AnnAssign))
            and isinstance(child.value, (ast.Name, ast.Attribute, ast.IfExp))
            for child in ast.walk(node)
        ):
            self._emit(
                {
                    "kind": "unknown",
                    "line": node.lineno,
                    "reason": "conditional binding in try/except",
                }
            )
        self.generic_visit(node)

    visit_TryStar = visit_Try

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_definition_expressions(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_definition_expressions(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for decorator in node.decorator_list:
            self.visit(decorator)
        for expression in [*node.bases, *(keyword.value for keyword in node.keywords)]:
            self.visit(expression)
        for child in node.body:
            _visit_import_time_statement(child, self)

    def _visit_definition_expressions(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        target = _dotted(node.func, self.bindings, self.handles)
        binding = _binding_for_expression(node.func, self.bindings, self.handles)
        callback = _higher_order_callback(node, target)
        if callback is not None:
            self._emit(
                {
                    "kind": "unknown",
                    "line": node.lineno,
                    "reason": f"higher-order callback: {callback[0]}",
                }
            )
        sdk = _sdk_call(target)
        if sdk:
            self._emit({"kind": "sdk", "line": node.lineno, "call": sdk})
        elif target in _SAFE_BIFROST_DECORATORS:
            pass
        elif target in {"eval", "exec"}:
            self._emit({"kind": "unknown", "line": node.lineno, "reason": target})
        elif target == "getattr":
            subject = (
                _dotted(node.args[0], self.bindings, self.handles)
                if node.args
                else None
            )
            if subject and subject.startswith("bifrost."):
                self._emit(
                    {
                        "kind": "unknown",
                        "line": node.lineno,
                        "reason": "dynamic SDK dispatch",
                    }
                )
        elif target in {"importlib.import_module", "__import__"} or (
            binding
            and binding["module"] == "importlib"
            and binding.get("symbol") == "import_module"
        ):
            value = node.args[0] if node.args else None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                level = len(value.value) - len(value.value.lstrip("."))
                self._emit(
                    {
                        "kind": "dynamic_import",
                        "line": node.lineno,
                        "module": value.value[level:],
                        "level": level,
                    }
                )
            else:
                self._emit(
                    {"kind": "unknown", "line": node.lineno, "reason": "dynamic import"}
                )
        else:
            if binding:
                module = binding["module"]
                if module.split(".")[0] in _RAW_HTTP_ROOTS:
                    self._emit(
                        {"kind": "unknown", "line": node.lineno, "reason": "raw HTTP"}
                    )
                elif _is_sdk_module(module):
                    # ``_sdk_call`` above handles public SDK call shapes.  This
                    # is intentionally conservative for wrappers we do not know.
                    self._emit(
                        {
                            "kind": "unknown",
                            "line": node.lineno,
                            "reason": "unrecognized SDK wrapper",
                        }
                    )
                else:
                    self._emit(
                        {
                            "kind": "imported",
                            "line": node.lineno,
                            "module": module,
                            "level": binding["level"],
                            "symbol": binding.get("symbol"),
                        }
                    )
            elif isinstance(node.func, ast.Name) and node.func.id in (
                self.local_functions | set(self.local_aliases)
            ):
                self._emit(
                    {
                        "kind": "local",
                        "line": node.lineno,
                        "name": self.local_aliases.get(node.func.id, node.func.id),
                    }
                )
            elif target and target.split(".")[0] in _RAW_HTTP_ROOTS:
                self._emit(
                    {"kind": "unknown", "line": node.lineno, "reason": "raw HTTP"}
                )
            elif target and target not in _BUILTINS:
                self._emit(
                    {
                        "kind": "unknown",
                        "line": node.lineno,
                        "reason": "unrecognized wrapper",
                    }
                )
        self.generic_visit(node)


class _FunctionScopeBindings(ast.NodeVisitor):
    """Collect names the compiler treats as local to one function scope."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.globals: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.names.update(
            alias.asname or alias.name.split(".")[0] for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.names.update(
            alias.asname or alias.name for alias in node.names if alias.name != "*"
        )

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.globals.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        # A lambda creates its own local scope.
        return


def _function_scope_bindings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return names that hide module imports throughout a function body."""
    collector = _FunctionScopeBindings()
    for statement in node.body:
        collector.visit(statement)
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    collector.names.update(argument.arg for argument in arguments)
    if node.args.vararg is not None:
        collector.names.add(node.args.vararg.arg)
    if node.args.kwarg is not None:
        collector.names.add(node.args.kwarg.arg)
    return collector.names - collector.globals


def _scope_summary(
    statements: list[ast.stmt],
    bindings: dict[str, dict[str, Any]],
    local_functions: set[str],
    shadowed_names: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    visitor = _ScopeSummary(bindings, local_functions)
    for name in shadowed_names or ():
        visitor.bindings.pop(name, None)
    for statement in statements:
        _visit_import_time_statement(statement, visitor)
    if visitor.truncated:
        visitor.events.append(
            {
                "kind": "unknown",
                "line": 0,
                "reason": f"summary event limit reached ({MAX_EVENTS_PER_SCOPE})",
            }
        )
    return visitor.events, visitor.bindings, visitor.imports


def _visit_import_time_statement(statement: ast.stmt, visitor: _ScopeSummary) -> None:
    """Visit expressions Python evaluates while defining modules and classes.

    A function's body is deferred, while decorators and default expressions are
    evaluated immediately.  Class bodies are also immediate, recursively.
    """
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        visitor._visit_definition_expressions(statement)
        return
    if isinstance(statement, ast.ClassDef):
        visitor.visit_ClassDef(statement)
        return
    visitor.visit(statement)


def summarize_source(source: str) -> dict[str, Any]:
    """Return a bounded, JSON-serializable static summary without retaining source or AST."""
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        return {
            "version": ANALYZER_VERSION,
            "functions": {},
            "bindings": {},
            "top_level": [],
            "parse_error": f"source exceeds maximum size ({MAX_SOURCE_BYTES} bytes)",
        }
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return {
            "version": ANALYZER_VERSION,
            "functions": {},
            "bindings": {},
            "top_level": [],
            "parse_error": f"{error.msg} at line {error.lineno}",
        }

    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        return {
            "version": ANALYZER_VERSION,
            "functions": {},
            "bindings": {},
            "top_level": [],
            "parse_error": f"source exceeds maximum AST nodes ({MAX_AST_NODES})",
        }

    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    names = set(functions)
    top_level, bindings, _ = _scope_summary(tree.body, {}, names)
    function_summaries: dict[str, dict[str, Any]] = {}
    for name, node in functions.items():
        events, function_bindings, function_imports = _scope_summary(
            node.body, bindings, names, _function_scope_bindings(node)
        )
        function_summaries[name] = {
            "events": events,
            "bindings": function_bindings,
            "imports": function_imports,
        }

    return {
        "version": ANALYZER_VERSION,
        "functions": function_summaries,
        "bindings": bindings,
        "top_level": top_level,
    }


ResolveImport = Callable[[SourceFile, str, int], Awaitable[SourceFile | None]]
Summarize = Callable[[SourceFile], Awaitable[dict[str, Any]]]


def _label(source_file: SourceFile, function: str) -> str:
    return f"{source_file.path}::{function}"


def _is_stdlib(module: str) -> bool:
    return module.split(".")[0] in sys.stdlib_module_names


async def analyze_workflow(
    entry: SourceFile,
    function_name: str,
    resolve_import: ResolveImport,
    summarize: Summarize,
) -> dict[str, Any]:
    """Analyze one workflow entry point without evaluating any user source."""
    summaries: dict[str, dict[str, Any]] = {}
    dependencies: dict[str, str] = {}
    initialized: set[str] = set()
    active_functions: set[tuple[str, str]] = set()
    completed_functions: set[tuple[str, str]] = set()
    sdk_calls: set[str] = set()
    evidence: list[dict[str, Any]] = []
    unknowns: set[str] = set()
    graph_steps = 0
    graph_limited = False
    evidence_limited = False
    sdk_calls_limited = False

    async def load(source_file: SourceFile) -> dict[str, Any]:
        if source_file.key not in dependencies:
            if len(dependencies) >= MAX_DEPENDENCIES:
                unknowns.add(
                    f"{source_file.path}: dependency limit reached ({MAX_DEPENDENCIES})"
                )
                return {"parse_error": "dependency limit reached"}
            dependencies[source_file.key] = source_file.content_hash
        if source_file.key not in summaries:
            summaries[source_file.key] = await summarize(source_file)
        return summaries[source_file.key]

    def consume_graph_step(source_file: SourceFile) -> bool:
        nonlocal graph_steps, graph_limited
        if graph_limited:
            return False
        graph_steps += 1
        if graph_steps > MAX_GRAPH_STEPS:
            graph_limited = True
            unknowns.add(
                f"{source_file.path}: graph traversal limit reached ({MAX_GRAPH_STEPS})"
            )
            return False
        return True

    def add_unknown(source_file: SourceFile, event: dict[str, Any]) -> None:
        unknowns.add(f"{source_file.path}:{event['line']}: {event['reason']}")

    async def resolve_binding(
        source_file: SourceFile,
        module: str,
        level: int,
        symbol: str | None,
        via: list[str],
        line: int,
    ) -> None:
        if not consume_graph_step(source_file):
            return
        if _is_sdk_module(module):
            return
        root = module.split(".")[0] if module else ""

        # Python initializes every concrete parent package before importing a
        # child, even when the parent is never called explicitly.
        parts = module.split(".")
        for end in range(1, len(parts)):
            if not consume_graph_step(source_file):
                return
            package = await resolve_import(source_file, ".".join(parts[:end]), level)
            if package is not None and package.path.endswith("/__init__.py"):
                await walk_module_init(package, via + [_label(package, _MODULE_INIT)])

        # A relative import executes its package initializer before the target
        # module.  Resolvers that do not model package files simply return None.
        if level:
            package = await resolve_import(source_file, "", level)
            if package is not None and package.key != source_file.key:
                await walk_module_init(package, via + [_label(package, _MODULE_INIT)])

        resolved = await resolve_import(source_file, module, level)
        if resolved is None:
            if level == 0 and _is_stdlib(module):
                return
            if level == 0 and root in _THIRD_PARTY_ROOTS:
                unknowns.add(
                    f"{source_file.path}:{line}: third-party helper not analyzed: {module}"
                )
            else:
                prefix = "." * level
                unknowns.add(
                    f"{source_file.path}:{line}: unresolved user module: {prefix}{module}"
                )
            return
        await walk_module_init(resolved, via + [_label(resolved, _MODULE_INIT)])
        if symbol:
            await walk_function(resolved, symbol, via + [_label(resolved, symbol)])

    async def process_events(
        source_file: SourceFile, events: list[dict[str, Any]], via: list[str]
    ) -> None:
        nonlocal evidence_limited, sdk_calls_limited
        for event in events:
            kind = event["kind"]
            if kind == "sdk":
                call = event["call"]
                if call in sdk_calls or len(sdk_calls) < MAX_SDK_CALLS:
                    sdk_calls.add(call)
                elif not sdk_calls_limited:
                    sdk_calls_limited = True
                    unknowns.add(
                        f"{source_file.path}: SDK call limit reached ({MAX_SDK_CALLS})"
                    )
                if len(evidence) < MAX_EVIDENCE:
                    evidence.append(
                        {
                            "path": source_file.path,
                            "line": event["line"],
                            "call": call,
                            "via": list(via),
                        }
                    )
                elif not evidence_limited:
                    evidence_limited = True
                    unknowns.add(
                        f"{source_file.path}: evidence limit reached ({MAX_EVIDENCE})"
                    )
            elif kind == "local":
                await walk_function(
                    source_file,
                    event["name"],
                    via + [_label(source_file, event["name"])],
                )
            elif kind == "imported":
                await resolve_binding(
                    source_file,
                    event["module"],
                    event["level"],
                    event.get("symbol"),
                    via,
                    event["line"],
                )
            elif kind == "dynamic_import":
                await resolve_binding(
                    source_file,
                    event["module"],
                    event.get("level", 0),
                    None,
                    via,
                    event["line"],
                )
            elif kind == "unknown":
                add_unknown(source_file, event)

    async def walk_module_init(source_file: SourceFile, via: list[str]) -> None:
        if source_file.key in initialized:
            return
        if not consume_graph_step(source_file):
            return
        initialized.add(source_file.key)
        summary = await load(source_file)
        parse_error = summary.get("parse_error")
        if parse_error:
            unknowns.add(f"{source_file.path}: {parse_error}")
            return
        await process_events(source_file, summary.get("top_level", []), via)
        # Imports do not appear as call events, but execute import-time code.
        seen_imports: set[tuple[str, int]] = set()
        for binding in summary.get("bindings", {}).values():
            module = binding["module"]
            level = binding["level"]
            if (module, level) not in seen_imports:
                seen_imports.add((module, level))
                await resolve_binding(source_file, module, level, None, via, 1)

    async def walk_function(source_file: SourceFile, name: str, via: list[str]) -> None:
        marker = (source_file.key, name)
        if marker in completed_functions or marker in active_functions:
            return
        if not consume_graph_step(source_file):
            return
        summary = await load(source_file)
        if summary.get("parse_error"):
            return
        function = summary.get("functions", {}).get(name)
        if function is None:
            exported = summary.get("bindings", {}).get(name)
            if exported:
                await resolve_binding(
                    source_file,
                    exported["module"],
                    exported["level"],
                    exported.get("symbol"),
                    via,
                    1,
                )
                completed_functions.add(marker)
                return
            unknowns.add(f"{source_file.path}:1: unresolved user function: {name}")
            completed_functions.add(marker)
            return
        active_functions.add(marker)
        try:
            for binding in function.get("imports", []):
                await resolve_binding(
                    source_file, binding["module"], binding["level"], None, via, 1
                )
            await process_events(source_file, function.get("events", []), via)
        finally:
            active_functions.remove(marker)
        completed_functions.add(marker)

    try:
        await walk_module_init(entry, [_label(entry, _MODULE_INIT)])
        await walk_function(entry, function_name, [_label(entry, function_name)])
    except RecursionError:
        unknowns.add(
            f"{entry.path}: Python recursion depth reached before graph traversal limit "
            f"({MAX_GRAPH_STEPS})"
        )

    unique_evidence = {
        (item["path"], item["line"], item["call"], tuple(item["via"])): item
        for item in evidence
    }
    return {
        "sdk_calls": sorted(sdk_calls),
        "evidence": [unique_evidence[key] for key in sorted(unique_evidence)],
        "unknowns": sorted(unknowns),
        "dependencies": {key: dependencies[key] for key in sorted(dependencies)},
    }
