"""
Bifrost import migration.

Rewrites `import { ... } from "bifrost"` statements in app TSX/TS files:
  - User components (matching components/<Name>.tsx) -> default import from "./components/Name"
  - React Router primitives -> "react-router-dom" import
  - Lucide icons -> "lucide-react" import
  - Everything else -> stays in "bifrost" import

Also infers missing user-component imports from JSX usage.

Known limitations
-----------------
This classifier uses regex, not an AST. It does NOT track function
parameters, destructured bindings, or type-level identifiers. A
user-declared PascalCase name that shadows a platform export may be
incorrectly imported. Always review the diff before applying -- the
CLI prints one by default (see `bifrost migrate-imports --help`).

Standalone: no dependencies on src.*.
"""
from __future__ import annotations

import difflib
import pathlib
import re
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field

# React Router primitives that should be moved to "react-router-dom" by the
# classifier. Must stay in sync with `router_primitives` in
# app_bundler/__init__.py.
#
# NOT in this set (intentionally): Link, NavLink, Navigate, useNavigate,
# navigate. These five MUST stay as "bifrost" imports so the bundler's
# `wrapped_router_names` path routes them through the platform wrappers in
# `app-code-platform/navigation.tsx`, which prepend the app base path
# (`/apps/<slug>/preview` or `/apps/<slug>`) to absolute `to="/..."` props.
# The host's BrowserRouter has no `basename`, so raw react-router-dom Link
# would navigate to `/email` absolute instead of `/apps/<slug>/preview/email`.
#
# Only runtime values (components / hooks / factory fns) — TypeScript types
# are not runtime names and don't belong here.
_ROUTER_NAMES: set[str] = {
    # Components (minus the 5 platform-wrapped ones)
    "Outlet", "Routes", "Route",
    "BrowserRouter", "HashRouter", "MemoryRouter", "Router",
    "RouterProvider", "ScrollRestoration", "Form", "Await",
    # Hooks (minus useNavigate/navigate)
    "useLocation", "useParams",
    "useSearchParams", "useOutletContext", "useOutlet", "useMatch",
    "useResolvedPath", "useRoutes", "useHref",
    "useLinkClickHandler", "useInRouterContext",
    "useNavigationType", "useNavigation", "useRevalidator",
    "useRouteError", "useRouteLoaderData", "useLoaderData",
    "useActionData", "useAsyncError", "useAsyncValue",
    "useSubmit", "useFetcher", "useFetchers", "useBlocker",
    "useBeforeUnload",
    # Factories / helpers
    "createBrowserRouter", "createHashRouter", "createMemoryRouter",
    "createRoutesFromChildren", "createRoutesFromElements",
    "createSearchParams", "generatePath", "matchPath", "matchRoutes",
    "renderMatches", "resolvePath",
    # Unstable / advanced
    "unstable_usePrompt",
}


@dataclass
class FileMigrationResult:
    path: pathlib.Path
    original: str
    updated: str
    moved_icons: int = 0
    moved_router: int = 0
    added_components: int = 0
    user_shadow_warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.original != self.updated

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        if self.moved_icons:
            lines.append(f"  - moved {self.moved_icons} icon{'s' if self.moved_icons != 1 else ''} to lucide-react")
        if self.moved_router:
            lines.append(f"  - moved {self.moved_router} primitive{'s' if self.moved_router != 1 else ''} to react-router-dom")
        if self.added_components:
            lines.append(f"  - added {self.added_components} missing component import{'s' if self.added_components != 1 else ''}")
        for w in self.user_shadow_warnings:
            lines.append(f"  ! warning: user component '{w}' shadows a platform/router/lucide name")
        return lines


def load_lucide_icon_names(dts_path: pathlib.Path | None = None) -> set[str]:
    """
    Extract the set of Lucide icon names from lucide-react's .d.ts file,
    or (preferred) from the `lucide_icon_names.json` snapshot shipped
    alongside this module.

    Resolution order:
      1. Explicit `dts_path` argument — parse that specific .d.ts.
      2. `bifrost/lucide_icon_names.json` beside this file — the API
         container doesn't ship `client/node_modules/`, so the CLI and the
         bundler-server path both rely on this snapshot for the full
         ~5700-name set.
      3. `client/node_modules/lucide-react/dist/lucide-react.d.ts` walking
         up from this file — dev-mode fallback when running in the repo.
      4. Conservative hardcoded set — absolute-last-resort fallback.
    """
    # 1. Explicit override always takes precedence.
    if dts_path is not None and dts_path.exists():
        return _parse_lucide_dts(dts_path.read_text(encoding="utf-8"))

    # 2. Shipped JSON snapshot beside this module.
    json_path = pathlib.Path(__file__).resolve().parent / "lucide_icon_names.json"
    if json_path.exists():
        try:
            import json as _json
            return set(_json.loads(json_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    # 3. Walk upward from this file to find client/node_modules/lucide-react.
    here = pathlib.Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent.parent / "client" / "node_modules" / "lucide-react" / "dist" / "lucide-react.d.ts"
        if candidate.exists():
            return _parse_lucide_dts(candidate.read_text(encoding="utf-8"))

    # 4. Conservative fallback.
    return {
        "Phone", "Mail", "User", "Users", "Search", "Plus", "Minus",
        "X", "Check", "Building", "Building2", "Home", "Settings",
        "Trash", "Trash2", "Edit", "Edit2", "Edit3", "Save",
        "ChevronLeft", "ChevronRight", "ChevronUp", "ChevronDown",
        "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
    }


def _parse_lucide_dts(text: str) -> set[str]:
    """Extract Lucide icon names from the raw .d.ts text."""
    names: set[str] = set()
    names.update(
        re.findall(
            r"^declare const ([A-Z][A-Za-z0-9]*): react\.ForwardRefExoticComponent",
            text,
            re.MULTILINE,
        )
    )
    names.update(re.findall(r"\bas\s+([A-Z][A-Za-z0-9]*)\b", text))
    return names


# ---------------------------------------------------------------------------
# Import parsing
# ---------------------------------------------------------------------------

# Matches any `import { ... } from "bifrost"` or `'bifrost'` — multiline.
_BIFROST_IMPORT_RE = re.compile(
    r'import\s*\{([^}]*)\}\s*from\s*["\']bifrost["\'];?',
    re.DOTALL,
)


@dataclass
class _ParsedSpecifier:
    """One named import specifier: `Foo` or `Foo as Bar`."""
    original: str   # e.g. "Button"
    alias: str | None  # e.g. "MyButton" if aliased, else None

    @property
    def local_name(self) -> str:
        return self.alias or self.original

    def render(self) -> str:
        return f"{self.original} as {self.alias}" if self.alias else self.original


def _parse_specifiers(inner: str) -> list[_ParsedSpecifier]:
    """Parse the inside of `{ A, B as C, D }`."""
    specs: list[_ParsedSpecifier] = []
    for raw in inner.split(","):
        part = raw.strip()
        if not part:
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?$", part)
        if not m:
            continue
        specs.append(_ParsedSpecifier(original=m.group(1), alias=m.group(2)))
    return specs


def _render_named_import(specs: list[_ParsedSpecifier], module: str) -> str:
    """Render a one-line `import { A, B as C } from "module";` statement."""
    names = ", ".join(s.render() for s in specs)
    return f'import {{ {names} }} from "{module}";'


# ---------------------------------------------------------------------------
# App detection
# ---------------------------------------------------------------------------


def _is_app_dir(p: pathlib.Path) -> bool:
    if not p.is_dir():
        return False
    return (p / "_layout.tsx").exists()


def discover_apps(root: pathlib.Path) -> list[pathlib.Path]:
    """
    Given a root path:
      - If root is an app dir, return [root].
      - If root contains apps/<something>/ with _layout.tsx, return those.
      - Otherwise, treat root itself as the app (may contain zero TSX files; caller handles).
    """
    root = root.resolve()
    if _is_app_dir(root):
        return [root]

    apps_dir = root / "apps"
    if apps_dir.is_dir():
        out = [p for p in sorted(apps_dir.iterdir()) if _is_app_dir(p)]
        if out:
            return out

    # Fallback: scan children for app dirs.
    children = [p for p in sorted(root.iterdir()) if _is_app_dir(p)] if root.is_dir() else []
    if children:
        return children

    return [root]


def find_source_files(app_dir: pathlib.Path) -> list[pathlib.Path]:
    """All .tsx / .ts files under app_dir, excluding node_modules and components/."""
    out: list[pathlib.Path] = []
    for p in app_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in (".tsx", ".ts"):
            continue
        parts = set(p.parts)
        if "node_modules" in parts or ".bifrost" in parts:
            continue
        out.append(p)
    return sorted(out)


def list_user_components(app_dir: pathlib.Path) -> dict[str, str]:
    """Components at <app>/components/<Name>.{tsx,ts}, mapped to export style.

    Returns {Name: "default" | "named"}. Files with only a named export of the
    same name get "named"; everything else defaults to "default". Used to pick
    the correct import form when rewriting.
    """
    comp_dir = app_dir / "components"
    if not comp_dir.is_dir():
        return {}
    out: dict[str, str] = {}
    for p in comp_dir.iterdir():
        if not (p.is_file() and p.suffix in (".tsx", ".ts")):
            continue
        stem = p.stem
        if not (stem and stem[0].isupper()):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            out[stem] = "default"
            continue
        if _has_default_export(text):
            out[stem] = "default"
        elif _has_named_export(text, stem):
            out[stem] = "named"
        else:
            # Fall back to default; bundler will complain if wrong, but that's
            # an app-level bug, not a migration concern.
            out[stem] = "default"
    return out


_DEFAULT_EXPORT_RE = re.compile(
    r"^\s*export\s+default\b|^\s*export\s*\{\s*default\b",
    re.MULTILINE,
)


# Matches `import ... from "whatever";` including multi-line `import { a, b, c }`
# imports. We look for `from "..."` anchoring the end of each statement; whatever
# precedes it belongs to the import.
_IMPORT_END_RE = re.compile(
    r'from\s*["\'][^"\']+["\'];?\s*\n',
)


def _end_of_last_import(src: str) -> int:
    """Offset just past the last top-level import statement (0 if none).

    Unlike a line-based regex, this correctly handles multi-line imports like

        import {
          foo,
          bar,
        } from "pkg";

    so new import lines are inserted AFTER the closing `from "pkg";` rather
    than inside the braces.
    """
    last_end = 0
    for m in _IMPORT_END_RE.finditer(src):
        # Only count it if there's an `import` keyword earlier in the
        # statement (otherwise `export ... from` would trigger).
        window_start = max(0, m.start() - 400)
        window = src[window_start:m.start()]
        if re.search(r"^\s*import\b", window, re.MULTILINE):
            last_end = m.end()
    return last_end


def _has_default_export(src: str) -> bool:
    return bool(_DEFAULT_EXPORT_RE.search(src))


def _has_named_export(src: str, name: str) -> bool:
    # Matches `export function Foo`, `export const Foo`, `export class Foo`,
    # `export { Foo }`, `export { Foo as X }`.
    patterns = [
        rf"^\s*export\s+(?:async\s+)?function\s+{re.escape(name)}\b",
        rf"^\s*export\s+(?:const|let|var|class)\s+{re.escape(name)}\b",
        rf"^\s*export\s*\{{[^}}]*\b{re.escape(name)}\b",
    ]
    return any(re.search(p, src, re.MULTILINE) for p in patterns)


# ---------------------------------------------------------------------------
# Per-file migration
# ---------------------------------------------------------------------------


def _relative_component_path(source_file: pathlib.Path, app_dir: pathlib.Path, name: str) -> str:
    """
    Build a relative import path like "./components/Foo" or "../components/Foo"
    from source_file to app_dir/components/Foo.
    """
    # We don't include the extension — bundler / TS resolves it.
    target = app_dir / "components" / name
    src_dir = source_file.parent
    try:
        rel = pathlib.Path(*_relpath_parts(target, src_dir))
    except ValueError:
        # Fallback: assume co-located
        rel = pathlib.Path("./components") / name
    rel_str = str(rel).replace("\\", "/")
    if not rel_str.startswith(".") and not rel_str.startswith("/"):
        rel_str = "./" + rel_str
    return rel_str


def _relpath_parts(target: pathlib.Path, start: pathlib.Path) -> list[str]:
    """Compute a relative-path segment list from start to target (posix-style)."""
    target = target.resolve() if target.exists() else target
    # Use os.path.relpath semantics without requiring the target to exist.
    target_parts = list(target.parts)
    start_parts = list(start.resolve().parts if start.exists() else start.parts)
    # Find common prefix
    i = 0
    while i < len(target_parts) and i < len(start_parts) and target_parts[i] == start_parts[i]:
        i += 1
    up = [".."] * (len(start_parts) - i)
    down = target_parts[i:]
    if not up and not down:
        return ["."]
    if not up:
        return ["."] + down
    return up + down


def _locally_declared_names(src: str) -> set[str]:
    """Names declared locally in the file that shouldn't be auto-imported.

    Covers `const/let/var/function/class` at top level and inside blocks;
    de-scoping errors are rare here since we only skip names that look
    declared, not actual scope tracking. Good enough to avoid adding
    imports for user-defined identifiers that happen to be PascalCase.
    """
    names: set[str] = set()
    for m in re.finditer(
        r"\b(?:const|let|var|function|class|interface|type|enum)\s+([A-Z][A-Za-z0-9_]*)\b",
        src,
    ):
        names.add(m.group(1))
    # Also capture destructured bindings: `const { Foo, Bar } = ...`. Greedy
    # but skips nested rest patterns.
    for m in re.finditer(r"\b(?:const|let|var)\s*\{\s*([^}]+?)\s*\}", src):
        for part in m.group(1).split(","):
            ident = part.strip().split(":")[0].split("=")[0].strip()
            if ident and ident[0].isupper() and ident.isidentifier():
                names.add(ident)
    return names


def _extract_referenced_identifiers(src: str) -> set[str]:
    """Every PascalCase identifier that looks referenced in the source.

    Includes JSX tags AND value references (e.g. `{ icon: LayoutDashboard }`,
    `const Foo = Bar`). Used to infer platform/router/lucide/user imports
    the legacy runtime auto-injected. Skips identifiers that appear only in
    strings, comments, or import statements (the import body doesn't count
    as a use — otherwise `import { Button as MyButton }` would flag `Button`
    as a reference).
    """
    # Strip import statements first — names inside them are bindings, not uses.
    stripped = re.sub(
        r"^\s*import\b[^;]*?(?:from\s*['\"][^'\"]+['\"])?;?\s*$",
        "",
        src,
        flags=re.MULTILINE,
    )
    # Multiline imports: also strip `import { ... } from "..."` spanning lines.
    stripped = re.sub(
        r"import\s*\{[^}]*\}\s*from\s*['\"][^'\"]+['\"];?",
        "",
        stripped,
    )
    # Strip line and block comments.
    stripped = re.sub(r"//[^\n]*", "", stripped)
    stripped = re.sub(r"/\*[\s\S]*?\*/", "", stripped)
    # Strip string literals.
    stripped = re.sub(r'"[^"\n]*"', "", stripped)
    stripped = re.sub(r"'[^'\n]*'", "", stripped)
    stripped = re.sub(r"`[^`]*`", "", stripped)

    names: set[str] = set()
    for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", stripped):
        names.add(m.group(1))
    return names


def _existing_import_names(src: str) -> set[str]:
    """Collect the set of locally-bound identifier names introduced by all imports."""
    names: set[str] = set()

    # default imports: `import Name from "..."`
    for m in re.finditer(r'import\s+([A-Za-z_][A-Za-z0-9_]*)\s+from\s*["\'][^"\']+["\']', src):
        names.add(m.group(1))

    # default + named: `import Default, { Named } from "..."`
    for m in re.finditer(
        r'import\s+([A-Za-z_][A-Za-z0-9_]*)\s*,\s*\{([^}]*)\}\s*from\s*["\'][^"\']+["\']',
        src, flags=re.DOTALL,
    ):
        names.add(m.group(1))
        for spec in _parse_specifiers(m.group(2)):
            names.add(spec.local_name)

    # named only: `import { A, B as C } from "..."`
    for m in re.finditer(r'import\s*\{([^}]*)\}\s*from\s*["\'][^"\']+["\']', src, flags=re.DOTALL):
        for spec in _parse_specifiers(m.group(1)):
            names.add(spec.local_name)

    # namespace: `import * as Name from "..."`
    for m in re.finditer(r'import\s*\*\s*as\s+([A-Za-z_][A-Za-z0-9_]*)\s*from\s*["\'][^"\']+["\']', src):
        names.add(m.group(1))

    return names


def _merge_named_import(
    src: str,
    module: str,
    new_specs: list[_ParsedSpecifier],
    insert_after_offset: int,
) -> str:
    """
    If `src` already has a `import {...} from "<module>"`, merge new_specs into it
    (deduping by local_name). Otherwise insert a new import line at insert_after_offset.
    """
    if not new_specs:
        return src

    # Look for the first existing named import from this module.
    pattern = re.compile(
        r'import\s*\{([^}]*)\}\s*from\s*["\']' + re.escape(module) + r'["\'];?',
        re.DOTALL,
    )
    match = pattern.search(src)
    if match:
        existing = _parse_specifiers(match.group(1))
        seen = {s.local_name for s in existing}
        for spec in new_specs:
            if spec.local_name not in seen:
                existing.append(spec)
                seen.add(spec.local_name)
        rendered = _render_named_import(existing, module)
        return src[:match.start()] + rendered + src[match.end():]

    # Insert as a new line right after insert_after_offset (usually end of the
    # replaced bifrost import, or top of file).
    line = _render_named_import(new_specs, module)
    # Ensure we land on a fresh line
    before = src[:insert_after_offset]
    after = src[insert_after_offset:]
    sep = "" if before.endswith("\n") else "\n"
    tail_sep = "" if after.startswith("\n") else "\n"
    return before + sep + line + tail_sep + after


# Navigation primitives that MUST be imported from "bifrost" (platform
# wrappers), NOT raw "react-router-dom". Kept as a tuple so membership
# checks stay cheap and the intent is documented inline.
_PLATFORM_WRAPPED_NAMES: frozenset[str] = frozenset({
    "Link", "NavLink", "Navigate", "useNavigate", "navigate",
})


def _rescue_platform_wrapped_names(src: str) -> str:
    """Rewrite any `from "react-router-dom"` imports that contain the 5
    platform-wrapped navigation names, moving just those names to a
    `from "bifrost"` import.

    Recovers apps that a previous (buggy) migration routed through raw
    react-router-dom, which bypassed the platform `transformPath` wrappers
    and navigated `<Link to="/email">` to `/email` absolute instead of
    `/apps/<slug>/preview/email`. Idempotent: if there are no platform-
    wrapped names in any react-router-dom import, returns src unchanged.
    """
    rr_pattern = re.compile(
        r'import\s*\{([^}]*)\}\s*from\s*["\']react-router-dom["\'];?',
        re.DOTALL,
    )

    rescued: list[_ParsedSpecifier] = []
    def _rewrite_rr(m: re.Match[str]) -> str:
        specs = _parse_specifiers(m.group(1))
        keep: list[_ParsedSpecifier] = []
        for s in specs:
            if s.original in _PLATFORM_WRAPPED_NAMES:
                rescued.append(s)
            else:
                keep.append(s)
        if not rescued or len(keep) == len(specs):
            # Nothing to rescue from THIS import; leave it alone.
            return m.group(0)
        if not keep:
            # Entire react-router-dom import was platform-wrapped names —
            # drop the line entirely (trailing newline swallowed by the
            # caller's cleanup pass if needed).
            return ""
        return _render_named_import(keep, "react-router-dom")

    new_src = rr_pattern.sub(_rewrite_rr, src)
    if not rescued:
        return src

    # Merge rescued names into an existing `from "bifrost"` import if one
    # exists; otherwise insert a fresh line after the last top-level import.
    # Dedupe by local_name so a file that already has `Link` from bifrost
    # plus `Link` from react-router-dom (shouldn't happen, but be safe)
    # doesn't produce a duplicate.
    new_src = _merge_named_import(
        new_src, "bifrost", rescued, _end_of_last_import(new_src),
    )
    return new_src


def migrate_file(
    source_file: pathlib.Path,
    app_dir: pathlib.Path,
    user_components: dict[str, str],
    platform_names: AbstractSet[str],
    lucide_names: AbstractSet[str],
) -> FileMigrationResult:
    """
    Apply all migrations to a single source file.
    """
    original = source_file.read_text(encoding="utf-8")
    # Reverse pass first: recover the 5 platform-wrapped navigation primitives
    # that a previous (buggy) migration may have moved to "react-router-dom".
    # Done before the main pass so the downstream classification logic sees
    # them as "bifrost" imports and leaves them there (platform_names has
    # them, so they stay).
    src = _rescue_platform_wrapped_names(original)
    moved_icons = 0
    moved_router = 0
    added_components = 0
    shadow_warnings: list[str] = []

    # Track which user-component names appeared in a bifrost import (for
    # rewriting as default-imports).
    user_component_rewrites: list[tuple[str, str | None]] = []  # (original, alias)

    def classify_bifrost_import(match: re.Match[str]) -> str:
        nonlocal moved_icons, moved_router, src  # src is captured outer
        inner = match.group(1)
        specs = _parse_specifiers(inner)

        keep_bifrost: list[_ParsedSpecifier] = []
        router_specs: list[_ParsedSpecifier] = []
        lucide_specs: list[_ParsedSpecifier] = []

        for spec in specs:
            name = spec.original
            # Precedence (first match wins):
            # 1. User component — local components/<Name>.tsx always wins.
            # 2. React Router primitive — Link/NavLink/Navigate/useNavigate.
            # 3. Platform name — shadcn components, hooks, etc. Platform
            #    wins over Lucide when the name collides (e.g. `Badge`
            #    and `Sheet` exist in both lucide-react AND as shadcn
            #    components; from "bifrost" they always mean the shadcn one).
            # 4. Lucide icon.
            # 5. Unknown — keep in "bifrost" as a passthrough.
            if name in user_components:
                if (
                    name in _ROUTER_NAMES
                    or name in _PLATFORM_WRAPPED_NAMES
                    or name in lucide_names
                    or name in platform_names
                ):
                    shadow_warnings.append(name)
                user_component_rewrites.append((name, spec.alias))
                continue
            if name in _ROUTER_NAMES:
                router_specs.append(spec)
                continue
            if name in platform_names:
                keep_bifrost.append(spec)
                continue
            if name in lucide_names:
                lucide_specs.append(spec)
                continue
            keep_bifrost.append(spec)

        moved_router_local = len(router_specs)
        moved_icons_local = len(lucide_specs)

        # Build replacement text in-place. The merge step for lucide / router
        # happens after the whole substitution, since it needs to know about
        # sibling imports elsewhere in the file. Store the specs for later.
        _pending_router.extend(router_specs)
        _pending_lucide.extend(lucide_specs)

        moved_router += moved_router_local
        moved_icons += moved_icons_local

        if keep_bifrost:
            return _render_named_import(keep_bifrost, "bifrost")
        return ""  # caller strips leading whitespace / blank line if needed

    # Work queues populated by classify_bifrost_import (closure over these):
    _pending_router: list[_ParsedSpecifier] = []
    _pending_lucide: list[_ParsedSpecifier] = []

    # Collect all bifrost import locations, process them, build new src
    new_src_parts: list[str] = []
    cursor = 0
    last_replacement_end = 0

    for match in _BIFROST_IMPORT_RE.finditer(src):
        new_src_parts.append(src[cursor:match.start()])
        replacement = classify_bifrost_import(match)
        new_src_parts.append(replacement)
        cursor = match.end()
        last_replacement_end = len(''.join(new_src_parts))
        # If the replacement is empty and the following chars are a newline,
        # consume that newline so we don't leave a blank line behind.
        if replacement == "" and cursor < len(src) and src[cursor] == "\n":
            cursor += 1

    new_src_parts.append(src[cursor:])
    src = "".join(new_src_parts)

    # Insert merged lucide / router imports.
    # Anchor: right after the last bifrost import we processed (or top of file).
    insert_at = last_replacement_end if last_replacement_end > 0 else 0
    # If we didn't touch bifrost imports at all, insert after the final import
    # block at top of file — find the last `from "..."` line.
    if insert_at == 0 and (_pending_router or _pending_lucide):
        last_import_match = None
        for m in re.finditer(r'^import[^\n]*\n', src, flags=re.MULTILINE):
            last_import_match = m
        if last_import_match:
            insert_at = last_import_match.end()

    if _pending_router:
        src = _merge_named_import(src, "react-router-dom", _pending_router, insert_at)
    if _pending_lucide:
        # Recompute insert_at — src may have shifted.
        src = _merge_named_import(src, "lucide-react", _pending_lucide, insert_at)

    # --- User component rewrites (default imports) ---
    # For each (name, alias), add `import <alias_or_name> from "./components/Name"`
    # if not already present.
    if user_component_rewrites:
        existing_defaults: set[str] = set()
        for m in re.finditer(
            r'import\s+([A-Za-z_][A-Za-z0-9_]*)\s+from\s*["\'][^"\']+["\']', src
        ):
            existing_defaults.add(m.group(1))

        lines_to_add: list[str] = []
        for original_name, alias in user_component_rewrites:
            local = alias or original_name
            if local in existing_defaults:
                continue
            rel = _relative_component_path(source_file, app_dir, original_name)
            export_style = user_components.get(original_name, "default")
            if export_style == "named":
                # Named-export component — `import { Foo } from "./components/Foo"`.
                if alias:
                    lines_to_add.append(
                        f'import {{ {original_name} as {alias} }} from "{rel}";'
                    )
                else:
                    lines_to_add.append(
                        f'import {{ {original_name} }} from "{rel}";'
                    )
            else:
                # Default export — standard default import.
                lines_to_add.append(f'import {local} from "{rel}";')
            existing_defaults.add(local)

        if lines_to_add:
            # Insert near the top of the file, after the last existing import
            # (or at position 0).
            last_import_end = 0
            for m in re.finditer(r'^import[^\n]*\n', src, flags=re.MULTILINE):
                last_import_end = m.end()
            insertion = "\n".join(lines_to_add) + "\n"
            if last_import_end == 0:
                src = insertion + src
            else:
                src = src[:last_import_end] + insertion + src[last_import_end:]

    # --- Missing import inference from identifier usage ---
    # The legacy runtime auto-injected `$` scope into every file, so user
    # code like `<Outlet />`, `icon: LayoutDashboard`, `<Button>` worked
    # without imports. Post-migration we need explicit imports for every
    # platform/router/lucide/user-component name actually referenced.
    used_names = _extract_referenced_identifiers(src)
    existing_names = _existing_import_names(src) | _locally_declared_names(src)
    inferred_user: list[str] = []
    inferred_platform: list[str] = []
    inferred_router: list[str] = []
    inferred_lucide: list[str] = []
    for name in sorted(used_names):
        if not name:
            continue
        if name in existing_names:
            continue
        # User-component lookup only considers PascalCase names (components
        # must start uppercase). Platform/router/lucide lookups accept any
        # case — platform exports `cn`, `toast`, `clsx`, `format`, etc.
        if name[0].isupper() and name in user_components:
            inferred_user.append(name)
        elif name in _ROUTER_NAMES:
            inferred_router.append(name)
        elif name in platform_names:
            inferred_platform.append(name)
        elif name[0].isupper() and name in lucide_names:
            inferred_lucide.append(name)

    lines_to_add: list[str] = []
    for name in inferred_user:
        rel = _relative_component_path(source_file, app_dir, name)
        export_style = user_components.get(name, "default")
        if export_style == "named":
            lines_to_add.append(f'import {{ {name} }} from "{rel}";')
        else:
            lines_to_add.append(f'import {name} from "{rel}";')

    # Merge into existing bucket imports if present; otherwise new lines.
    if inferred_router:
        src = _merge_named_import(
            src, "react-router-dom",
            [_ParsedSpecifier(original=n, alias=None) for n in inferred_router],
            _end_of_last_import(src),
        )
    if inferred_platform:
        src = _merge_named_import(
            src, "bifrost",
            [_ParsedSpecifier(original=n, alias=None) for n in inferred_platform],
            _end_of_last_import(src),
        )
    if inferred_lucide:
        src = _merge_named_import(
            src, "lucide-react",
            [_ParsedSpecifier(original=n, alias=None) for n in inferred_lucide],
            _end_of_last_import(src),
        )

    added_components = (
        len(inferred_user)
        + len(inferred_platform)
        + len(inferred_router)
        + len(inferred_lucide)
    )

    if lines_to_add:
        last_import_end = _end_of_last_import(src)
        insertion = "\n".join(lines_to_add) + "\n"
        if last_import_end == 0:
            src = insertion + src
        else:
            src = src[:last_import_end] + insertion + src[last_import_end:]

    # Collapse any run of 3+ newlines the rewrites may have introduced.
    src = re.sub(r"\n{3,}", "\n\n", src)

    return FileMigrationResult(
        path=source_file,
        original=original,
        updated=src,
        moved_icons=moved_icons,
        moved_router=moved_router,
        added_components=added_components,
        user_shadow_warnings=sorted(set(shadow_warnings)),
    )


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def migrate_app(
    app_dir: pathlib.Path,
    platform_names: AbstractSet[str],
    lucide_names: AbstractSet[str],
) -> list[FileMigrationResult]:
    """Run migration on every TSX/TS file in an app, return results (changed + unchanged)."""
    user_components = list_user_components(app_dir)
    results: list[FileMigrationResult] = []
    for src_file in find_source_files(app_dir):
        results.append(
            migrate_file(src_file, app_dir, user_components, platform_names, lucide_names)
        )
    return results


def render_diff(result: FileMigrationResult) -> str:
    """Unified diff of a file migration result."""
    rel = str(result.path)
    diff = difflib.unified_diff(
        result.original.splitlines(keepends=True),
        result.updated.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    )
    return "".join(diff)
