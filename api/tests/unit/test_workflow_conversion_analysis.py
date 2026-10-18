"""Static workflow capability analysis stays conservative and executable-free."""

import hashlib

import pytest

from shared.workflow_conversion_analysis import (
    SourceFile,
    analyze_workflow,
    summarize_source,
)


def _source(key: str, source: str) -> SourceFile:
    return SourceFile(
        key=key,
        path=key,
        source=source,
        content_hash=hashlib.sha256(source.encode()).hexdigest(),
    )


@pytest.mark.asyncio
async def test_absolute_submodule_import_includes_parent_package_initialization() -> (
    None
):
    entry = _source(
        "run.py", "from helpers.api import work\ndef run():\n    return work()\n"
    )
    package = _source(
        "helpers/__init__.py",
        "from bifrost import organizations\norganizations.list()\n",
    )
    helper = _source("helpers/api.py", "def work():\n    return 1\n")

    async def resolve(_source_file, module, level):
        return {"helpers": package, "helpers.api": helper}.get(module)

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve, summarize)
    assert result["sdk_calls"] == ["bifrost.organizations.list"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_similarly_named_user_module_is_not_confused_with_the_platform_sdk() -> (
    None
):
    entry = _source(
        "run.py", "from bifrost_helpers import work\ndef run():\n    return work()\n"
    )
    helper = _source(
        "bifrost_helpers.py",
        "from bifrost import ai\ndef work():\n    return ai.complete('example')\n",
    )

    async def resolve(_source_file, module, level):
        return helper if module == "bifrost_helpers" else None

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve, summarize)
    assert result["sdk_calls"] == ["bifrost.ai.complete"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_analyzes_same_file_helper_without_executing_source() -> None:
    entry = _source(
        "workflows/report.py",
        """
from bifrost.tables import tables

def _load_report():
    return tables.get("reports")

def run():
    return _load_report()
""",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.get"]
    assert result["evidence"] == [
        {
            "path": "workflows/report.py",
            "line": 5,
            "call": "bifrost.tables.get",
            "via": ["workflows/report.py::run", "workflows/report.py::_load_report"],
        }
    ]
    assert result["dependencies"] == {entry.key: entry.content_hash}


@pytest.mark.asyncio
async def test_reuses_caller_cached_summary_across_workflows() -> None:
    helper = _source(
        "workflows/helpers.py",
        """
from bifrost.secrets import secrets

def load_secret():
    return secrets.get("token")
""",
    )
    first = _source(
        "workflows/first.py",
        "from .helpers import load_secret\ndef run():\n    return load_secret()\n",
    )
    second = _source(
        "workflows/second.py",
        "from .helpers import load_secret\ndef run():\n    return load_secret()\n",
    )
    files = {source.key: source for source in (helper, first, second)}
    summary_cache: dict[str, dict] = {}
    summarized: list[str] = []

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        if (module, level) == ("helpers", 1):
            return files["workflows/helpers.py"]
        return None

    async def summarize(source_file: SourceFile) -> dict:
        if source_file.key not in summary_cache:
            summarized.append(source_file.key)
            summary_cache[source_file.key] = summarize_source(source_file.source)
        return summary_cache[source_file.key]

    first_result = await analyze_workflow(first, "run", resolve_import, summarize)
    second_result = await analyze_workflow(second, "run", resolve_import, summarize)

    assert (
        first_result["sdk_calls"]
        == second_result["sdk_calls"]
        == ["bifrost.secrets.get"]
    )
    assert summarized.count(helper.key) == 1


@pytest.mark.asyncio
async def test_follows_relative_alias_and_reexported_helper() -> None:
    entry = _source(
        "workflows/run.py",
        """
from .exports import public as invoke

def run():
    return invoke()
""",
    )
    exports = _source("workflows/exports.py", "from .leaf import perform as public\n")
    leaf = _source(
        "workflows/leaf.py",
        """
from bifrost.tables import tables as records

def perform():
    return records.list()
""",
    )
    files = {source.key: source for source in (entry, exports, leaf)}

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            ("run.py", "exports", 1): exports,
            ("exports.py", "leaf", 1): leaf,
        }.get((source_file.path.rsplit("/", 1)[-1], module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.list"]
    assert result["unknowns"] == []
    assert set(result["dependencies"]) == set(files)


@pytest.mark.asyncio
async def test_handles_import_cycles_without_analyzing_unreachable_functions() -> None:
    entry = _source(
        "workflows/run.py",
        "from .a import entry_helper\ndef run():\n    return entry_helper()\n",
    )
    module_a = _source(
        "workflows/a.py",
        """
from .b import unused_from_b
from bifrost.tables import tables

def entry_helper():
    return tables.get("safe")

def unreachable():
    return tables.delete("must-not-appear")
""",
    )
    module_b = _source(
        "workflows/b.py",
        "from .a import entry_helper\ndef unused_from_b():\n    return entry_helper()\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            ("run.py", "a", 1): module_a,
            ("a.py", "b", 1): module_b,
            ("b.py", "a", 1): module_a,
        }.get((source_file.path.rsplit("/", 1)[-1], module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_includes_import_time_sdk_calls_but_not_unused_imported_functions() -> (
    None
):
    entry = _source(
        "workflows/run.py", "from .setup import helper\ndef run():\n    return 'ok'\n"
    )
    setup = _source(
        "workflows/setup.py",
        """
from bifrost.configs import configs
configs.get("runtime")

def helper():
    return configs.delete("must-not-appear")
""",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            setup
            if (source_file is entry and module == "setup" and level == 1)
            else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.configs.get"]
    assert result["evidence"][0]["via"] == [
        "workflows/run.py::<import>",
        "workflows/setup.py::<import>",
    ]


@pytest.mark.asyncio
async def test_reports_dynamic_dispatch_import_execution_and_raw_http_as_unknowns() -> (
    None
):
    entry = _source(
        "workflows/run.py",
        """
import importlib
from bifrost.tables import tables
import requests

def run(name):
    getattr(tables, name)()
    importlib.import_module(name)
    requests.get("https://example.invalid")
""",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert any("dynamic SDK dispatch" in item for item in result["unknowns"])
    assert any("dynamic import" in item for item in result["unknowns"])
    assert any("raw HTTP" in item for item in result["unknowns"])


@pytest.mark.asyncio
async def test_tracks_awaited_sdk_handles() -> None:
    entry = _source(
        "workflows/run.py",
        """
from bifrost.tables import tables

async def run():
    record = await tables.get("reports")
    return await record.insert({"ok": True})
""",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.get", "bifrost.tables.insert"]


@pytest.mark.asyncio
async def test_includes_decorators_and_defaults_but_excludes_unreachable_function_bodies() -> (
    None
):
    entry = _source(
        "workflows/run.py",
        """
from bifrost.tables import tables

@tables.get("decorator")
def unused(default=tables.list()):
    return tables.delete("must-not-appear")

def run():
    return "ok"
""",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.get", "bifrost.tables.list"]


@pytest.mark.asyncio
async def test_resolver_can_intentionally_shadow_a_stdlib_module() -> None:
    entry = _source(
        "workflows/run.py", "import json\ndef run():\n    return json.helper()\n"
    )
    shadow = _source(
        "workflows/json.py",
        "from bifrost.tables import tables\ndef helper():\n    return tables.list()\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            shadow if source_file is entry and (module, level) == ("json", 0) else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.list"]


@pytest.mark.asyncio
async def test_reports_source_summary_limits_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared import workflow_conversion_analysis as analysis

    monkeypatch.setattr(analysis, "MAX_SOURCE_BYTES", 1)
    entry = _source("workflows/run.py", "def run():\n    return 'ok'\n")

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return analysis.summarize_source(source_file.source)

    result = await analysis.analyze_workflow(entry, "run", resolve_import, summarize)

    assert any("source exceeds maximum size" in item for item in result["unknowns"])


@pytest.mark.asyncio
async def test_follows_dotted_module_imports_and_helper_aliases() -> None:
    entry = _source(
        "workflows/run.py",
        """
import shared.api

def run():
    invoke = shared.api.helper
    return invoke()
""",
    )
    library = _source(
        "shared/api.py",
        "from bifrost.tables import tables\ndef helper():\n    return tables.list()\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            library
            if source_file is entry and (module, level) == ("shared.api", 0)
            else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.list"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_function_local_import_runs_reachable_module_initialization() -> None:
    entry = _source(
        "workflows/run.py",
        """
def run():
    from .setup import helper
    return helper()
""",
    )
    setup = _source(
        "workflows/setup.py",
        """
from bifrost.configs import configs
configs.get("runtime")

def helper():
    return "ok"
""",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            setup if source_file is entry and (module, level) == ("setup", 1) else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.configs.get"]


@pytest.mark.asyncio
async def test_rebinding_an_imported_helper_does_not_follow_its_old_target() -> None:
    entry = _source(
        "workflows/run.py",
        """
from .library import imported_helper
from bifrost.tables import tables

def replacement():
    return tables.list()

def run():
    imported_helper = replacement
    return imported_helper()
""",
    )
    library = _source(
        "workflows/library.py",
        "from bifrost.tables import tables\ndef imported_helper():\n    return tables.delete('must-not-appear')\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            library
            if source_file is entry and (module, level) == ("library", 1)
            else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.list"]


@pytest.mark.asyncio
async def test_nested_function_definition_only_includes_its_immediate_expressions() -> (
    None
):
    entry = _source(
        "workflows/run.py",
        """
from bifrost.tables import tables

def run():
    @tables.get("decorator")
    def deferred(default=tables.list()):
        return tables.delete("must-not-appear")
    return "ok"
""",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.get", "bifrost.tables.list"]


@pytest.mark.asyncio
async def test_follows_constant_relative_dynamic_imports() -> None:
    entry = _source(
        "workflows/run.py",
        """
from importlib import import_module

def run():
    import_module(".helper")
""",
    )
    helper = _source(
        "workflows/helper.py",
        "from bifrost.configs import configs\nconfigs.get('runtime')\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            helper
            if source_file is entry and (module, level) == ("helper", 1)
            else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.configs.get"]


@pytest.mark.asyncio
async def test_function_parameter_shadowing_an_imported_helper_is_not_followed() -> (
    None
):
    entry = _source(
        "workflows/run.py",
        "from .helpers import load_secret as invoke\n\n"
        "def run(invoke):\n"
        "    return invoke()\n",
    )
    helper = _source(
        "workflows/helpers.py",
        "from bifrost.secrets import secrets\n\n"
        "def load_secret():\n"
        "    return secrets.get('token')\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            helper
            if source_file is entry and (module, level) == ("helpers", 1)
            else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert any("unrecognized wrapper" in item for item in result["unknowns"])


@pytest.mark.asyncio
async def test_deep_import_graph_reports_recursion_limit_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared import workflow_conversion_analysis as analysis

    monkeypatch.setattr(analysis, "MAX_DEPENDENCIES", 2_000)
    monkeypatch.setattr(analysis, "MAX_GRAPH_STEPS", 20_000)
    depth = 400
    files = {
        f"workflows/module_{index}.py": _source(
            f"workflows/module_{index}.py",
            (f"import module_{index + 1}\n" if index < depth else "VALUE = 1\n"),
        )
        for index in range(depth + 1)
    }
    entry = _source("workflows/run.py", "import module_0\n\ndef run():\n    return 1\n")

    async def resolve_import(_source_file: SourceFile, module: str, level: int):
        assert level == 0
        return files.get(f"workflows/{module}.py")

    async def summarize(source_file: SourceFile) -> dict:
        return analysis.summarize_source(source_file.source)

    # Python versions allocate coroutine frames differently. Deliberately
    # expose the recursion boundary rather than relying on a host's default.
    import sys

    previous_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(200)
        result = await analysis.analyze_workflow(
            entry, "run", resolve_import, summarize
        )
    finally:
        sys.setrecursionlimit(previous_limit)
    assert any("Python recursion depth" in item for item in result["unknowns"])


@pytest.mark.asyncio
async def test_higher_order_callback_positions_are_needs_review() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost.tables import tables\n\n"
        "def read(row):\n"
        "    return tables.get(row)\n\n"
        "def run(rows):\n"
        "    list(map(read, rows))\n"
        "    return sorted(rows, key=read)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert any("higher-order callback: map" in item for item in result["unknowns"])
    assert any("higher-order callback: sorted" in item for item in result["unknowns"])


@pytest.mark.asyncio
async def test_conditional_import_aliases_do_not_follow_the_last_branch() -> None:
    entry = _source(
        "workflows/run.py",
        "if condition:\n"
        "    from .first import read\n"
        "else:\n"
        "    from .second import read\n\n"
        "def run():\n"
        "    return read()\n",
    )
    first = _source(
        "workflows/first.py",
        "from bifrost.tables import tables\n\ndef read():\n    return tables.list()\n",
    )
    second = _source(
        "workflows/second.py",
        "from bifrost.tables import tables\n\n"
        "def read():\n"
        "    return tables.delete('must-not-appear')\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            (entry.key, "first", 1): first,
            (entry.key, "second", 1): second,
        }.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert any("conditional binding for read" in item for item in result["unknowns"])


@pytest.mark.asyncio
async def test_exception_path_import_aliases_remain_needs_review() -> None:
    entry = _source(
        "run.py",
        "try:\n    from first import read\nexcept ImportError:\n    from second import read\ndef run():\n    return read()\n",
    )
    files = {
        "first": _source(
            "first.py",
            "from bifrost import config\ndef read():\n    return config.get('example')\n",
        ),
        "second": _source("second.py", "def read():\n    return None\n"),
    }

    async def resolve(_source_file, module, level):
        return files.get(module)

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve, summarize)
    assert any(
        "conditional binding in try/except" in item for item in result["unknowns"]
    )


def test_builtin_sort_without_a_callback_has_no_callback_uncertainty() -> None:
    summary = summarize_source("def run(rows):\n    return sorted(rows)\n")
    assert summary["functions"]["run"]["events"] == []
