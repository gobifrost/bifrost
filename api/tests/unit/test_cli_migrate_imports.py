"""Unit tests for `bifrost migrate-imports` logic."""
from __future__ import annotations

import pathlib
import sys

# Ensure the standalone bifrost CLI package is importable.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from bifrost.migrate_imports import (  # noqa: E402
    discover_apps,
    migrate_app,
    migrate_file,
)


# Minimal platform-name set used by the tests. Only needed for shadow warnings.
PLATFORM = {
    "Button", "Card", "CardHeader", "CardTitle", "CardContent",
    "Alert", "Badge", "Skeleton", "useState", "useWorkflowQuery", "Outlet",
}

# Minimal lucide icon set used by the tests. Real classifier uses the full
# lucide-react export set; for tests we only need the names we're asserting on.
LUCIDE = {
    "Phone", "Mail", "Users", "Building2", "Trash2", "X",
}


def _write(p: pathlib.Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _make_app(tmp_path: pathlib.Path, components: dict[str, str] | None = None) -> pathlib.Path:
    """Create a minimal app dir at tmp_path/apps/my-app. Returns the app dir."""
    app_dir = tmp_path / "apps" / "my-app"
    _write(app_dir / "_layout.tsx", "export default () => null;\n")
    for name, body in (components or {}).items():
        _write(app_dir / "components" / f"{name}.tsx", body)
    return app_dir


# ---------------------------------------------------------------------------
# 1. Simple single-line rewrite
# ---------------------------------------------------------------------------


def test_simple_rewrite(tmp_path: pathlib.Path) -> None:
    # `Button` is platform (stays in bifrost), `Phone` is lucide (moves), and
    # `Link` is platform-wrapped navigation (MUST stay in bifrost so the
    # bundler's wrapped_router_names path routes it through the platform
    # wrappers; raw react-router-dom Link navigates to absolute paths
    # without prepending /apps/<slug>/preview).
    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, 'import { Button, Phone, Link } from "bifrost";\n\nexport default function L(){return null;}\n')

    results = migrate_app(app, PLATFORM, LUCIDE)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    # Button + Link stay in bifrost; order may vary, so assert membership
    # on the parsed specifier list.
    bifrost_line = next(
        line for line in updated.splitlines()
        if line.startswith("import") and 'from "bifrost"' in line
    )
    assert "Button" in bifrost_line
    assert "Link" in bifrost_line
    assert 'import { Phone } from "lucide-react";' in updated
    assert 'from "react-router-dom"' not in updated


# ---------------------------------------------------------------------------
# 2. Multi-line import rewrite
# ---------------------------------------------------------------------------


def test_multiline_import_rewrite(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, (
        "import {\n"
        "  useState, useWorkflowQuery, Card, CardHeader, CardTitle, CardContent,\n"
        "  Skeleton, Alert, Building2, Users\n"
        '} from "bifrost";\n'
        "\n"
        "export default function L(){return null;}\n"
    ))

    results = migrate_app(app, PLATFORM, LUCIDE)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    # bifrost keeps platform names
    assert 'useState' in updated
    assert 'useWorkflowQuery' in updated
    assert 'Card' in updated
    # Icons moved to lucide-react (single merged line)
    assert 'import { Building2, Users } from "lucide-react";' in updated
    # The original multi-line import statement should be gone
    assert 'Building2, Users\n}' not in updated
    assert 'import { \n' not in updated  # no leftover broken brackets


# ---------------------------------------------------------------------------
# 3. Aliased import preservation
# ---------------------------------------------------------------------------


def test_aliased_import_preserved(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, 'import { Button as MyButton, Trash2 as DeleteIcon } from "bifrost";\n')

    results = migrate_app(app, PLATFORM, LUCIDE)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    assert 'import { Button as MyButton } from "bifrost";' in updated
    assert 'import { Trash2 as DeleteIcon } from "lucide-react";' in updated


# ---------------------------------------------------------------------------
# 4. User component rewrite (default import with relative path)
# ---------------------------------------------------------------------------


def test_user_component_rewrite(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path, components={
        "SearchInput": "export default function SearchInput(){return null;}\n",
    })
    src_file = app / "_layout.tsx"
    _write(src_file, 'import { SearchInput, Button } from "bifrost";\n')

    results = migrate_app(app, PLATFORM, LUCIDE)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    assert 'import { Button } from "bifrost";' in updated
    assert 'import SearchInput from "./components/SearchInput";' in updated
    # SearchInput must no longer be in the bifrost import
    assert 'SearchInput, Button' not in updated
    assert 'Button, SearchInput' not in updated


# ---------------------------------------------------------------------------
# 5. Missing-import inference from JSX
# ---------------------------------------------------------------------------


def test_missing_import_inferred_from_jsx(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path, components={
        "SearchInput": "export default function SearchInput(){return null;}\n",
    })
    src_file = app / "pages" / "index.tsx"
    _write(src_file, (
        'import { Card } from "bifrost";\n'
        "\n"
        "export default function Page(){\n"
        "  return <Card><SearchInput /></Card>;\n"
        "}\n"
    ))

    results = migrate_app(app, PLATFORM, LUCIDE)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    # Relative path from pages/index.tsx to components/SearchInput is "../components/SearchInput"
    assert 'import SearchInput from "../components/SearchInput";' in updated
    # Card is still imported from bifrost
    assert 'import { Card } from "bifrost";' in updated
    assert changed[0].added_components == 1


def test_missing_import_ignored_when_component_file_missing(tmp_path: pathlib.Path) -> None:
    """Tags not matching any components/ file are left alone."""
    app = _make_app(tmp_path)
    src_file = app / "pages" / "index.tsx"
    _write(src_file, (
        'import { Card } from "bifrost";\n'
        "export default function Page(){\n"
        "  return <Card><DoesNotExist /></Card>;\n"
        "}\n"
    ))

    results = migrate_app(app, PLATFORM, LUCIDE)
    changed = [r for r in results if r.changed]
    assert changed == []


# ---------------------------------------------------------------------------
# 6. User component shadowing warning
# ---------------------------------------------------------------------------


def test_user_component_shadows_platform_warning(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path, components={
        "Link": "export default function Link(){return null;}\n",
    })
    src_file = app / "_layout.tsx"
    _write(src_file, 'import { Link, Button } from "bifrost";\n')

    results = migrate_app(app, PLATFORM, LUCIDE)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    r = changed[0]

    assert "Link" in r.user_shadow_warnings
    assert 'import Link from "./components/Link";' in r.updated
    assert 'import { Button } from "bifrost";' in r.updated
    # Link must NOT have been routed to react-router-dom
    assert 'react-router-dom' not in r.updated


# ---------------------------------------------------------------------------
# 7. Idempotency — running twice does nothing on the second pass
# ---------------------------------------------------------------------------


def test_idempotency(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path, components={
        "SearchInput": "export default function SearchInput(){return null;}\n",
    })
    src_file = app / "_layout.tsx"
    _write(src_file, (
        'import { SearchInput, Button, Phone, Link, Users } from "bifrost";\n'
        "export default function L(){return <><SearchInput/><Phone/><Link to='/' /></>;}\n"
    ))

    # Round 1
    results = migrate_app(app, PLATFORM, LUCIDE)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    for r in changed:
        r.path.write_text(r.updated, encoding="utf-8")

    # Round 2 — no changes
    results2 = migrate_app(app, PLATFORM, LUCIDE)
    changed2 = [r for r in results2 if r.changed]
    assert changed2 == []


# ---------------------------------------------------------------------------
# Extras: discover_apps, no-op cases
# ---------------------------------------------------------------------------


def test_discover_apps_single_app(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path)
    # Point discover at the app dir itself
    assert discover_apps(app) == [app.resolve()]


def test_discover_apps_workspace(tmp_path: pathlib.Path) -> None:
    app1 = tmp_path / "apps" / "a"
    app2 = tmp_path / "apps" / "b"
    _write(app1 / "_layout.tsx", "export default () => null;\n")
    _write(app2 / "_layout.tsx", "export default () => null;\n")
    found = discover_apps(tmp_path)
    assert sorted([p.name for p in found]) == ["a", "b"]


def test_noop_file_with_only_platform_imports(tmp_path: pathlib.Path) -> None:
    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, 'import { Button, Card } from "bifrost";\n')

    r = migrate_file(src_file, app, set(), PLATFORM, LUCIDE)
    assert not r.changed


# ---------------------------------------------------------------------------
# 8. Platform/lucide collision — platform wins
# ---------------------------------------------------------------------------


def test_platform_wins_on_lucide_collision(tmp_path: pathlib.Path) -> None:
    """Badge, Sheet, Dialog, Table, Command exist in BOTH platform and lucide-react.

    Precedence: platform wins. These names must stay in the "bifrost" import
    and must NOT be rewritten to "lucide-react".
    """
    collision_platform = PLATFORM | {"Badge", "Sheet", "Dialog", "Table", "Command"}
    collision_lucide = LUCIDE | {"Badge", "Sheet", "Table", "Command"}  # Dialog isn't in lucide

    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, (
        'import { Badge, Sheet, Dialog, Table, Command } from "bifrost";\n'
        "export default function L(){return null;}\n"
    ))

    results = migrate_app(app, collision_platform, collision_lucide)
    changed = [r for r in results if r.changed]
    # No changes expected — every name is platform and stays in "bifrost".
    assert changed == []


# ---------------------------------------------------------------------------
# 9. Lucide-only names — move to lucide-react (plain + aliased forms)
# ---------------------------------------------------------------------------


def test_lucide_only_names_move_to_lucide_react(tmp_path: pathlib.Path) -> None:
    """Edit, AlertTriangle, CheckCircle, Loader2 live only in lucide-react."""
    lucide = LUCIDE | {"Edit", "AlertTriangle", "CheckCircle", "Loader2"}

    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, (
        'import { Edit, AlertTriangle, CheckCircle, Loader2 } from "bifrost";\n'
        "export default function L(){return null;}\n"
    ))

    results = migrate_app(app, PLATFORM, lucide)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    assert '"lucide-react"' in updated
    assert "Edit" in updated
    assert "AlertTriangle" in updated
    assert "CheckCircle" in updated
    assert "Loader2" in updated
    # None of them should remain in a "bifrost" import.
    assert 'from "bifrost"' not in updated


def test_lucide_aliased_names_move_to_lucide_react(tmp_path: pathlib.Path) -> None:
    """Aliased forms like `Edit as EditIcon` must also be rewritten and keep the alias."""
    lucide = LUCIDE | {"Edit", "AlertTriangle", "Loader2"}

    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, (
        'import { Edit as EditIcon, AlertTriangle as WarnIcon, Loader2 } from "bifrost";\n'
        "export default function L(){return null;}\n"
    ))

    results = migrate_app(app, PLATFORM, lucide)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    assert "Edit as EditIcon" in updated
    assert "AlertTriangle as WarnIcon" in updated
    assert "Loader2" in updated
    assert '"lucide-react"' in updated
    assert 'from "bifrost"' not in updated


# ---------------------------------------------------------------------------
# 10. Named-export user component → named import (not default)
# ---------------------------------------------------------------------------


def test_named_export_user_component_becomes_named_import(tmp_path: pathlib.Path) -> None:
    """`export function Foo` → `import { Foo } from "./components/Foo"` (NOT default)."""
    app = _make_app(tmp_path, components={
        "Foo": "export function Foo(){return null;}\n",  # named export only
    })
    src_file = app / "_layout.tsx"
    _write(src_file, 'import { Foo, Button } from "bifrost";\n')

    results = migrate_app(app, PLATFORM, LUCIDE)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    assert 'import { Foo } from "./components/Foo";' in updated
    # Explicitly NOT the default form.
    assert 'import Foo from "./components/Foo";' not in updated
    assert 'import { Button } from "bifrost";' in updated


# ---------------------------------------------------------------------------
# 11. Multi-line unrelated import — inserted lines must not corrupt it
# ---------------------------------------------------------------------------


def test_multiline_unrelated_import_preserved(tmp_path: pathlib.Path) -> None:
    """A pre-existing multi-line `import { A, B, C } from "recharts";` must stay
    intact when the migrator inserts a new import (e.g. moved lucide icons)
    after the import block.
    """
    lucide = LUCIDE | {"ChevronDown"}

    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, (
        "import {\n"
        "  LineChart,\n"
        "  Line,\n"
        "  XAxis,\n"
        '} from "recharts";\n'
        'import { Button, ChevronDown } from "bifrost";\n'
        "\n"
        "export default function L(){return null;}\n"
    ))

    results = migrate_app(app, PLATFORM, lucide)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    # The multi-line recharts import must survive unchanged.
    assert (
        "import {\n"
        "  LineChart,\n"
        "  Line,\n"
        "  XAxis,\n"
        '} from "recharts";'
    ) in updated
    # The new lucide import must exist as its own line.
    assert 'import { ChevronDown } from "lucide-react";' in updated
    # And Button must still be imported from bifrost.
    assert 'import { Button } from "bifrost";' in updated


# ---------------------------------------------------------------------------
# 12. Lowercase platform names inferred from usage
# ---------------------------------------------------------------------------


def test_lowercase_platform_names_inferred_from_usage(tmp_path: pathlib.Path) -> None:
    """`cn`, `toast`, `format` are lowercase platform exports. The inference
    pass must pick them up from usage even though they're not PascalCase.
    """
    platform = PLATFORM | {"cn", "toast", "format"}

    app = _make_app(tmp_path)
    src_file = app / "pages" / "index.tsx"
    _write(src_file, (
        'import { Button } from "bifrost";\n'
        "\n"
        "export default function Page(){\n"
        '  const cls = cn("foo", "bar");\n'
        "  toast.success('hi');\n"
        "  const d = format(new Date(), 'yyyy');\n"
        "  return <Button className={cls}>{d}</Button>;\n"
        "}\n"
    ))

    results = migrate_app(app, platform, LUCIDE)
    changed = [r for r in results if r.changed]
    assert len(changed) == 1
    updated = changed[0].updated

    # All three lowercase names must be merged into the bifrost import.
    assert "cn" in updated
    assert "toast" in updated
    assert "format" in updated
    # Single merged bifrost import containing all four names.
    for name in ("Button", "cn", "toast", "format"):
        assert name in updated
    # Only one `from "bifrost"` import statement.
    assert updated.count('from "bifrost"') == 1


# ---------------------------------------------------------------------------
# 13. False-positive guard — names inside import bodies aren't "references"
# ---------------------------------------------------------------------------


def test_names_in_import_body_not_counted_as_references(tmp_path: pathlib.Path) -> None:
    """A name that appears ONLY inside another `import { ... } from "x"` body
    must not trigger an auto-import. Otherwise `import { Button as MyButton }
    from "bifrost"` would flag `Button` as a reference and re-add it.
    """
    # `Card` is a platform name. It appears ONLY inside the import body.
    # The file uses `MyCard` everywhere in JSX, so the unaliased `Card` is
    # NOT actually referenced. Migrator must not auto-import `Card`.
    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, (
        'import { Card as MyCard } from "bifrost";\n'
        "\n"
        "export default function L(){\n"
        "  return <MyCard />;\n"
        "}\n"
    ))

    results = migrate_app(app, PLATFORM, LUCIDE)
    # No changes should be made — `Card as MyCard` is complete; `Card`
    # appearing inside the import body does NOT count as a reference that
    # needs auto-importing.
    changed = [r for r in results if r.changed]
    assert changed == []


# ---------------------------------------------------------------------------
# Rescue pass: Link/NavLink/Navigate/useNavigate/navigate MUST live in
# "bifrost" so they pick up the platform wrappers that prepend the app base
# path. A prior migration moved them to "react-router-dom", which breaks
# `<Link to="/email">` — it navigates to `/email` absolute instead of
# `/apps/<slug>/preview/email`. The reverse pass recovers them.
# ---------------------------------------------------------------------------


def test_rescues_platform_wrapped_names_from_react_router_dom(
    tmp_path: pathlib.Path,
) -> None:
    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, (
        'import { cn } from "bifrost";\n'
        'import { Outlet, Link, useLocation } from "react-router-dom";\n'
        "\n"
        "export default function L(){\n"
        '  return <div><Link to="/email">Email</Link><Outlet /></div>;\n'
        "}\n"
    ))

    results = migrate_app(app, PLATFORM, LUCIDE)
    (res,) = [r for r in results if r.path.name == "_layout.tsx"]
    # Link moved back to "bifrost"; Outlet + useLocation stay in
    # "react-router-dom" because they're not platform-wrapped.
    assert 'import { cn, Link } from "bifrost"' in res.updated \
        or 'import { Link, cn } from "bifrost"' in res.updated, res.updated
    assert "Link" not in (
        # Extract the react-router-dom import line and assert Link is gone
        # from it specifically (rather than from the whole file — Link
        # still appears in JSX).
        next(
            line for line in res.updated.splitlines()
            if line.startswith("import") and 'from "react-router-dom"' in line
        )
    ), res.updated


def test_rescue_is_idempotent_on_already_correct_source(
    tmp_path: pathlib.Path,
) -> None:
    """Running the reverse pass on a file that already has Link from
    "bifrost" must not produce any changes.
    """
    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, (
        'import { cn, Link } from "bifrost";\n'
        'import { Outlet, useLocation } from "react-router-dom";\n'
        "\n"
        "export default function L(){\n"
        '  return <div><Link to="/email">Email</Link><Outlet /></div>;\n'
        "}\n"
    ))
    results = migrate_app(app, PLATFORM, LUCIDE)
    (res,) = [r for r in results if r.path.name == "_layout.tsx"]
    assert res.original == res.updated


def test_rescue_drops_react_router_import_entirely_when_only_wrapped_names(
    tmp_path: pathlib.Path,
) -> None:
    """A react-router-dom import consisting entirely of platform-wrapped
    names (which can happen after a buggy migration) should be removed, not
    left behind as `import { } from "react-router-dom"`.
    """
    app = _make_app(tmp_path)
    src_file = app / "_layout.tsx"
    _write(src_file, (
        'import { useNavigate, Link } from "react-router-dom";\n'
        "\n"
        "export default function L(){\n"
        "  const nav = useNavigate();\n"
        '  return <Link to="/x">Go</Link>;\n'
        "}\n"
    ))
    results = migrate_app(app, PLATFORM, LUCIDE)
    (res,) = [r for r in results if r.path.name == "_layout.tsx"]
    # Both names moved to "bifrost"; no react-router-dom import line left.
    assert 'from "bifrost"' in res.updated
    rr_lines = [
        line for line in res.updated.splitlines()
        if line.startswith("import") and 'from "react-router-dom"' in line
    ]
    assert rr_lines == [], res.updated
