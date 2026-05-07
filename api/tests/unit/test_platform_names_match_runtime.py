"""
Drift test: every key in the client's `$` registry is in PLATFORM_EXPORT_NAMES.

The client's `$` registry (`client/src/lib/app-code-runtime.ts`) is what the
browser injects as the `bifrost` platform scope for bundled apps. The Python
side — both the esbuild bundler and the `bifrost migrate-imports` classifier —
consults `bifrost.platform_names.PLATFORM_EXPORT_NAMES` to decide which
imported names route through the platform scope vs. lucide-react vs. local
user components.

If a new platform export ships to the runtime but the Python list doesn't
know about it, `bifrost migrate-imports` will misclassify it (moving it to
lucide-react, or adding a bogus relative import) and the bundler's synthesized
`node_modules/bifrost/index.js` will not re-export it. The original
`KNOWN_BIFROST_EXPORTS` list rotted this way and produced the "`toast` is
not available" false-positive flood.

Extraction strategy (regex-based, documented because we're not using a real
TS parser):
1. Locate `export const $: Record<string, unknown> = { ... };` in app-code-runtime.ts.
2. Walk entries. Direct identifiers (e.g. `React`, `format`) count as keys.
   Shorthand `X,` or `X: Y,` both mean `X` is a key.
3. Spread entries `...X` resolve as follows:
   - `...React`         → SKIP. React namespace; hooks already enumerated
                         individually in PLATFORM_EXPORT_NAMES.
   - `...LucideIcons`   → SKIP. Lucide has ~1000 icons; they are handled by
                         the bundler/classifier via the lucide-react resolution
                         path, not by PLATFORM_EXPORT_NAMES.
   - `...createPlatformScope()` → parse the return-object literal of
                         `createPlatformScope` in scope.ts.
   - `...reactRouterExports` → parse the object literal assigned to
                         `reactRouterExports` in app-code-runtime.ts itself.
   - `...utils`         → parse `export` declarations in utils.ts.
   - `...XxxModule`     → resolve `import * as XxxModule from "<path>"`,
                         then parse `export { A, B }` blocks and
                         `export function Foo` / `export const Foo` in that file.

The assumption this test rests on is that the `$` registry is a literal
object (not assembled imperatively) and that the modules spread into it
expose names via `export { ... }` blocks or `export function`/`export const`
declarations. Both hold in the codebase today; if they change, this test
needs an update — which is the point: silent drift is impossible.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from bifrost.platform_names import PLATFORM_EXPORT_NAMES


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# api/tests/unit/test_platform_names_match_runtime.py
#   -> api/ -> repo root -> client/
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENT_SRC = _REPO_ROOT / "client" / "src"
_APP_CODE_RUNTIME = _CLIENT_SRC / "lib" / "app-code-runtime.ts"


def _resolve_client_import(module_path: str, from_file: Path) -> Path | None:
    """Map a TS import specifier to a concrete file under client/src.

    Handles `@/...` (= client/src/...) and relative `./`, `../` imports.
    Tries `.ts`, `.tsx`, and `/index.ts{,x}` suffixes.
    """
    if module_path.startswith("@/"):
        base = _CLIENT_SRC / module_path[2:]
    elif module_path.startswith("."):
        base = (from_file.parent / module_path).resolve()
    else:
        return None  # external package, we don't resolve these

    for suffix in (".ts", ".tsx"):
        candidate = base.with_suffix(suffix)
        if candidate.exists():
            return candidate
    for suffix in ("index.ts", "index.tsx"):
        candidate = base / suffix
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# TS source parsing helpers
# ---------------------------------------------------------------------------


_NAMESPACE_IMPORT_RE = re.compile(
    r'import\s+\*\s+as\s+([A-Za-z_][A-Za-z0-9_]*)\s+from\s+["\']([^"\']+)["\']',
)


def _find_namespace_import(src: str, alias: str) -> str | None:
    """Return the module path for `import * as <alias> from "..."`, or None."""
    for m in _NAMESPACE_IMPORT_RE.finditer(src):
        if m.group(1) == alias:
            return m.group(2)
    return None


def _extract_module_exports(src: str) -> set[str]:
    """Extract exported names from a TS module source.

    Handles:
      - `export function Foo(`
      - `export async function Foo(`
      - `export const Foo =`, `export let Foo =`, `export var Foo =`
      - `export class Foo`
      - `export { A, B, C }` blocks (including multi-line)
      - `export { A as B }` (emits the alias `B`)

    Skips `export interface`, `export type`, `export enum`, `export default`
    (those aren't named value exports that would show up on a namespace import
    object usable by `$`).
    """
    names: set[str] = set()

    # export function/class/const/let/var Name
    for m in re.finditer(
        r"^\s*export\s+(?:async\s+)?(?:function|class|const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)",
        src,
        re.MULTILINE,
    ):
        names.add(m.group(1))

    # export { A, B as C, D }
    for m in re.finditer(
        r"export\s*\{([^}]*)\}\s*(?:from\s*[\"'][^\"']+[\"'])?\s*;?",
        src,
    ):
        for raw in m.group(1).split(","):
            part = raw.strip()
            if not part:
                continue
            # Handle `type X` modifier
            part = re.sub(r"^type\s+", "", part)
            # `A as B` → export name is B
            if " as " in part:
                _, _, after = part.partition(" as ")
                name = after.strip()
            else:
                name = part
            # Strip trailing comments etc.
            m2 = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", name)
            if m2:
                names.add(m2.group(1))

    return names


def _extract_return_object_keys(src: str, fn_name: str) -> set[str]:
    """Extract keys from the object literal returned by `export function <fn_name>`.

    Used for `createPlatformScope` which returns a literal `{ ... }`.
    The first `return {` after the function declaration is taken; we balance
    braces to find the matching close.
    """
    fn_match = re.search(
        rf"export\s+function\s+{re.escape(fn_name)}\s*\([^)]*\)\s*:\s*[^{{]*\{{",
        src,
    )
    if not fn_match:
        return set()
    body_start = fn_match.end()
    return_match = re.search(r"return\s*\{", src[body_start:])
    if not return_match:
        return set()
    obj_start = body_start + return_match.end()
    return _keys_from_object_literal(src, obj_start)


def _find_const_object(src: str, const_name: str) -> int | None:
    """Return the offset just past `const <name> = {` (opening brace), or None."""
    m = re.search(
        rf"(?:const|let|var)\s+{re.escape(const_name)}\s*(?::[^=]+)?=\s*\{{",
        src,
    )
    return m.end() if m else None


def _keys_from_object_literal(src: str, open_brace_end: int) -> set[str]:
    """Given the offset just PAST an opening `{`, extract top-level key names.

    Walks the string balancing braces/parens/brackets and string literals so
    nested objects / function calls don't confuse key detection. Top-level
    entries are split on commas at depth 0.

    Recognizes:
      - shorthand: `Foo,`     -> "Foo"
      - named:    `Foo: ...,` -> "Foo"
      - quoted:   `"Foo": ...`-> "Foo"
      - spread:   `...Bar,`   -> treated as a spread marker (returned as
                                 `"...Bar"` so callers can handle it).
    """
    depth = 1
    i = open_brace_end
    n = len(src)
    entries: list[str] = []
    current = []
    in_str: str | None = None
    in_line_comment = False
    in_block_comment = False
    escape = False

    while i < n and depth > 0:
        ch = src[i]

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and i + 1 < n and src[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_str is not None:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
            current.append(ch)
            i += 1
            continue

        # Detect comment start
        if ch == "/" and i + 1 < n:
            nxt = src[i + 1]
            if nxt == "/":
                in_line_comment = True
                i += 2
                continue
            if nxt == "*":
                in_block_comment = True
                i += 2
                continue

        if ch in ("'", '"', "`"):
            in_str = ch
            current.append(ch)
            i += 1
            continue
        if ch == "{":
            depth += 1
            current.append(ch)
            i += 1
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                entries.append("".join(current))
                i += 1
                break
            current.append(ch)
            i += 1
            continue
        if ch in "([":
            depth += 1
            current.append(ch)
            i += 1
            continue
        if ch in ")]":
            depth -= 1
            current.append(ch)
            i += 1
            continue
        if ch == "," and depth == 1:
            entries.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1

    names: set[str] = set()
    for raw in entries:
        entry = raw.strip()
        if not entry:
            continue
        # Spread: `...X` — report as `"...X"` so the caller can decide.
        spread_m = re.match(r"^\.\.\.\s*([A-Za-z_][A-Za-z0-9_]*)", entry)
        if spread_m:
            names.add("..." + spread_m.group(1))
            continue
        # "Quoted": "Foo"
        quoted_m = re.match(r'^["\']([A-Za-z_][A-Za-z0-9_]*)["\']\s*:', entry)
        if quoted_m:
            names.add(quoted_m.group(1))
            continue
        # Named: Foo: ... OR shorthand: Foo[,}]
        key_m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", entry)
        if key_m:
            names.add(key_m.group(1))
    return names


# ---------------------------------------------------------------------------
# Registry resolution
# ---------------------------------------------------------------------------

# Spreads we intentionally do not resolve — these don't belong in
# PLATFORM_EXPORT_NAMES by design (see module docstring).
_SPREAD_SKIPS = {"React", "LucideIcons"}


# Names provided by `...React` in the runtime `$` registry. The parser skips
# `...React` (see _SPREAD_SKIPS) because enumerating every React export is
# fragile, but PLATFORM_EXPORT_NAMES *does* list these explicitly so that
# the bundler/classifier can route them. They are wired into the runtime
# via the React namespace spread; the reverse-direction drift test below
# excludes them from its check.
#
# Keep this in sync with the "React" block at the top of
# bifrost/platform_names.py. If a new React API is added there, add it here
# too — or move it out of the React block if it has its own runtime entry.
_REACT_NAMESPACE_NAMES: frozenset[str] = frozenset({
    "React", "Fragment", "Suspense", "lazy", "memo", "forwardRef",
    "useState", "useEffect", "useCallback", "useMemo", "useRef",
    "useContext", "useReducer", "useLayoutEffect", "useId",
    "useTransition", "useDeferredValue", "useImperativeHandle",
})


# Names that PLATFORM_EXPORT_NAMES lists but are intentionally provided via
# the `...LucideIcons` spread rather than a discrete entry in the `$` registry.
# The parser skips `...LucideIcons` (see _SPREAD_SKIPS), so the reverse-direction
# drift test must explicitly excuse these. Documented in platform_names.py.
_LUCIDE_PROVIDED_NAMES: frozenset[str] = frozenset({
    # `Calendar` from "bifrost" resolves to the Lucide calendar icon, NOT a
    # picker component. Apps that want a date picker import `CalendarPicker`.
    "Calendar",
})


def _resolve_registry_keys() -> set[str]:
    """Return every key present in the client's `$` registry.

    Walks the registry object literal; for each spread, resolves through
    imports / local declarations per the rules in the module docstring.
    """
    assert _APP_CODE_RUNTIME.exists(), f"Missing: {_APP_CODE_RUNTIME}"
    runtime_src = _APP_CODE_RUNTIME.read_text(encoding="utf-8")

    # Find the `$` registry. Form: `export const $: Record<string, unknown> = {`
    registry_match = re.search(
        r"export\s+const\s+\$\s*:\s*Record<[^>]+>\s*=\s*\{",
        runtime_src,
    )
    assert registry_match, (
        "Could not locate `export const $: Record<string, unknown> = {` in "
        "app-code-runtime.ts. If the registry shape changed, update this test."
    )

    raw_entries = _keys_from_object_literal(runtime_src, registry_match.end())

    keys: set[str] = set()
    for entry in raw_entries:
        if entry.startswith("..."):
            spread_name = entry[3:]
            if spread_name in _SPREAD_SKIPS:
                continue
            keys.update(_resolve_spread(spread_name, runtime_src))
        else:
            keys.add(entry)
    return keys


def _resolve_spread(name: str, runtime_src: str) -> set[str]:
    """Resolve `...<name>` inside the `$` registry to the set of keys it adds."""
    # Case 1: call expression — only `createPlatformScope()` matters today.
    # The `$` parser drops the trailing `()` so spreads like
    # `...createPlatformScope()` arrive here as `createPlatformScope`.
    if name == "createPlatformScope":
        scope_path = _resolve_client_import("./app-code-platform/scope", _APP_CODE_RUNTIME)
        assert scope_path, "Could not resolve ./app-code-platform/scope"
        return _extract_return_object_keys(
            scope_path.read_text(encoding="utf-8"),
            "createPlatformScope",
        )

    # Case 2: local object literal constant — e.g. `reactRouterExports`.
    open_end = _find_const_object(runtime_src, name)
    if open_end is not None:
        keys = _keys_from_object_literal(runtime_src, open_end)
        # Strip spreads inside (none expected here).
        return {k for k in keys if not k.startswith("...")}

    # Case 3: namespace import — `import * as <name> from "..."`.
    mod_path = _find_namespace_import(runtime_src, name)
    if mod_path is not None:
        resolved = _resolve_client_import(mod_path, _APP_CODE_RUNTIME)
        assert resolved, (
            f"Could not resolve module {mod_path!r} referenced by `...{name}` "
            f"spread in app-code-runtime.ts. Update this test if the import "
            f"path changed."
        )
        return _extract_module_exports(resolved.read_text(encoding="utf-8"))

    raise AssertionError(
        f"Unrecognized spread `...{name}` in `$` registry. The drift test "
        f"doesn't know how to resolve it — update "
        f"api/tests/unit/test_platform_names_match_runtime.py to handle it."
    )


# ---------------------------------------------------------------------------
# The actual test
# ---------------------------------------------------------------------------


def test_every_runtime_registry_key_is_in_platform_export_names() -> None:
    """PLATFORM_EXPORT_NAMES must be a superset of every key in `$`.

    When this fails: a new platform export was added to the client runtime
    without being added to `bifrost/platform_names.py`. Either add it to
    `PLATFORM_EXPORT_NAMES`, or (rarely) document why it should be skipped
    (e.g. another React-namespace-like case).
    """
    runtime_keys = _resolve_registry_keys()
    # Sanity check: we should find a reasonable number of keys. If the parser
    # silently finds none, the test passes vacuously — guard against that.
    assert len(runtime_keys) > 40, (
        f"Drift test found only {len(runtime_keys)} keys in the `$` registry; "
        f"parser is almost certainly broken. Keys: {sorted(runtime_keys)}"
    )

    missing = runtime_keys - PLATFORM_EXPORT_NAMES
    assert not missing, (
        "The client's `$` platform-scope registry exposes names that are "
        "missing from bifrost.platform_names.PLATFORM_EXPORT_NAMES:\n  "
        + ", ".join(sorted(missing))
        + "\nAdd them to PLATFORM_EXPORT_NAMES (or update this test's "
        "_SPREAD_SKIPS / resolver if they're intentionally excluded)."
    )


def test_every_platform_export_name_is_in_runtime_registry() -> None:
    """Every PLATFORM_EXPORT_NAMES entry must be wired into the `$` registry.

    The reverse direction of the check above. When this fails: a name was
    declared in `bifrost/platform_names.py` (so the validator allows
    `import { X } from "bifrost"` and the bundler routes it through the
    platform scope), but the runtime `$` registry doesn't actually inject
    it — so `X` resolves to `undefined` at runtime and any call site throws.

    This is exactly how `toast` shipped broken (issue #203): listed in
    PLATFORM_EXPORT_NAMES, imported in app-code-platform/components.ts,
    documented in platform-api.md and llms.txt — but never added to the
    `$` registry's Utilities section. The original one-way check passed
    vacuously.

    React-namespace names (`useState`, `useEffect`, …) are excluded because
    they're injected via `...React` in the registry; the parser skips that
    spread (see `_SPREAD_SKIPS`).
    """
    runtime_keys = _resolve_registry_keys()
    expected = PLATFORM_EXPORT_NAMES - _REACT_NAMESPACE_NAMES - _LUCIDE_PROVIDED_NAMES

    missing = expected - runtime_keys
    assert not missing, (
        "The following names are listed in PLATFORM_EXPORT_NAMES but are "
        "missing from the client's `$` platform-scope registry "
        "(client/src/lib/app-code-runtime.ts):\n  "
        + ", ".join(sorted(missing))
        + "\nEither wire them into the `$` registry (so apps that import "
        "them actually get a value), or remove them from PLATFORM_EXPORT_NAMES "
        "if they were never meant to ship."
    )


if __name__ == "__main__":
    # Convenience: run as a script to print the diff.
    keys = _resolve_registry_keys()
    print(f"Resolved {len(keys)} keys from `$` registry")
    missing = keys - PLATFORM_EXPORT_NAMES
    extra = PLATFORM_EXPORT_NAMES - keys
    if missing:
        print("MISSING from PLATFORM_EXPORT_NAMES:", sorted(missing))
    if extra:
        print("In PLATFORM_EXPORT_NAMES but not in `$`:", sorted(extra))
    if not missing:
        pytest.skip("No drift detected.")
