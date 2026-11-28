"""Pure compiler for inline App bundles.

This module accepts a frozen source mapping and dependency mapping.  It has no
repository, object-storage, database, settings, or platform-job dependency, so
the same compiler can run locally for request-scoped preview work or inside a
least-privilege worker child for a durable publish.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from bifrost.platform_names import PLATFORM_EXPORT_NAMES
from src.services.app_compiler import AppTailwindService, TailwindGenerationError
from src.services.app_resource_limits import AppArtifactLimitError, validate_app_artifacts
from src.services.node_subprocess import APP_BUNDLER_NODE_POLICY, run_node_script


BUNDLE_SCRIPT = Path(__file__).parents[1] / "src" / "services" / "app_bundler" / "bundle.js"
SCHEMA_VERSION = 5
TAILWIND_OUTPUT_CSS = "__bifrost_tailwind.css"
DEFAULT_EXTERNALS = ["react", "react-dom", "react-dom/client", "react-router-dom", "lucide-react", "sonner", "date-fns", "clsx", "tailwind-merge"]
Mode = Literal["preview", "live"]
_PLATFORM_EXPORT_NAMES: frozenset[str] = PLATFORM_EXPORT_NAMES
_DEFAULT_EXPORT_RE = re.compile(r"^\s*export\s+default\b|^\s*export\s*\{\s*default\b", re.MULTILINE)
_BIFROST_IMPORT_RE = re.compile(r'import\s+\{([^}]+)\}\s+from\s+["\']bifrost["\']')


@dataclass
class BundleMessage:
    text: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    line_text: str | None = None


@dataclass
class BundleManifest:
    entry: str
    css: str | None
    outputs: list[str]
    duration_ms: int
    warnings: list[BundleMessage]
    dependencies: dict[str, str]


@dataclass
class BundleResult:
    success: bool
    manifest: BundleManifest | None = None
    errors: list[BundleMessage] | None = None
    warnings: list[BundleMessage] | None = None
    duration_ms: int = 0


@dataclass
class BundleCompilation:
    result: BundleResult
    artifacts: dict[str, bytes]


async def compile_app_bundle(
    source_files: Mapping[str, bytes], dependencies: Mapping[str, str] | None, mode: Mode,
    *, platform_exports: frozenset[str] | tuple[str, ...] = _PLATFORM_EXPORT_NAMES,
) -> BundleCompilation:
    """Compile explicit App source into explicit artifacts without platform I/O."""
    dependencies = dict(dependencies or {})
    with tempfile.TemporaryDirectory(prefix="bifrost-bundle-") as tmp:
        source_dir, output_dir = Path(tmp) / "src", Path(tmp) / "dist"
        source_dir.mkdir()
        sources: list[str] = []
        for rel, body in source_files.items():
            target = _safe_source_path(source_dir, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            sources.append(rel)
        if not sources:
            return BundleCompilation(BundleResult(False, errors=[BundleMessage(text="No source files for app")]), {})
        try:
            tailwind_added, consumed_css = await generate_app_tailwind(source_dir, sources)
        except TailwindGenerationError as exc:
            return BundleCompilation(BundleResult(False, errors=[BundleMessage(text=f"Tailwind CSS generation failed: {exc}")]), {})
        if tailwind_added:
            sources = [source for source in sources if source not in consumed_css]
            sources.append(TAILWIND_OUTPUT_CSS)
        write_entry(source_dir, "_entry.tsx", sources)
        write_bifrost_package(source_dir, sources, platform_exports=platform_exports)
        raw = await run_esbuild({
            "source_dir": str(source_dir), "out_dir": str(output_dir), "entry": "_entry.tsx",
            "mode": mode, "externals": DEFAULT_EXTERNALS + list(dependencies),
        })
        duration_ms = int(raw.get("duration_ms", 0))
        warnings, warning_error = _diagnostics(raw.get("warnings", []), "warning")
        if warning_error is not None:
            return BundleCompilation(
                BundleResult(False, errors=[warning_error], duration_ms=duration_ms), {}
            )
        if not raw.get("success"):
            errors, error_error = _diagnostics(raw.get("errors", []), "error")
            if error_error is not None:
                return BundleCompilation(
                    BundleResult(
                        False,
                        errors=[error_error],
                        warnings=warnings,
                        duration_ms=duration_ms,
                    ),
                    {},
                )
            if not errors:
                errors = [BundleMessage(text="esbuild failed with no error output")]
            return BundleCompilation(BundleResult(False, errors=errors, warnings=warnings, duration_ms=duration_ms), {})
        try:
            artifacts = _read_artifacts(output_dir, raw["outputs"])
        except (AppArtifactLimitError, KeyError, OSError, TypeError, ValueError) as exc:
            message = str(exc) if isinstance(exc, AppArtifactLimitError) else f"Bundler returned invalid artifact metadata: {exc}"
            return BundleCompilation(BundleResult(False, errors=[BundleMessage(text=message)]), {})
        manifest = BundleManifest(str(raw["entry_file"]), raw.get("css_file"), list(artifacts), duration_ms, warnings, dependencies)
        return BundleCompilation(BundleResult(True, manifest=manifest, warnings=warnings, duration_ms=duration_ms), artifacts)


async def generate_app_tailwind(source_dir: Path, sources: list[str]) -> tuple[bool, set[str]]:
    scannable = [item for item in sources if item.endswith((".tsx", ".ts", ".jsx", ".js"))]
    css_files = [item for item in sources if item.endswith(".css")]
    if not scannable and not css_files:
        return False, set()
    code_sources = [_read_text(source_dir / item) for item in scannable]
    user_css = [(item, _read_text(source_dir / item)) for item in css_files]
    config_path = next((str((source_dir / f"tailwind.config.{extension}").resolve()) for extension in ("ts", "js", "mjs", "cjs") if (source_dir / f"tailwind.config.{extension}").exists()), None)
    css = await AppTailwindService.generate_css_pipeline(code_sources=code_sources, user_css=user_css, config_path=config_path)
    if not css:
        return False, set()
    (source_dir / TAILWIND_OUTPUT_CSS).write_text(css, encoding="utf-8")
    return True, set(css_files)


def write_entry(source_dir: Path, entry_file: str, sources: list[str]) -> None:
    has_layout = "_layout.tsx" in sources
    pages = sorted(item for item in sources if item.startswith("pages/") and item.endswith((".tsx", ".ts")))
    lines = ["// AUTO-GENERATED by bifrost bundler. Do not edit.", "import React from 'react';", "import { Routes, Route } from 'react-router-dom';"]
    lines.extend(f"import './{item}';" for item in sorted(item for item in sources if item.endswith(".css")))
    if has_layout:
        lines.append("import Layout from './_layout';")
    page_imports: list[tuple[str, str]] = []
    for index, page in enumerate(pages):
        variable = f"Page{index}"
        route = page[len("pages/"):].rsplit(".", 1)[0].removesuffix("/index")
        route = "/" if route in ("index", "") else "/" + route.replace("[", ":").replace("]", "")
        lines.append(f"import {variable} from './{page.rsplit('.', 1)[0]}';")
        page_imports.append((route, variable))
    lines.extend(["", "export default function BundledApp() {"])
    if has_layout:
        lines.extend(["  return React.createElement(Routes, null,", "    React.createElement(Route, { element: React.createElement(Layout) },"])
        lines.extend(f"      React.createElement(Route, {{ path: '{route}', element: React.createElement({variable}) }})," for route, variable in page_imports)
        lines.extend(["    ),", "  );"])
    else:
        lines.append("  return React.createElement(Routes, null,")
        lines.extend(f"    React.createElement(Route, {{ path: '{route}', element: React.createElement({variable}) }})," for route, variable in page_imports)
        lines.append("  );")
    lines.append("}")
    (source_dir / entry_file).write_text("\n".join(lines), encoding="utf-8")


def write_bifrost_package(
    source_dir: Path,
    sources: list[str],
    *,
    platform_exports: frozenset[str] | tuple[str, ...] = _PLATFORM_EXPORT_NAMES,
) -> None:
    platform_exports = frozenset(platform_exports)
    package_dir = source_dir / "node_modules" / "bifrost"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "package.json").write_text(json.dumps({"name": "bifrost", "type": "module", "main": "index.js"}))
    components = [(item, item[len("components/"):].rsplit(".", 1)[0].rsplit("/", 1)[-1]) for item in sources if item.startswith("components/") and item.endswith((".tsx", ".ts"))]
    components = [(item, name) for item, name in components if name and name[0].isalpha()]
    user_names = {name for _, name in components}
    imported = {raw.strip().split(" as ")[0].strip() for item in sources if item.endswith((".tsx", ".ts")) for matched in _BIFROST_IMPORT_RE.findall(_read_text(source_dir / item)) for raw in matched.split(",") if raw.strip() and raw.strip()[0].isalpha()}
    router = {"Link", "NavLink", "Navigate", "Outlet", "Routes", "Route", "BrowserRouter", "HashRouter", "MemoryRouter", "Router", "RouterProvider", "ScrollRestoration", "Form", "Await", "useNavigate", "navigate", "useLocation", "useParams", "useSearchParams", "useOutletContext", "useOutlet", "useMatch", "useResolvedPath", "useRoutes", "useHref", "useLinkClickHandler", "useInRouterContext", "useNavigationType", "useNavigation", "useRevalidator", "useRouteError", "useRouteLoaderData", "useLoaderData", "useActionData", "useAsyncError", "useAsyncValue", "useSubmit", "useFetcher", "useFetchers", "useBlocker", "useBeforeUnload", "createBrowserRouter", "createHashRouter", "createMemoryRouter", "createRoutesFromChildren", "createRoutesFromElements", "createSearchParams", "generatePath", "matchPath", "matchRoutes", "renderMatches", "resolvePath", "unstable_usePrompt"}
    lucide = imported - user_names - platform_exports
    raw_router = (router & imported) - {"Link", "NavLink", "Navigate", "useNavigate", "navigate", "useLocation"}
    wrapped_router = (router & imported) - raw_router
    lines = ["// AUTO-GENERATED by bifrost bundler. Do not edit.", "// Re-exports platform scope + user components + used Lucide icons.", ""]
    deprecated_user = sorted(name for _, name in components if name in imported)
    deprecated_icons = sorted(lucide)
    deprecated_router = sorted(router & imported)
    if deprecated_user or deprecated_icons or deprecated_router:
        details: list[str] = []
        if deprecated_user:
            details.append("User components imported from 'bifrost' (prefer relative imports like ./components/Foo): " + ", ".join(deprecated_user))
        if deprecated_icons:
            details.append("Icons imported from 'bifrost' (prefer 'lucide-react'): " + ", ".join(deprecated_icons))
        if deprecated_router:
            details.append("React Router primitives imported from 'bifrost' (prefer 'react-router-dom' — basename handles app paths): " + ", ".join(deprecated_router))
        message = "[Bifrost] Deprecated imports detected. These will be removed in a future version.\\n  - " + "\\n  - ".join(details)
        lines.extend([f"console.warn({json.dumps(message)});", ""])
    for item, name in components:
        if name in imported:
            lines.append(f"export {{ {'default as ' if _has_default_export(_read_text(source_dir / item)) else ''}{name} }} from {json.dumps('../../' + item.rsplit('.', 1)[0])};")
    if lucide:
        lines.extend(["", f'export {{ {", ".join(sorted(lucide))} }} from "lucide-react";'])
    if raw_router:
        lines.extend(["", f'export {{ {", ".join(sorted(raw_router))} }} from "react-router-dom";'])
    lines.extend(["", "const _p = globalThis.__bifrost_platform || {};"])
    for name in sorted(((platform_exports & imported) | wrapped_router) - user_names - lucide - raw_router):
        lines.append(f"export const {name} = _p[{name!r}];")
    (package_dir / "index.js").write_text("\n".join(lines), encoding="utf-8")


async def run_esbuild(config: dict[str, object]) -> dict[str, object]:
    returncode, stdout, stderr = await run_node_script(BUNDLE_SCRIPT, json.dumps(config).encode(), policy=APP_BUNDLER_NODE_POLICY)
    if returncode != 0:
        return {"success": False, "errors": [{"text": stderr.decode(errors="replace").strip() or "non-zero exit from node"}]}
    try:
        value = json.loads(stdout.decode())
    except json.JSONDecodeError as exc:
        return {"success": False, "errors": [{"text": f"invalid JSON from bundler: {exc}"}]}
    return value if isinstance(value, dict) else {"success": False, "errors": [{"text": "invalid JSON from bundler: result is not an object"}]}


def _read_artifacts(output_dir: Path, outputs: object) -> dict[str, bytes]:
    if not isinstance(outputs, list):
        raise ValueError("outputs is not a list")
    root, artifacts = output_dir.resolve(), {}
    for output in outputs:
        if not isinstance(output, Mapping):
            raise ValueError("artifact metadata is invalid")
        rel = str(output["path"])
        path = (output_dir / rel).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"artifact path escapes bundle output: {rel}")
        artifacts[rel] = path.read_bytes()
    if len(artifacts) != len(outputs):
        raise ValueError("duplicate artifact paths")
    validate_app_artifacts({path: len(body) for path, body in artifacts.items()})
    return artifacts


def _safe_source_path(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"source path escapes bundle input: {rel}")
    return path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _diagnostics(
    value: object, label: str
) -> tuple[list[BundleMessage], BundleMessage | None]:
    """Parse esbuild diagnostics without erasing malformed structured output."""
    if not isinstance(value, list):
        return [], BundleMessage(text=f"Bundler returned invalid {label} diagnostics")

    messages: list[BundleMessage] = []
    for item in value:
        if not isinstance(item, Mapping):
            return [], BundleMessage(text=f"Bundler returned invalid {label} diagnostics")
        text = item.get("text")
        file = item.get("file")
        line = item.get("line")
        column = item.get("column")
        line_text = item.get("line_text")
        if not isinstance(text, str):
            return [], BundleMessage(text=f"Bundler returned invalid {label} diagnostics")
        file_value: str | None = None
        line_value: int | None = None
        column_value: int | None = None
        line_text_value: str | None = None
        if file is not None:
            if not isinstance(file, str):
                return [], BundleMessage(text=f"Bundler returned invalid {label} diagnostics")
            file_value = file
        if line is not None:
            if isinstance(line, bool) or not isinstance(line, int):
                return [], BundleMessage(text=f"Bundler returned invalid {label} diagnostics")
            line_value = line
        if column is not None:
            if isinstance(column, bool) or not isinstance(column, int):
                return [], BundleMessage(text=f"Bundler returned invalid {label} diagnostics")
            column_value = column
        if line_text is not None:
            if not isinstance(line_text, str):
                return [], BundleMessage(text=f"Bundler returned invalid {label} diagnostics")
            line_text_value = line_text
        messages.append(
            BundleMessage(
                text=text,
                file=file_value,
                line=line_value,
                column=column_value,
                line_text=line_text_value,
            )
        )
    return messages, None


def _has_default_export(source: str) -> bool:
    return bool(_DEFAULT_EXPORT_RE.search(source))
