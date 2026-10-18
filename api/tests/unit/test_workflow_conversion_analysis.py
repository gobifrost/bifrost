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
from bifrost import config

def load_secret():
    return config.get("token")
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
        == ["bifrost.config.get"]
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
from bifrost import config
config.get("runtime")

def helper():
    return config.delete("must-not-appear")
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

    assert result["sdk_calls"] == ["bifrost.config.get"]
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
async def test_does_not_treat_an_awaited_sdk_return_as_an_sdk_handle() -> None:
    entry = _source(
        "workflows/run.py",
        """
from bifrost.tables import tables

async def run():
    record = await tables.get("reports", "summary")
    return record.data.get("ok")
""",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.get"]
    assert result["unknowns"] == []


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
    monkeypatch.setattr("shared.workflow_conversion_analysis.MAX_SOURCE_BYTES", 1)
    entry = _source("workflows/run.py", "def run():\n    return 'ok'\n")

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

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
from bifrost import config
config.get("runtime")

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

    assert result["sdk_calls"] == ["bifrost.config.get"]


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
        "from bifrost import config\nconfig.get('runtime')\n",
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

    assert result["sdk_calls"] == ["bifrost.config.get"]


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
        "from bifrost import config\n\n"
        "def load_secret():\n"
        "    return config.get('token')\n",
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
    monkeypatch.setattr("shared.workflow_conversion_analysis.MAX_DEPENDENCIES", 2_000)
    monkeypatch.setattr("shared.workflow_conversion_analysis.MAX_GRAPH_STEPS", 20_000)
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
        return summarize_source(source_file.source)

    # Python versions allocate coroutine frames differently. Deliberately
    # expose the recursion boundary rather than relying on a host's default.
    import sys

    previous_limit = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(200)
        result = await analyze_workflow(
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


def test_accepts_a_representative_large_module_within_source_bounds() -> None:
    summary = summarize_source("value = 1\n" * 8_000)

    assert "parse_error" not in summary


def test_literal_data_methods_and_plain_branch_values_do_not_need_review() -> None:
    summary = summarize_source(
        "def run(enabled):\n"
        "    if enabled:\n"
        "        label = 'enabled'\n"
        "    else:\n"
        "        label = 'disabled'\n"
        "    values = {'label': label}\n"
        "    values.get('label', '')\n"
        "    return label.upper()\n"
    )

    assert summary["functions"]["run"]["events"] == []


@pytest.mark.asyncio
async def test_imported_local_class_constructor_is_not_an_unresolved_function() -> None:
    entry = _source(
        "workflows/run.py",
        "from .models import Payload\n\ndef run():\n    return Payload().as_dict()\n",
    )
    models = _source(
        "workflows/models.py",
        "class Payload:\n    def as_dict(self):\n        return {'ok': True}\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            models
            if source_file is entry and (module, level) == ("models", 1)
            else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_follows_an_inherited_local_class_method() -> None:
    entry = _source(
        "workflows/run.py",
        "from .models import Derived\n\ndef run():\n    return Derived().read()\n",
    )
    models = _source(
        "workflows/models.py",
        "from bifrost.tables import tables\n\n"
        "class Base:\n"
        "    def read(self):\n"
        "        return tables.list()\n\n"
        "class Derived(Base):\n"
        "    pass\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            models
            if source_file is entry and (module, level) == ("models", 1)
            else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.list"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_follows_a_child_module_from_a_namespace_package() -> None:
    entry = _source(
        "workflows/run.py",
        "from .plugins import helper\n\ndef run():\n    return helper.run()\n",
    )
    namespace = SourceFile(
        key="workflows/plugins",
        path="workflows/plugins",
        source="",
        content_hash="namespace-hash",
        kind="namespace",
    )
    helper = _source(
        "workflows/plugins/helper.py",
        "from bifrost import config\n\ndef run():\n    return config.get('setting')\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            (entry.key, "plugins", 1): namespace,
            (namespace.key, "helper", 1): helper,
        }.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_first", [False, True])
async def test_helper_parameter_keeps_json_and_sdk_binding_contexts_separate(
    sdk_first: bool,
) -> None:
    calls = "helper(config)\n    helper(payload)" if sdk_first else "helper(payload)\n    helper(config)"
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n\n"
        "def helper(value):\n"
        "    return value.get('setting')\n\n"
        "def run(payload):\n"
        f"    {calls}\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_static_context_data_getattr_is_not_dynamic_sdk_dispatch() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import context\n\ndef run():\n"
        "    return getattr(context, 'org_id', None)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_static_context_method_getattr_alias_is_followed() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import context\n\ndef run():\n"
        "    select_scope = getattr(context, 'set_scope')\n"
        "    select_scope('target')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.context.set_scope"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_unknown_static_context_getattr_remains_needs_review() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import context\n\ndef run():\n"
        "    return getattr(context, 'future_capability', None)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert result["unknowns"] == [
        "workflows/run.py:4: unrecognized SDK wrapper"
    ]


@pytest.mark.asyncio
async def test_records_only_table_selectors_from_literal_and_constant_arguments() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import tables\n\n"
        "def run(requested_table):\n"
        "    reports = 'reports'\n"
        "    tables.get(reports)\n"
        "    tables.get(requested_table)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["resource_calls"] == [
        {
            "call": "bifrost.tables.get",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 5,
            "selectors": {"table": "reports"},
            "dynamic_selectors": [],
            "selector_reasons": {},
        },
        {
            "call": "bifrost.tables.get",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 6,
            "selectors": {},
            "dynamic_selectors": ["table"],
            "selector_reasons": {"table": "nonliteral selector"},
        },
    ]


@pytest.mark.asyncio
async def test_does_not_retain_an_oversized_resource_selector() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import tables\n\n"
        "def run():\n"
        f"    tables.get({'x' * 2_001!r})\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["resource_calls"][0]["selectors"] == {}
    assert result["resource_calls"][0]["dynamic_selectors"] == ["table"]
    assert result["resource_calls"][0]["selector_reasons"] == {
        "table": "selector exceeds maximum length (2000)"
    }


@pytest.mark.asyncio
async def test_records_shipped_file_location_defaults_without_marking_them_dynamic() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import files\n\n"
        "async def run():\n"
        "    return await files.read('reports/latest.txt')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["resource_calls"] == [
        {
            "call": "bifrost.files.read",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 4,
            "selectors": {"location": "workspace", "path": "reports/latest.txt"},
            "dynamic_selectors": [],
            "selector_reasons": {},
        }
    ]


@pytest.mark.asyncio
async def test_unresolved_installed_external_call_remains_needs_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _source(
        "workflows/run.py",
        "from external_dependency import helper\n\n"
        "def run():\n"
        "    helper()\n",
    )
    monkeypatch.setattr(
        "shared.workflow_conversion_analysis._is_installed_third_party",
        lambda _module: True,
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["unknowns"] == [
        "workflows/run.py:1: unresolved external dependency: external_dependency",
        "workflows/run.py:4: unresolved external dependency: external_dependency"
    ]


@pytest.mark.asyncio
async def test_signed_url_method_is_captured_only_for_supported_file_actions() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import files\n\n"
        "async def run(requested_method):\n"
        "    await files.get_signed_url('reports/latest.txt')\n"
        "    await files.get_signed_url('reports/latest.txt', method='GET')\n"
        "    await files.get_signed_url('reports/latest.txt', method=requested_method)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert [item["selectors"].get("method") for item in result["resource_calls"]] == [
        "PUT",
        "GET",
        None,
    ]
    assert result["resource_calls"][2]["dynamic_selectors"] == ["method"]


@pytest.mark.asyncio
async def test_records_scope_selector_for_known_nonresource_sdk_calls() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n\n"
        "async def run():\n"
        "    return await config.get('setting', scope='foreign-org')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["resource_calls"] == [
        {
            "call": "bifrost.config.get",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 4,
            "selectors": {"scope": "foreign-org"},
            "dynamic_selectors": [],
            "selector_reasons": {},
        }
    ]


def test_json_branch_union_keeps_ordinary_methods_out_of_capability_review() -> None:
    summary = summarize_source(
        "def run(enabled):\n"
        "    if enabled:\n"
        "        value = {'label': 'enabled'}\n"
        "    else:\n"
        "        value = []\n"
        "    return value.get('label')\n"
    )

    assert summary["functions"]["run"]["events"] == []


def test_sdk_alias_in_a_branch_remains_a_conditional_binding() -> None:
    summary = summarize_source(
        "from bifrost import config\n\n"
        "def run(enabled):\n"
        "    if enabled:\n"
        "        value = {}\n"
        "    else:\n"
        "        value = config\n"
        "    return value.get('label')\n"
    )

    assert any(
        event["reason"] == "conditional binding for value"
        for event in summary["functions"]["run"]["events"]
        if event["kind"] == "unknown"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sdk_first", [False, True])
async def test_helper_return_shapes_keep_json_and_sdk_results_separate(
    sdk_first: bool,
) -> None:
    calls = (
        "sdk_value().get('setting')\n    json_value().get('setting')"
        if sdk_first
        else "json_value().get('setting')\n    sdk_value().get('setting')"
    )
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n\n"
        "def json_value():\n"
        "    return {}\n\n"
        "def sdk_value():\n"
        "    return config\n\n"
        "def run():\n"
        f"    {calls}\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_factory_returning_a_local_class_follows_its_method() -> None:
    entry = _source(
        "workflows/run.py",
        "from .helpers import factory\n\n"
        "def run():\n"
        "    return factory().run()\n",
    )
    helpers = _source(
        "workflows/helpers.py",
        "from bifrost import tables\n\n"
        "class Client:\n"
        "    def run(self):\n"
        "        return tables.list()\n\n"
        "def factory():\n"
        "    return Client()\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return helpers if source_file is entry and (module, level) == ("helpers", 1) else None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.list"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_package_reexported_factory_preserves_return_method_dispatch() -> None:
    entry = _source(
        "workflows/run.py",
        "from .exports import factory\n\n"
        "def run():\n"
        "    return factory().run()\n",
    )
    exports = _source("workflows/exports.py", "from .impl import factory\n")
    implementation = _source(
        "workflows/impl.py",
        "from bifrost import tables\n\n"
        "class Client:\n"
        "    def run(self):\n"
        "        return tables.list()\n\n"
        "def factory():\n"
        "    return Client()\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            (entry.key, "exports", 1): exports,
            (exports.key, "impl", 1): implementation,
        }.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.list"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_entry_json_parameter_methods_do_not_create_a_binding_unknown() -> None:
    entry = _source(
        "workflows/run.py",
        "def run(payload):\n"
        "    return payload.get('label')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_entry_context_parameter_preserves_scope_override_and_inherited_scope() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n\n"
        "async def run(context):\n"
        "    context.set_scope('target')\n"
        "    return await config.get('setting')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.ExecutionContext.set_scope",
        "bifrost.config.get",
    ]
    assert result["resource_calls"] == [
        {
            "call": "bifrost.config.get",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 5,
            "selectors": {},
            "dynamic_selectors": [],
            "selector_reasons": {},
        }
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_entry_context_alias_preserves_scope_override() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import ExecutionContext as Ctx\n\n"
        "def run(ctx: Ctx):\n"
        "    ctx.set_scope('target')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.ExecutionContext.set_scope"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_future_context_annotation_preserves_scope_override() -> None:
    entry = _source(
        "workflows/run.py",
        "from __future__ import annotations\n\n"
        "def run(runtime: ExecutionContext | None):\n"
        "    runtime.set_scope('target')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.ExecutionContext.set_scope"]
    assert result["unknowns"] == []


def test_unresolved_entry_annotation_is_not_assumed_json_data() -> None:
    summary = summarize_source(
        "from somewhere import ContextAlias\n\n"
        "def run(value: ContextAlias):\n"
        "    return value.get('setting')\n"
    )

    assert summary["functions"]["run"]["context_parameter"] is None
    assert summary["functions"]["run"]["uncertain_context_parameter"] == "value"


def test_allows_a_synthetic_wide_module_within_expanded_bounds() -> None:
    source = "\n".join(
        f"class Generated{index}:\n    value = {index}\n" for index in range(30_000)
    )

    assert "parse_error" not in summarize_source(source)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory_source",
    [
        "from bifrost import config\n\ndef factory():\n    return config\n",
        "from bifrost import tables\n\nclass Client:\n    def read(self):\n        return tables.list()\n\ndef factory():\n    return Client()\n",
    ],
)
async def test_invoked_dynamic_getattr_on_a_factory_return_remains_needs_review(
    factory_source: str,
) -> None:
    entry = _source(
        "workflows/run.py",
        factory_source
        + "\n"
        "def run(requested_method):\n"
        "    return getattr(factory(), requested_method)()\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert any("dynamic callable target" in item for item in result["unknowns"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body_scope", "else_scope"),
    [("global", "foreign-scope"), ("foreign-scope", "global")],
)
async def test_branch_scope_constants_do_not_select_one_path_as_static(
    body_scope: str, else_scope: str,
) -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n\n"
        "async def run(enabled):\n"
        f"    if enabled:\n        scope = {body_scope!r}\n"
        f"    else:\n        scope = {else_scope!r}\n"
        "    return await config.get('setting', scope=scope)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["resource_calls"] == [{
        "call": "bifrost.config.get",
        "source_key": "workflows/run.py",
        "path": "workflows/run.py",
        "line": 8,
        "selectors": {},
        "dynamic_selectors": ["scope"],
        "selector_reasons": {"scope": "nonliteral selector"},
    }]


def test_prior_and_branch_json_assignments_do_not_emit_a_binding_unknown() -> None:
    summary = summarize_source(
        "def run(enabled):\n"
        "    items = []\n"
        "    if enabled:\n"
        "        items = [1]\n"
        "    items.append(2)\n"
        "    return items\n"
    )

    assert summary["functions"]["run"]["events"] == []


def test_callable_and_json_branch_assignments_remain_distinct() -> None:
    summary = summarize_source(
        "from bifrost import config\n\n"
        "def run(enabled):\n"
        "    value = {}\n"
        "    if enabled:\n"
        "        value = config\n"
        "    else:\n"
        "        value = {}\n"
        "    return value.get('setting')\n"
    )

    assert any(
        event["reason"] == "conditional binding for value"
        for event in summary["functions"]["run"]["events"]
        if event["kind"] == "unknown"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("factory_first", [False, True])
async def test_factory_result_and_json_branch_assignments_remain_distinct(
    factory_first: bool,
) -> None:
    branches = (
        "        target = factory()\n    else:\n        target = {}\n"
        if factory_first
        else "        target = {}\n    else:\n        target = factory()\n"
    )
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n\n"
        "def factory():\n"
        "    return config\n\n"
        "def run(enabled):\n"
        "    if enabled:\n"
        f"{branches}"
        "    return target.get('setting')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert any("conditional binding for target" in item for item in result["unknowns"])


@pytest.mark.asyncio
async def test_sdk_method_alias_preserves_its_exact_static_target() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n\n"
        "async def run():\n"
        "    namespace = config\n"
        "    fetch = config.get\n"
        "    await namespace.get('one')\n"
        "    return await fetch('two')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_typed_context_getter_preserves_scope_override_effect() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import get_execution_context\n\n"
        "def run():\n"
        "    context = get_execution_context()\n"
        "    context.set_scope('target')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.ExecutionContext.set_scope",
        "bifrost.get_execution_context",
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_annotation_does_not_override_a_known_context_return_type() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import get_execution_context\n\n"
        "def run():\n"
        "    context: dict = get_execution_context()\n"
        "    context.set_scope('target')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.ExecutionContext.set_scope",
        "bifrost.get_execution_context",
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_typed_sdk_object_field_preserves_credential_refresh_call() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import integrations\n\n"
        "async def run():\n"
        "    integration = await integrations.get('example')\n"
        "    return await integration.oauth.refresh()\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.OAuthCredentials.refresh",
        "bifrost.integrations.get",
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_annotation_does_not_override_a_known_credential_field_type() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import integrations\n\n"
        "async def run():\n"
        "    integration = await integrations.get('example')\n"
        "    oauth: dict = integration.oauth\n"
        "    return await oauth.refresh()\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.OAuthCredentials.refresh",
        "bifrost.integrations.get",
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_helper_parameter_annotation_does_not_hide_a_sdk_argument() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n\n"
        "def read(value: dict):\n"
        "    return value.get('setting')\n\n"
        "def run():\n"
        "    return read(config)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


def test_local_annotation_does_not_prove_unknown_factory_data() -> None:
    summary = summarize_source(
        "def factory():\n"
        "    return object()\n\n"
        "def run():\n"
        "    value: dict = factory()\n"
        "    return value.get('setting')\n"
    )

    assert summary["functions"]["run"]["events"] == [
        {"kind": "local", "line": 5, "name": "factory", "arguments": []},
        {"kind": "bound_method", "line": 6, "receiver": "value", "method": "get"},
    ]


@pytest.mark.asyncio
async def test_unknown_typed_sdk_object_field_remains_needs_review() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import integrations\n\n"
        "async def run():\n"
        "    integration = await integrations.get('example')\n"
        "    return integration.future_capability()\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.integrations.get"]
    assert result["unknowns"] == [
        "workflows/run.py:5: unrecognized SDK wrapper"
    ]


def test_list_sort_callback_remains_needs_review() -> None:
    summary = summarize_source(
        "from bifrost import config\n\n"
        "def run():\n"
        "    values = []\n"
        "    values.sort(key=lambda value: config.get('setting'))\n"
    )

    assert any(
        event["reason"] == "higher-order callback: sort"
        for event in summary["functions"]["run"]["events"]
        if event["kind"] == "unknown"
    )
