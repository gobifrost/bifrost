"""Unit tests for `extract_external_deps` — the validator helper that
classifies import targets as external (must be declared in the app
manifest) vs. internal/relative (resolves within the app, no manifest
entry needed).

Regression: the validator used to flag relative imports like
`./components/CommandPalette` and `../lib/format` as missing
dependencies, producing a wall of false-positive errors on every
push. Only bare specifiers (and not the `bifrost` runtime) are
external.
"""
from __future__ import annotations

from src.routers.applications import extract_external_deps


def test_relative_imports_are_not_external():
    src = '''
import { CommandPalette } from "./components/CommandPalette";
import { fmt } from "./lib/format";
import { wf } from "../lib/workflows";
'''
    assert extract_external_deps(src) == set()


def test_absolute_path_imports_are_not_external():
    src = '''
import { something } from "/some/root/path";
'''
    assert extract_external_deps(src) == set()


def test_bifrost_runtime_is_not_external():
    src = '''
import { useState, Link } from "bifrost";
'''
    assert extract_external_deps(src) == set()


def test_bare_specifiers_are_external():
    src = '''
import { Search } from "lucide-react";
import { useLocation } from "react-router-dom";
'''
    assert extract_external_deps(src) == {"lucide-react", "react-router-dom"}


def test_scoped_packages_are_external():
    src = '''
import { something } from "@scope/pkg";
import { other } from "@scope/pkg/sub";
'''
    assert extract_external_deps(src) == {"@scope/pkg", "@scope/pkg/sub"}


def test_mixed_imports_only_picks_externals():
    src = '''
import { useState, Link, cn } from "bifrost";
import { useLocation, Outlet } from "react-router-dom";
import { CommandPalette } from "./components/CommandPalette";
import { Search } from "lucide-react";
'''
    assert extract_external_deps(src) == {"react-router-dom", "lucide-react"}


def test_default_imports_are_picked_up():
    src = '''
import React from "react";
import clsx from "clsx";
'''
    assert extract_external_deps(src) == {"react", "clsx"}


def test_namespace_imports_are_picked_up():
    src = '''
import * as utils from "lodash";
'''
    assert extract_external_deps(src) == {"lodash"}


def test_empty_source_returns_empty_set():
    assert extract_external_deps("") == set()


def test_no_imports_returns_empty_set():
    src = '''
export default function Foo() {
  return <div>hi</div>;
}
'''
    assert extract_external_deps(src) == set()


def test_commented_imports_are_ignored():
    """The regex requires `import` at the start of the line — line comments
    that mention the word `import` shouldn't be picked up."""
    src = '''
// import { Foo } from "fake-pkg";
import { Real } from "real-pkg";
'''
    deps = extract_external_deps(src)
    assert "real-pkg" in deps
    assert "fake-pkg" not in deps


def test_default_externals_cover_host_provided_libs():
    """The validator filters host-provided modules out of the
    "missing dependency" check by subtracting the bundler's
    DEFAULT_EXTERNALS from the referenced set. lucide-react and
    react-router-dom MUST stay in that list, otherwise every app that
    imports them will fail validation despite working at runtime
    (regression: covi-design-system tripped this on first push).
    """
    from src.services.app_bundler import DEFAULT_EXTERNALS

    externals = set(DEFAULT_EXTERNALS)
    # The runtime import map serves these; users must not need to declare them.
    for required in ("react", "react-dom", "react-router-dom", "lucide-react"):
        assert required in externals, (
            f"{required!r} missing from DEFAULT_EXTERNALS — apps that import "
            f"it will fail validation while still running at runtime"
        )
