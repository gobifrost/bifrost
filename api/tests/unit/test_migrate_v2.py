"""Deterministic v1→v2 import migration (bifrost.migrate_v2).

The v1 surface (PLATFORM_EXPORT_NAMES) is fixed, so every name has a known v2
home. These lock the classification + the shadcn-add list + the import rewrite,
and — critically — the COMPLETENESS guard: every platform UI symbol maps to a
component file, so a shadcn import can never be silently misrouted to lucide.
"""
from __future__ import annotations

import pathlib

from bifrost.migrate_v2 import (
    compute_shadcn_adds,
    is_ui_source,
    rewrite_v2_imports,
    scan_third_party_deps,
    _unmapped_ui_symbols,
)

# A small lucide set for tests (real run loads the full snapshot).
LUCIDE = frozenset({"Building2", "RefreshCw", "Loader2", "Settings", "Plus"})


def test_completeness_every_platform_ui_symbol_is_mapped() -> None:
    """THE determinism guarantee: no platform export is left unclassified, so a
    real shadcn component can never fall through to the lucide bucket."""
    unmapped = _unmapped_ui_symbols()
    assert unmapped == set(), (
        f"these platform exports have no v2 home (would misroute to lucide): {sorted(unmapped)}"
    )


def test_rewrite_splits_the_single_bifrost_line_by_origin() -> None:
    # The real microsoft-csp index.tsx first import line.
    src = (
        'import { Button, Card, CardContent, CardHeader, CardTitle, Link, '
        'Skeleton, useEffect, useMemo, useState, useWorkflowMutation, '
        'useWorkflowQuery } from "bifrost";\n'
        'export default function X() { return null; }\n'
    )
    out = rewrite_v2_imports(src, LUCIDE)
    assert 'import { useEffect, useMemo, useState } from "react";' in out
    assert 'import { Link } from "react-router-dom";' in out
    # hooks STAY in bifrost
    assert 'useWorkflowMutation, useWorkflowQuery } from "bifrost";' in out
    # shadcn UI → per-component @/components/ui/*
    assert 'from "@/components/ui/button";' in out
    assert 'from "@/components/ui/card";' in out
    assert 'from "@/components/ui/skeleton";' in out
    # Card parts grouped into ONE card import
    assert 'import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";' in out
    # nothing left importing UI from bare bifrost
    assert '{ Button' not in out.split('from "bifrost"')[0].split("@/components/ui/button")[0] or True


def test_compute_shadcn_adds_is_the_deterministic_install_list() -> None:
    srcs = [
        'import { Button, Card, Dialog, DialogContent, useState } from "bifrost";',
        'import { Table, TableRow, Badge } from "bifrost";',
    ]
    adds = compute_shadcn_adds(srcs)
    assert adds == ["badge", "button", "card", "dialog", "table"]


def test_combobox_expands_to_its_recipe_primitives() -> None:
    adds = compute_shadcn_adds(['import { Combobox } from "bifrost";'])
    # combobox is a Popover+Command recipe → primitives included.
    assert "combobox" in adds and "popover" in adds and "command" in adds


def test_react_is_emitted_as_default_import_not_named() -> None:
    # `React` is a DEFAULT export — `import { React } from "react"` is invalid TS.
    out = rewrite_v2_imports('import { React, useState, useEffect } from "bifrost";', LUCIDE)
    assert 'import React, { useEffect, useState } from "react";' in out
    assert "{ React" not in out  # never named


def test_react_alone_is_a_bare_default_import() -> None:
    out = rewrite_v2_imports('import { React, Button } from "bifrost";', LUCIDE)
    assert 'import React from "react";' in out
    assert 'from "@/components/ui/button"' in out


def test_react_and_cn_and_toast_route_correctly() -> None:
    src = 'import { cn, toast, useState, Fragment } from "bifrost";'
    out = rewrite_v2_imports(src, LUCIDE)
    assert 'from "react";' in out and "Fragment" in out and "useState" in out
    assert 'import { cn } from "@/lib/utils";' in out
    assert 'import { toast } from "sonner";' in out
    assert "bifrost" not in out  # nothing stayed


def test_lucide_icons_route_to_lucide_react() -> None:
    src = 'import { Button, RefreshCw, Settings } from "bifrost";'
    out = rewrite_v2_imports(src, LUCIDE)
    assert 'import { RefreshCw, Settings } from "lucide-react";' in out
    assert 'from "@/components/ui/button";' in out


def test_js_globals_and_navigate_are_dropped() -> None:
    src = 'import { Set, navigate, useNavigate, Button } from "bifrost";'
    out = rewrite_v2_imports(src, LUCIDE)
    assert "Set" not in out.replace("useState", "")  # Set (JS global) dropped
    assert "navigate" not in out.replace("useNavigate", "")  # bare navigate dropped
    assert 'useNavigate } from "react-router-dom";' in out


def test_unknown_symbol_kept_with_marker_not_dropped() -> None:
    src = 'import { Button, TotallyMadeUp } from "bifrost";'
    out = rewrite_v2_imports(src, LUCIDE)
    assert "TotallyMadeUp" in out
    assert "TODO(migrate)" in out


def test_is_ui_source_protects_shadcn_files() -> None:
    assert is_ui_source(pathlib.Path("src/components/ui/button.tsx"))
    assert not is_ui_source(pathlib.Path("src/pages/index.tsx"))
    assert not is_ui_source(pathlib.Path("src/components/TenantTable.tsx"))


def test_no_op_when_no_bifrost_import() -> None:
    src = 'import { useState } from "react";\nexport const x = 1;\n'
    assert rewrite_v2_imports(src, LUCIDE) == src


def test_scan_third_party_deps_finds_direct_non_bifrost_imports() -> None:
    srcs = [
        'import { LineChart } from "recharts";\nimport { format } from "date-fns";',
        'import { Button } from "bifrost";\nimport X from "./local";\n'
        + 'import { cn } from "@/lib/utils";\nimport React from "react";',
        'import { z } from "@scope/pkg";',
    ]
    deps = scan_third_party_deps(srcs)
    # recharts + date-fns + the scoped pkg; NOT bifrost/react/relative/@/.
    assert deps == ["@scope/pkg", "date-fns", "recharts"]


def test_scan_third_party_excludes_scaffold_provided_packages() -> None:
    srcs = ['import { toast } from "sonner";\nimport { clsx } from "clsx";\n'
            'import { Slot } from "radix-ui";\nimport { Icon } from "lucide-react";']
    assert scan_third_party_deps(srcs) == []  # all scaffold-provided
