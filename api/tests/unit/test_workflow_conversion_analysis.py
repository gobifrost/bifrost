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
async def test_reports_only_dynamic_sdk_dispatch_as_an_unknown() -> (
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
    assert len(result["unknowns"]) == 1


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
    assert result["unknowns"] == []


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
async def test_higher_order_callback_positions_follow_local_sdk_helpers() -> None:
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

    assert result["sdk_calls"] == ["bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_conditional_import_aliases_union_reachable_sdk_helpers() -> None:
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

    assert result["sdk_calls"] == ["bifrost.tables.delete", "bifrost.tables.list"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_exception_path_import_aliases_union_reachable_sdk_helpers() -> None:
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
    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


def test_builtin_sort_without_a_callback_has_no_callback_uncertainty() -> None:
    summary = summarize_source("def run(rows):\n    return sorted(rows)\n")
    assert all(
        event["kind"] != "unknown"
        for event in summary["functions"]["run"]["events"]
    )


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
async def test_optional_context_getattr_fields_are_data_reads() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import context\n\ndef run(field):\n"
        "    return getattr(context, 'future_capability', None), getattr(context, field, None)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


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
async def test_unresolved_installed_external_call_is_outside_sdk_inventory(
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

    assert result["unknowns"] == []


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


def test_sdk_alias_in_a_branch_retains_its_reachable_sdk_call() -> None:
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
        event["kind"] == "sdk" and event["call"] == "bifrost.config.get"
        for event in summary["functions"]["run"]["events"]
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

    assert any("dynamic SDK dispatch" in item for item in result["unknowns"])


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

    assert all(
        event["kind"] != "unknown"
        for event in summary["functions"]["run"]["events"]
    )


def test_callable_and_json_branch_assignments_do_not_create_generic_review() -> None:
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

    assert all(
        event["kind"] != "unknown"
        for event in summary["functions"]["run"]["events"]
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
    assert result["unknowns"] == []


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
async def test_scalar_sdk_field_chained_builtin_methods_are_data_operations() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import artifacts\n\n"
        "async def run():\n"
        "    ref = await artifacts.create_text('report.txt', format='text', content='contoso')\n"
        "    return ref.filename.lower().endswith('.txt')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.artifacts.create_text"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_unknown_typed_sdk_field_chained_data_method_is_not_a_permission_call() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import workflows\n\n"
        "async def run():\n"
        "    execution = await workflows.get('contoso')\n"
        "    return execution.result.get('value')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.workflows.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_json_sdk_field_chained_data_methods_are_not_permission_calls() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import tables\n\n"
        "async def run():\n"
        "    document = await tables.get('records', 'contoso')\n"
        "    return document.data.get('value').replace('a', 'b')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.get"]
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

    events = summary["functions"]["run"]["events"]
    assert [(event["kind"], event["line"]) for event in events] == [
        ("local", 5),
        ("bound_method", 6),
    ]
    assert events[0]["name"] == "factory"
    assert events[1]["receiver"] == "value"
    assert events[1]["method"] == "get"


@pytest.mark.asyncio
async def test_unknown_typed_sdk_object_method_is_a_data_correctness_warning() -> None:
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
    assert result["unknowns"] == []
    assert result["warnings"] == [
        "workflows/run.py:5: unsupported SDK data-object method"
    ]


@pytest.mark.asyncio
async def test_inventory_ignores_ordinary_python_but_keeps_dynamic_sdk_dispatch() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import tables\n"
        "import requests\n\n"
        "def run(name, values):\n"
        "    requests.get('https://example.invalid')\n"
        "    values.sort(key=str)\n"
        "    getattr(values, name)()\n"
        "    getattr(tables, name)()\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert result["unknowns"] == [
        "workflows/run.py:8: dynamic SDK dispatch"
    ]


@pytest.mark.asyncio
async def test_imported_helper_tracks_a_passed_sdk_namespace() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n"
        "from .helpers import read\n\n"
        "def run():\n"
        "    return read(config)\n",
    )
    helper = _source(
        "workflows/helpers.py",
        "def read(client):\n"
        "    return client.get('setting')\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return helper if source_file is entry and (module, level) == ("helpers", 1) else None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_callback_and_factory_aliases_preserve_reachable_sdk_calls() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config, tables\n\n"
        "def factory():\n"
        "    namespace = config\n"
        "    return namespace\n\n"
        "def read(row):\n"
        "    return tables.get(row)\n\n"
        "def run(rows):\n"
        "    list(map(read, rows))\n"
        "    factory().get('setting')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_generic_sdk_api_path_metadata_is_bounded_and_normalized() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import api\n\n"
        "def run(identifier):\n"
        "    prefix = '/api/tables'\n"
        "    api.get(f'{prefix}/{identifier}')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.api.get"]
    assert result["resource_calls"] == [
        {
            "call": "bifrost.api.get",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 5,
            "selectors": {"path": "/api/tables/{}"},
            "dynamic_selectors": [],
            "selector_reasons": {},
        }
    ]


@pytest.mark.asyncio
async def test_runtime_external_roots_and_optional_imports_are_not_permissions() -> None:
    entry = _source(
        "workflows/run.py",
        "try:\n"
        "    import optional_contoso_client\n"
        "except ImportError:\n"
        "    optional_contoso_client = None\n"
        "import runtime_contoso_client\n\n"
        "def run():\n"
        "    return runtime_contoso_client.connect()\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(
        entry,
        "run",
        resolve_import,
        summarize,
        external_module_roots=frozenset({"runtime_contoso_client"}),
    )

    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_branch_prior_sdk_namespace_and_nested_keyword_helper_are_followed() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config, tables\n\n"
        "def run(flag):\n"
        "    client = tables\n"
        "    if flag:\n"
        "        client = config\n"
        "    client.get('id')\n\n"
        "    def read(client):\n"
        "        return client.get('setting')\n"
        "    return read(client=config)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_direct_sdk_result_data_methods_warn_without_inventing_transport() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import tables\n\n"
        "async def run():\n"
        "    return (await tables.get('reports')).get('id')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.get"]
    assert result["unknowns"] == []
    assert result["warnings"] == [
        "workflows/run.py:4: unsupported SDK data-object method"
    ]


@pytest.mark.asyncio
async def test_generic_file_sdk_path_retains_only_location_and_signing_method() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import api\n\n"
        "async def run(location):\n"
        "    await api.post('/api/files/write', json={'location': location, 'content': 'secret'})\n"
        "    await api.post('/api/files/signed-url', json={'location': 'uploads', 'method': 'GET', 'path': 'private'})\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["resource_calls"] == [
        {
            "call": "bifrost.api.post",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 4,
            "selectors": {"path": "/api/files/write"},
            "dynamic_selectors": ["location"],
            "selector_reasons": {"location": "nonliteral selector"},
        },
        {
            "call": "bifrost.api.post",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 5,
            "selectors": {
                "path": "/api/files/signed-url",
                "location": "uploads",
                "method": "GET",
            },
            "dynamic_selectors": [],
            "selector_reasons": {},
        },
    ]


@pytest.mark.asyncio
async def test_entrypoint_found_requires_a_local_function_or_resolved_reexport() -> None:
    entry = _source("workflows/run.py", "from .impl import execute as run\n")
    implementation = _source("workflows/impl.py", "def execute():\n    return 'ok'\n")

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return (
            implementation
            if source_file is entry and (module, level) == ("impl", 1)
            else None
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    resolved = await analyze_workflow(entry, "run", resolve_import, summarize)
    missing = await analyze_workflow(entry, "missing", resolve_import, summarize)

    assert resolved["entrypoint_found"] is True
    assert missing["entrypoint_found"] is False


@pytest.mark.asyncio
async def test_same_file_and_reexported_helpers_preserve_keyword_sdk_handles() -> None:
    same_file = _source(
        "workflows/same_file.py",
        "from bifrost import config\n\n"
        "def read(client):\n"
        "    return client.get('setting')\n\n"
        "def run():\n"
        "    return read(client=config)\n",
    )
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n"
        "from .exports import read\n\n"
        "def run():\n"
        "    return read(client=config)\n",
    )
    exports = _source("workflows/exports.py", "from .impl import read\n")
    implementation = _source(
        "workflows/impl.py",
        "def read(client):\n    return client.get('setting')\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            (entry.key, "exports", 1): exports,
            (exports.key, "impl", 1): implementation,
        }.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    same_file_result = await analyze_workflow(
        same_file, "run", resolve_import, summarize
    )
    reexport_result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert same_file_result["sdk_calls"] == ["bifrost.config.get"]
    assert reexport_result["sdk_calls"] == ["bifrost.config.get"]


@pytest.mark.asyncio
async def test_conditional_reexport_unions_keyword_helper_targets() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config\n"
        "from .exports import read\n\n"
        "def run():\n"
        "    return read(client=config)\n",
    )
    exports = _source(
        "workflows/exports.py",
        "if enabled:\n    from .first import read\nelse:\n    from .second import read\n",
    )
    first = _source("workflows/first.py", "def read(client):\n    return client.get('one')\n")
    second = _source("workflows/second.py", "def read(client):\n    return client.get('two')\n")

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            (entry.key, "exports", 1): exports,
            (exports.key, "first", 1): first,
            (exports.key, "second", 1): second,
        }.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_helper_bound_generic_api_method_preserves_route_evidence() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import api\n\n"
        "def users(client):\n"
        "    return client.get('/api/users')\n\n"
        "def signed_url(client):\n"
        "    return client.post('/api/files/signed-url', json={'location': 'uploads', 'method': 'GET', 'token': 'private'})\n\n"
        "def run():\n"
        "    users(api)\n"
        "    signed_url(api)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.api.get", "bifrost.api.post"]
    assert result["resource_calls"] == [
        {
            "call": "bifrost.api.get",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 4,
            "selectors": {"path": "/api/users"},
            "dynamic_selectors": [],
            "selector_reasons": {},
        },
        {
            "call": "bifrost.api.post",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 7,
            "selectors": {
                "path": "/api/files/signed-url",
                "location": "uploads",
                "method": "GET",
            },
            "dynamic_selectors": [],
            "selector_reasons": {},
        },
    ]


@pytest.mark.asyncio
async def test_same_file_generic_api_helper_binds_only_bounded_route_templates() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import api\n\n"
        "async def get_json(path):\n"
        "    return await api.get(path)\n\n"
        "async def run(identifier, requested_path):\n"
        "    await get_json('/api/users?token=private')\n"
        "    await get_json(f'/api/agents/{identifier}/runs')\n"
        "    return await get_json(requested_path)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.api.get"]
    assert {tuple(item["selectors"].items()) for item in result["resource_calls"]} == {
        (("path", "/api/agents/{}/runs"),),
        (("path", "/api/users"),),
        (),
    }
    assert any(
        item["dynamic_selectors"] == ["path"]
        for item in result["resource_calls"]
    )
    assert all(
        "?" not in item["selectors"].get("path", "")
        for item in result["resource_calls"]
    )
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_generic_routes_strip_sdk_query_suffixes_and_bound_helper_paths() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import api\n"
        "from bifrost.files import _solution_query\n"
        "from urllib.parse import urlencode\n\n"
        "async def get_json(path):\n"
        "    return await api.get(path)\n\n"
        "async def run(params):\n"
        "    await api.post(f'/api/files/complete-upload{_solution_query()}', json={'location': 'workspace', 'token': 'private'})\n"
        "    await api.post('/api/files/write' + _solution_query(), json={'location': 'workspace'})\n"
        "    path = '/api/agent-runs?' + urlencode(params)\n"
        "    return await get_json(path)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.api.get", "bifrost.api.post"]
    assert {
        tuple(sorted(item["selectors"].items()))
        for item in result["resource_calls"]
    } == {
        (("location", "workspace"), ("path", "/api/files/complete-upload")),
        (("location", "workspace"), ("path", "/api/files/write")),
        (("path", "/api/agent-runs"),),
    }
    assert all(
        "?" not in item["selectors"].get("path", "")
        for item in result["resource_calls"]
    )
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_generic_route_with_an_arbitrary_operation_suffix_stays_dynamic() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import api\n\n"
        "def suffix():\n"
        "    return '/files/write'\n\n"
        "async def run():\n"
        "    return await api.post('/api' + suffix(), json={'location': 'workspace'})\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["resource_calls"] == [
        {
            "call": "bifrost.api.post",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 7,
            "selectors": {},
            "dynamic_selectors": ["path"],
            "selector_reasons": {"path": "nonliteral selector"},
        }
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_generic_api_helper_binds_keyword_route_template() -> None:
    entry = _source(
        "workflows/run.py",
        "from .helpers import get_json\n\n"
        "async def run(identifier):\n"
        "    return await get_json(path=f'/api/agents/{identifier}/runs')\n",
    )
    helper = _source(
        "workflows/helpers.py",
        "from bifrost import api\n\n"
        "async def get_json(path):\n"
        "    return await api.get(path)\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {(entry.key, "helpers", 1): helper}.get(
            (source_file.key, module, level)
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.api.get"]
    assert result["resource_calls"] == [
        {
            "call": "bifrost.api.get",
            "source_key": "workflows/helpers.py",
            "path": "workflows/helpers.py",
            "line": 4,
            "selectors": {"path": "/api/agents/{}/runs"},
            "dynamic_selectors": [],
            "selector_reasons": {},
        }
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_module_function_return_data_does_not_become_module_member() -> None:
    entry = _source(
        "workflows/run.py",
        "from . import client\n\n"
        "def run():\n"
        "    row = client.fetch()\n"
        "    return row.get('value')\n",
    )
    package = _source("workflows/__init__.py", "")
    client = _source(
        "workflows/client.py",
        "from bifrost import config\n\n"
        "def fetch():\n"
        "    config.get('contoso')\n"
        "    return {'value': 'ok'}\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            (entry.key, "", 1): package,
            (package.key, "client", 1): client,
        }.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_bifrost_client_transports_retain_http_and_file_selectors() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost.client import get_client\n\n"
        "async def run():\n"
        "    client = get_client()\n"
        "    await client.engine_request('POST', '/api/files/signed-url?token=private', json={'location': 'workspace', 'method': 'GET'})\n"
        "    return await client.get('/api/users')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.client.engine_request",
        "bifrost.client.get",
        "bifrost.client.get_client",
    ]
    assert result["resource_calls"] == [
        {
            "call": "bifrost.client.engine_request",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 5,
            "selectors": {
                "path": "/api/files/signed-url",
                "http_method": "POST",
                "location": "workspace",
                "method": "GET",
            },
            "dynamic_selectors": [],
            "selector_reasons": {},
        },
        {
            "call": "bifrost.client.get",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 6,
            "selectors": {"path": "/api/users"},
            "dynamic_selectors": [],
            "selector_reasons": {},
        },
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_helper_bound_bifrost_client_retains_file_transport_selectors() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost.client import get_client\n\n"
        "async def write_file(client):\n"
        "    return await client.post('/api/files/write', json={'location': 'workspace'})\n\n"
        "async def run():\n"
        "    return await write_file(get_client())\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.client.BifrostClient.post",
        "bifrost.client.get_client",
    ]
    assert result["resource_calls"] == [
        {
            "call": "bifrost.client.BifrostClient.post",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 4,
            "selectors": {
                "path": "/api/files/write",
                "location": "workspace",
            },
            "dynamic_selectors": [],
            "selector_reasons": {},
        }
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_helper_bound_bifrost_client_engine_request_materializes_route_context() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost.client import get_client\n\n"
        "async def request(client, path):\n"
        "    return await client.engine_request('POST', path, json={'location': 'workspace', 'token': 'private'})\n\n"
        "async def run():\n"
        "    return await request(get_client(), '/api/files/write?token=private')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.client.BifrostClient.engine_request",
        "bifrost.client.get_client",
    ]
    assert result["resource_calls"] == [
        {
            "call": "bifrost.client.BifrostClient.engine_request",
            "source_key": "workflows/run.py",
            "path": "workflows/run.py",
            "line": 4,
            "selectors": {
                "path": "/api/files/write",
                "http_method": "POST",
                "location": "workspace",
            },
            "dynamic_selectors": [],
            "selector_reasons": {},
        }
    ]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_helper_bound_files_method_preserves_location_evidence() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import files\n\n"
        "def read(client):\n"
        "    return client.read('reports/latest.txt', location='workspace')\n\n"
        "def run():\n"
        "    return read(files)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.files.read"]
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
async def test_conditional_sdk_method_aliases_preserve_all_targets() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config, tables\n\n"
        "def run(flag):\n"
        "    if flag:\n"
        "        fetch = config.get\n"
        "    else:\n"
        "        fetch = tables.get\n"
        "    fetch('value')\n"
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_conditional_callback_aliases_follow_all_local_targets() -> None:
    entry = _source(
        "workflows/run.py",
        "from bifrost import config, tables\n\n"
        "def first():\n"
        "    return config.get('first')\n\n"
        "def second():\n"
        "    return tables.get('second')\n\n"
        "def run(flag, rows):\n"
        "    if flag:\n"
        "        callback = first\n"
        "    else:\n"
        "        callback = second\n"
        "    list(map(callback, rows))\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_conditional_sdk_imported_method_aliases_preserve_all_targets() -> None:
    entry = _source(
        "workflows/run.py",
        "def run(flag):\n"
        "    if flag:\n"
        "        from bifrost.config import get as fetch\n"
        "    else:\n"
        "        from bifrost.tables import get as fetch\n"
        "    return fetch('value')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("import_style", ["direct", "namespace"])
@pytest.mark.parametrize("map_location", ["local", "imported"])
async def test_literal_module_getattr_facade_follows_client_factory_and_child(import_style, map_location) -> None:
    entry = _source(
        "workflows/run.py",
        ("from .facade import list_records\n\nasync def run():\n    return await list_records()\n"
         if import_style == "direct" else
         "from modules import facade\n\nasync def run():\n    return await facade.list_records()\n"),
    )
    facade = _source(
        "workflows/facade/__init__.py",
        "import importlib\nfrom . import credentials\n\n"
        "METHOD_TO_MODULE = {'list_records': 'records'}\n\n"
        "def load_child(name):\n    return importlib.import_module(f'{__name__}.{name}')\n\n"
        "def __getattr__(name):\n"
        "    child_name = METHOD_TO_MODULE.get(name)\n"
        "    if child_name is not None:\n"
        "        module = load_child(child_name)\n"
        "        function = getattr(module, name, None)\n"
        "        async def bound(*args, **kwargs):\n"
        "            client = await credentials.ensure_client()\n"
        "            return await function(client, *args, **kwargs)\n"
        "        return bound\n"
        "    raise AttributeError(name)\n",
    )
    if map_location == "imported":
        facade = _source(facade.path, facade.source.replace(
            "METHOD_TO_MODULE = {'list_records': 'records'}",
            "from .metadata import METHOD_TO_MODULE",
        ))
    metadata = _source(
        "workflows/facade/metadata.py",
        "METHOD_TO_MODULE = {'list_records': 'records'}\n",
    )
    credentials = _source(
        "workflows/facade/credentials.py",
        "from bifrost import config\n\n"
        "async def ensure_client():\n"
        "    return await config.get('contoso_client')\n",
    )
    records = _source(
        "workflows/facade/records.py",
        "from bifrost import tables\n\n"
        "async def list_records(client):\n"
        "    return await tables.get('records')\n",
    )

    namespace = SourceFile("modules", "modules", "", "namespace", kind="namespace")
    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            (entry.key, "facade", 1): facade,
            (entry.key, "modules", 0): namespace,
            (namespace.key, "facade", 1): facade,
            (facade.key, "credentials", 1): credentials,
            (facade.key, "", 1): facade,
            (facade.key, "metadata", 1): metadata,
            (facade.key, "records", 1): records,
        }.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_module_getattr_facade_does_not_route_names_absent_from_literal_map() -> None:
    entry = _source(
        "workflows/run.py",
        "from .facade import missing_operation\n\n"
        "async def run():\n"
        "    return await missing_operation()\n",
    )
    facade = _source(
        "workflows/facade/__init__.py",
        "import importlib\nMETHOD_TO_MODULE = {'list_records': 'records'}\n\n"
        "def load_child(name):\n    return importlib.import_module(f'{__name__}.{name}')\n\n"
        "def __getattr__(name):\n"
        "    child_name = METHOD_TO_MODULE.get(name)\n"
        "    if child_name is not None:\n"
        "        module = load_child(child_name)\n"
        "        function = getattr(module, name, None)\n"
        "        async def bound(*args, **kwargs):\n"
        "            client = await make_client()\n"
        "            return await function(client, *args, **kwargs)\n"
        "        return bound\n"
        "    raise AttributeError(name)\n",
    )
    records = _source(
        "workflows/facade/records.py",
        "async def missing_operation(client):\n    return client\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            (entry.key, "facade", 1): facade,
            (facade.key, "records", 1): records,
        }.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert any("unresolved user function: missing_operation" in item for item in result["unknowns"])


def test_local_class_name_takes_precedence_over_builtin_exception_name() -> None:
    summary = summarize_source(
        "class ValueError:\n    pass\n\n"
        "class Derived(ValueError):\n    pass\n"
    )

    assert summary["classes"]["Derived"]["bases"] == [
        {"kind": "local", "name": "ValueError"}
    ]


def test_list_sort_callback_follows_sdk_lambda() -> None:
    summary = summarize_source(
        "from bifrost import config\n\n"
        "def run():\n"
        "    values = []\n"
        "    values.sort(key=lambda value: config.get('setting'))\n"
    )

    assert any(
        event["kind"] == "sdk" and event["call"] == "bifrost.config.get"
        for event in summary["functions"]["run"]["events"]
    )


@pytest.mark.asyncio
async def test_imported_top_level_client_instance_follows_class_helpers() -> None:
    entry = _source(
        "workflows/run.py",
        "from .service import client\n\n"
        "async def run():\n"
        "    return await client.execute()\n",
    )
    service = _source(
        "workflows/service.py",
        "from bifrost import config, tables\n\n"
        "class ContosoClient:\n"
        "    async def prepare(self):\n"
        "        return await config.get('contoso')\n\n"
        "    async def execute(self):\n"
        "        await self.prepare()\n"
        "        return await tables.get('records')\n\n"
        "client = ContosoClient()\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {(entry.key, "service", 1): service}.get(
            (source_file.key, module, level)
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("factory_style", ["cached", "wrapper"])
async def test_lazy_local_instance_facade_routes_only_concrete_client_methods(factory_style) -> None:
    entry = _source(
        "workflows/run.py",
        "from .lazy import list_records\n\n"
        "async def run():\n"
        "    return await list_records()\n",
    )
    lazy = _source(
        "workflows/lazy.py",
        "from bifrost import config, tables\n\n"
        "class ContosoClient:\n"
        "    async def list_records(self):\n"
        "        return await tables.get('records')\n\n"
        "def create_client():\n"
        "    return ContosoClient()\n\n"
        "class LazyClient:\n"
        "    def __init__(self):\n"
        "        self._client = None\n\n"
        "    async def make_client(self):\n"
        "        await config.get('contoso')\n"
        "        if self._client is None:\n"
        "            self._client = create_client()\n"
        "        return self._client\n\n"
        "    def __getattr__(self, name):\n"
        "        async def wrapper(*args, **kwargs):\n"
        "            client = await self.make_client()\n"
        "            method = getattr(client, name)\n"
        "            return await method(*args, **kwargs)\n"
        "        return wrapper\n\n"
        "_lazy = LazyClient()\n\n"
        "def __getattr__(name):\n"
        "    return getattr(_lazy, name)\n",
    )

    if factory_style == "wrapper":
        lazy = _source(lazy.path, lazy.source.replace(
            "return self._client", "return {}",
        ).replace(
            "client = await self.make_client()", "settings = await self.make_client()\n            client = create_client()",
        ))

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {(entry.key, "lazy", 1): lazy}.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_lazy_local_instance_facade_keeps_unknown_method_unresolved() -> None:
    entry = _source(
        "workflows/run.py",
        "from .lazy import remove_records\n\n"
        "async def run():\n"
        "    return await remove_records()\n",
    )
    lazy = _source(
        "workflows/lazy.py",
        "class ContosoClient:\n"
        "    async def list_records(self):\n"
        "        return []\n\n"
        "class LazyClient:\n"
        "    async def make_client(self):\n"
        "        return ContosoClient()\n\n"
        "    def __getattr__(self, name):\n"
        "        async def wrapper(*args, **kwargs):\n"
        "            client = await self.make_client()\n"
        "            return await getattr(client, name)(*args, **kwargs)\n"
        "        return wrapper\n\n"
        "_lazy = LazyClient()\n\n"
        "def __getattr__(name):\n"
        "    return getattr(_lazy, name)\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {(entry.key, "lazy", 1): lazy}.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert any("ContosoClient.remove_records" in item for item in result["unknowns"])


@pytest.mark.asyncio
async def test_top_level_local_function_alias_is_an_importable_export() -> None:
    entry = _source(
        "workflows/run.py",
        "from .helper import public\n\n"
        "async def run():\n"
        "    return await public()\n",
    )
    helper = _source(
        "workflows/helper.py",
        "from bifrost import config\n\n"
        "async def implementation():\n"
        "    return await config.get('contoso')\n\n"
        "public = implementation\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {(entry.key, "helper", 1): helper}.get(
            (source_file.key, module, level)
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('source,expected', [
    (
        "from bifrost import data_provider, tool, config\n"
        "@data_provider()\ndef read():\n    return config.get('setting')\n"
        "@tool()\ndef run():\n    return read()\n",
        ['bifrost.config.get'],
    ),
    (
        "from bifrost import integrations, context\n"
        "async def run():\n"
        "    integration = await integrations.get('contoso')\n"
        "    integration.entity_id.lower()\n"
        "    integration.oauth.client_id.split('.')\n"
        "    return context.email.lower()\n",
        ['bifrost.integrations.get'],
    ),
    (
        "from bifrost import config\n"
        "class ValidationError(ValueError):\n    pass\n"
        "def parse(value):\n    return value.json() if value else {}\n"
        "def run(value):\n"
        "    ValidationError('invalid')\n"
        "    parse(value).get('message')\n"
        "    return config.get('setting')\n",
        ['bifrost.config.get'],
    ),
])
async def test_metadata_decorators_and_data_operations_do_not_require_permission_review(source, expected):
    entry = _source('workflows/contoso.py', source)
    async def resolve_import(_source_file, _module, _level):
        return None
    async def summarize(source_file):
        return summarize_source(source_file.source)
    result = await analyze_workflow(entry, 'run', resolve_import, summarize)
    assert result['sdk_calls'] == expected
    assert result['unknowns'] == []


@pytest.mark.asyncio
async def test_awaited_sdk_return_arguments_are_data_models_not_sdk_namespaces():
    entry = _source('workflows/contoso.py',
        "from bifrost import tables, agents\n"
        "def normalize(document):\n    return document.get('data', document)\n"
        "def serialize(run):\n    return run.model_dump(mode='json')\n"
        "async def run():\n"
        "    normalize(await tables.get('records', 'contoso'))\n"
        "    serialize(await agents.get_run('contoso'))\n")
    async def resolve_import(_source_file, _module, _level):
        return None
    async def summarize(source_file):
        return summarize_source(source_file.source)
    result = await analyze_workflow(entry, 'run', resolve_import, summarize)
    assert result['sdk_calls'] == ['bifrost.agents.get_run', 'bifrost.tables.get']
    assert result['unknowns'] == []


@pytest.mark.asyncio
async def test_imported_literal_data_exports_keep_module_initialization_but_not_class_dispatch():
    entry = _source(
        "workflows/run.py",
        "from .constants import LABELS, QUERY\n\n"
        "def run():\n"
        "    return LABELS.get('open'), QUERY.format(ticket='contoso')\n",
    )
    constants = _source(
        "workflows/constants.py",
        "from bifrost import config\n\n"
        "LABELS = {'open': {'name': 'Open'}}\n"
        "QUERY = 'select {ticket}'\n"
        "config.get('contoso')\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {(entry.key, "constants", 1): constants}.get(
            (source_file.key, module, level)
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_local_data_fields_and_builtin_data_subclasses_do_not_dispatch_methods() -> None:
    entry = _source(
        "workflows/run.py",
        "class ContosoIndex:\n"
        "    by_ticket: dict\n\n"
        "    def lookup(self):\n"
        "        return self.by_ticket.get('contoso')\n\n"
        "class ContosoLabel(str):\n"
        "    def parts(self):\n"
        "        return self.rsplit('-', 1)\n\n"
        "def run():\n"
        "    index = ContosoIndex()\n"
        "    label = ContosoLabel('a-b')\n"
        "    return index.lookup(), label.rsplit('-', 1)\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_lazy_facade_direct_local_client_constructor_routes_requested_method() -> None:
    entry = _source(
        "workflows/run.py",
        "from .lazy import list_records\n\n"
        "async def run():\n"
        "    return await list_records()\n",
    )
    lazy = _source(
        "workflows/lazy.py",
        "from bifrost import config, tables\n\n"
        "class ContosoClient:\n"
        "    def __init__(self, token):\n"
        "        self.token = token\n\n"
        "    async def list_records(self):\n"
        "        return await tables.get('records')\n\n"
        "class LazyClient:\n"
        "    async def _get_integration(self):\n"
        "        return await config.get('contoso')\n\n"
        "    def __getattr__(self, name):\n"
        "        async def wrapper(*args, **kwargs):\n"
        "            integration = await self._get_integration()\n"
        "            client = ContosoClient(integration)\n"
        "            method = getattr(client, name)\n"
        "            return await method(*args, **kwargs)\n"
        "        return wrapper\n\n"
        "_lazy = LazyClient()\n\n"
        "def __getattr__(name):\n"
        "    return getattr(_lazy, name)\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {(entry.key, "lazy", 1): lazy}.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field_annotation", ["dict", "dict | None", "Optional[dict]"])
async def test_imported_local_instance_typed_data_field_is_not_a_class_method(field_annotation) -> None:
    entry = _source(
        "workflows/run.py",
        "from .evidence import evidence\n\n"
        "def run():\n"
        "    return evidence.by_ticket.get('contoso')\n",
    )
    evidence_module = _source(
        "workflows/evidence.py",
        "from typing import Optional\n"
        "class Evidence:\n"
        f"    by_ticket: {field_annotation}\n\n"
        "evidence = Evidence()\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {(entry.key, "evidence", 1): evidence_module}.get(
            (source_file.key, module, level)
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_reassigned_literal_export_remains_an_sdk_namespace_alias() -> None:
    entry = _source(
        "workflows/run.py",
        "from .aliases import LABELS\n\n"
        "def run():\n"
        "    return LABELS.get('contoso')\n",
    )
    aliases = _source(
        "workflows/aliases.py",
        "from bifrost import config\n\n"
        "LABELS = {}\n"
        "LABELS = config\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {(entry.key, "aliases", 1): aliases}.get(
            (source_file.key, module, level)
        )

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_dataclass_sdk_callable_fields_only_inventory_reachable_callbacks() -> None:
    entry = _source(
        "workflows/run.py",
        "from dataclasses import dataclass\n"
        "from typing import Callable\n"
        "from bifrost import tables\n\n"
        "@dataclass\n"
        "class Store:\n"
        "    table_name: str\n"
        "    get: Callable\n"
        "    insert: Callable\n"
        "    update: Callable\n"
        "    delete: Callable\n\n"
        "    async def acquire(self):\n"
        "        await self.insert({'id': 'contoso'})\n"
        "        return await self.get(self.table_name)\n\n"
        "    def mark_delivered(self):\n"
        "        return self.update({'delivered': True})\n\n"
        "def make_store():\n"
        "    return Store(table_name='records', get=tables.get, insert=tables.insert, update=tables.update, delete=tables.delete)\n\n"
        "async def run():\n"
        "    direct = Store(table_name='records', get=tables.get, insert=tables.insert, update=tables.update, delete=tables.delete)\n"
        "    await direct.acquire()\n"
        "    direct.mark_delivered()\n"
        "    return await make_store().acquire()\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.tables.get",
        "bifrost.tables.insert",
        "bifrost.tables.update",
    ]
    assert "bifrost.tables.delete" not in result["sdk_calls"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_dataclass_constructor_and_helper_return_follow_reachable_sdk_fields() -> None:
    direct_entry = _source(
        "workflows/run.py",
        "from bifrost import tables\n"
        "from .store import Store\n\n"
        "async def run():\n"
        "    direct = Store(get=tables.get, insert=tables.insert, update=tables.update, delete=tables.delete)\n"
        "    await direct.acquire()\n"
        "    return direct.mark_delivered()\n",
    )
    helper_entry = _source(
        "workflows/helper_run.py",
        "from .helpers import make_store\n\n"
        "async def run():\n"
        "    return await make_store().acquire()\n",
    )
    store = _source(
        "workflows/store.py",
        "from dataclasses import dataclass\n"
        "from typing import Callable\n\n"
        "@dataclass\n"
        "class Store:\n"
        "    get: Callable\n"
        "    insert: Callable\n"
        "    update: Callable\n"
        "    delete: Callable\n\n"
        "    async def acquire(self):\n"
        "        await self.insert({'id': 'contoso'})\n"
        "        return await self.get('records')\n\n"
        "    def mark_delivered(self):\n"
        "        return self.update({'delivered': True})\n",
    )
    helper = _source(
        "workflows/helpers.py",
        "from bifrost import tables\n"
        "from .store import Store\n\n"
        "def make_store():\n"
        "    return Store(get=tables.get, insert=tables.insert, update=tables.update, delete=tables.delete)\n",
    )

    async def resolve_import(source_file: SourceFile, module: str, level: int):
        return {
            (direct_entry.key, "store", 1): store,
            (helper_entry.key, "helpers", 1): helper,
            (helper.key, "store", 1): store,
        }.get((source_file.key, module, level))

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    direct_result = await analyze_workflow(
        direct_entry, "run", resolve_import, summarize
    )
    helper_result = await analyze_workflow(
        helper_entry, "run", resolve_import, summarize
    )

    assert direct_result["sdk_calls"] == [
        "bifrost.tables.get",
        "bifrost.tables.insert",
        "bifrost.tables.update",
    ]
    assert helper_result["sdk_calls"] == [
        "bifrost.tables.get",
        "bifrost.tables.insert",
    ]
    assert "bifrost.tables.delete" not in direct_result["sdk_calls"]
    assert "bifrost.tables.delete" not in helper_result["sdk_calls"]
    assert direct_result["unknowns"] == []
    assert helper_result["unknowns"] == []


@pytest.mark.asyncio
async def test_local_function_and_method_result_data_stay_at_return_boundaries() -> None:
    entry = _source(
        "workflows/run.py",
        "def _deterministic_outcome():\n"
        "    return {'value': 'ok'}\n\n"
        "class GraphClient:\n"
        "    def get(self):\n"
        "        return {'value': 'ok'}\n\n"
        "def run():\n"
        "    row = _deterministic_outcome()\n"
        "    client = GraphClient()\n"
        "    graph_row = client.get()\n"
        "    return row.get('value'), graph_row.get('value')\n",
    )

    async def resolve_import(_source_file: SourceFile, _module: str, _level: int):
        return None

    async def summarize(source_file: SourceFile) -> dict:
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_return_reassigned_to_data_does_not_break_branch_analysis() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from .helpers import load\n"
        "from bifrost import config\n"
        "def run(clear):\n"
        "    value = load()\n"
        "    if clear:\n"
        "        value = {}\n"
        "    return value.get('name'), config.get('setting')\n",
    )
    helper = _source("workflows/helpers.py", "def load():\n    return {'name': 'Contoso'}\n")

    async def resolve_import(source_file, module, level):
        return helper if (source_file.key, module, level) == (entry.key, "helpers", 1) else None

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_nullable_local_class_field_traverses_to_declared_data_field() -> None:
    entry = _source(
        "workflows/contoso.py",
        "class KnowledgeEntry:\n    metadata: dict\n\n"
        "class IndexDecision:\n"
        "    entry: KnowledgeEntry | None\n"
        "    def value(self):\n"
        "        return self.entry.metadata.get('status')\n\n"
        "def run():\n"
        "    return IndexDecision().value()\n",
    )

    async def resolve_import(_source_file, _module, _level):
        return None

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)
    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_classmethod_instance_and_json_property_keep_data_return_boundary() -> None:
    entry = _source(
        "workflows/contoso.py",
        "import json\n\n"
        "class Plan:\n"
        "    @classmethod\n"
        "    def from_dict(cls, value) -> dict:\n"
        "        obj = cls()\n"
        "        obj.payload_json = '{}'\n"
        "        return obj\n\n"
        "    @property\n"
        "    def payload(self):\n"
        "        return json.loads(self.payload_json)\n\n"
        "def run():\n"
        "    return Plan.from_dict({}).payload.get('value')\n",
    )

    async def resolve_import(_source_file, _module, _level):
        return None

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)
    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_constructor_stored_on_self_routes_its_reachable_method() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from .parser import BlockParser\n\n"
        "def run():\n"
        "    return BlockParser().parse()\n",
    )
    parser = _source(
        "workflows/parser.py",
        "from .util import Registry\n\n"
        "class BlockParser:\n"
        "    def __init__(self):\n"
        "        self.processors = Registry()\n\n"
        "    def parse(self):\n"
        "        return self.processors.register()\n",
    )
    util = _source(
        "workflows/util.py",
        "from bifrost import config\n\n"
        "class Registry:\n"
        "    def register(self):\n"
        "        return config.get('contoso')\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "parser", 1): parser,
            (parser.key, "util", 1): util,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)
    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_typed_class_field_routes_a_dotted_method_to_its_source() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from .decision import Decision\n\n"
        "def run():\n"
        "    return Decision().value()\n",
    )
    decision = _source(
        "workflows/decision.py",
        "from .record import Record\n\n"
        "class Decision:\n"
        "    record: Record | None\n\n"
        "    def value(self):\n"
        "        return self.record.lookup()\n",
    )
    record = _source(
        "workflows/record.py",
        "from bifrost import config\n\n"
        "class Record:\n"
        "    def lookup(self):\n"
        "        return config.get('contoso')\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "decision", 1): decision,
            (decision.key, "record", 1): record,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_class_callable_field_preserves_its_passed_sdk_callback() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from bifrost import tables\n"
        "from .adapter import Adapter\n\n"
        "async def run():\n"
        "    adapter = Adapter(get=tables.get)\n"
        "    return await adapter.get('records')\n",
    )
    adapter = _source(
        "workflows/adapter.py",
        "class Adapter:\n"
        "    def __init__(self, *, get):\n"
        "        self.get = get\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "adapter", 1): adapter,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_dataclass_positional_callbacks_follow_only_invoked_fields() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from bifrost import tables\n"
        "from .adapter import Adapter\n\n"
        "async def run():\n"
        "    adapter = Adapter(\n"
        "        'records', tables.insert, tables.get, tables.update, tables.delete\n"
        "    )\n"
        "    return await adapter.acquire()\n",
    )
    adapter = _source(
        "workflows/adapter.py",
        "from dataclasses import dataclass, field\n"
        "from typing import Callable, ClassVar\n\n"
        "@dataclass\n"
        "class Adapter:\n"
        "    DEFAULT_TABLE: ClassVar[str] = 'records'\n"
        "    table_name: str\n"
        "    unused_marker: str = field(init=False)\n"
        "    insert: Callable\n"
        "    get: Callable\n"
        "    update: Callable\n"
        "    delete: Callable\n\n"
        "    async def acquire(self):\n"
        "        await self.insert({'id': 'contoso'})\n"
        "        await self.update({'id': 'contoso'})\n"
        "        return await self.get(self.table_name)\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "adapter", 1): adapter,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == [
        "bifrost.tables.get",
        "bifrost.tables.insert",
        "bifrost.tables.update",
    ]
    assert "bifrost.tables.delete" not in result["sdk_calls"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_factory_data_field_continuation_stays_at_the_data_boundary() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from .factory import load_evidence\n\n"
        "def run():\n"
        "    return load_evidence().by_ticket.get('contoso')\n",
    )
    factory = _source(
        "workflows/factory.py",
        "from .evidence import Evidence\n\n"
        "def load_evidence():\n"
        "    return Evidence()\n",
    )
    evidence = _source(
        "workflows/evidence.py",
        "class Evidence:\n"
        "    by_ticket: dict\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "factory", 1): factory,
            (factory.key, "evidence", 1): evidence,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == []
    assert result["unknowns"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "sdk_calls", "has_unknown"),
    [
        ("get", ["bifrost.config.get"], False),
        ("future_capability", [], True),
    ],
)
async def test_imported_dataclass_sdk_field_binding_overrides_data_annotation(
    method, sdk_calls, has_unknown
) -> None:
    entry = _source(
        "workflows/contoso.py",
        "from bifrost import config\n"
        "from .holder import Holder\n\n"
        "def run():\n"
        "    holder = Holder(client=config)\n"
        f"    return holder.client.{method}('contoso')\n",
    )
    holder = _source(
        "workflows/holder.py",
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Holder:\n"
        "    client: dict\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "holder", 1): holder,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == sdk_calls
    assert bool(result["unknowns"]) is has_unknown


@pytest.mark.asyncio
async def test_imported_constructor_tuple_and_default_callback_fields_follow_only_invoked_callbacks() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from bifrost import config, tables\n"
        "from .coordinator import Coordinator\n\n"
        "async def run():\n"
        "    coordinator = Coordinator(\n"
        "        adapter=tables.get,\n"
        "        verify_customer=config.get,\n"
        "        clock=config.get,\n"
        "    )\n"
        "    await coordinator.acquire()\n"
        "    return coordinator.now()\n",
    )
    coordinator = _source(
        "workflows/coordinator.py",
        "class Coordinator:\n"
        "    def __init__(self, *, adapter, verify_customer, clock=None):\n"
        "        self.adapter, self.verify_customer = adapter, verify_customer\n"
        "        self.clock = clock or (lambda: 'now')\n\n"
        "    async def acquire(self):\n"
        "        return await self.adapter('records')\n\n"
        "    def now(self):\n"
        "        return self.clock('time')\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "coordinator", 1): coordinator,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_default_callback_lambda_keeps_its_reachable_sdk_call() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from .runner import Runner\n\n"
        "def run():\n"
        "    return Runner().run()\n",
    )
    runner = _source(
        "workflows/runner.py",
        "from bifrost import config\n\n"
        "class Runner:\n"
        "    def __init__(self, loader=None):\n"
        "        self.loader = loader or (lambda: config.get('contoso'))\n\n"
        "    def run(self):\n"
        "        return self.loader()\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "runner", 1): runner,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_from_package_import_class_base_and_constructor_field_keep_class_members() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from .worker import Worker\n\n"
        "def run():\n"
        "    return Worker().run()\n",
    )
    package = _source("workflows/__init__.py", "")
    worker = _source(
        "workflows/worker.py",
        "from . import utility\n\n"
        "class Worker(utility.Base):\n"
        "    def __init__(self):\n"
        "        self.registry = utility.Registry()\n\n"
        "    def run(self):\n"
        "        self.reset()\n"
        "        return self.registry.register()\n",
    )
    utility = _source(
        "workflows/utility.py",
        "from bifrost import config, tables\n\n"
        "class Base:\n"
        "    def reset(self):\n"
        "        return config.get('contoso')\n\n"
        "class Registry:\n"
        "    def register(self):\n"
        "        return tables.get('records')\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "worker", 1): worker,
            (worker.key, "", 1): package,
            (package.key, "utility", 1): utility,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_class_field_property_continuation_uses_the_property_return_shape() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from .decision import Decision\n\n"
        "def run():\n"
        "    return Decision().value()\n",
    )
    decision = _source(
        "workflows/decision.py",
        "from .record import Record\n\n"
        "class Decision:\n"
        "    record: Record | None\n\n"
        "    def value(self):\n"
        "        return self.record.catalog.get('contoso')\n",
    )
    record = _source(
        "workflows/record.py",
        "from bifrost import config\n\n"
        "class Record:\n"
        "    @property\n"
        "    def catalog(self):\n"
        "        return config\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "decision", 1): decision,
            (decision.key, "record", 1): record,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_literal_class_callback_map_follows_only_finite_imported_candidates() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from .formatter import Markdown\n\n"
        "def run():\n"
        "    return Markdown().render()\n",
    )
    formatter = _source(
        "workflows/formatter.py",
        "from typing import Callable, ClassVar\n"
        "from .formatters import to_html, to_xhtml, to_dead\n\n"
        "class Markdown:\n"
        "    FORMATS: ClassVar[dict[str, Callable]] = {'html': to_html, 'xhtml': to_xhtml}\n\n"
        "    DEAD_FORMATS: ClassVar[dict[str, Callable]] = {'dead': to_dead}\n\n"
        "    def __init__(self):\n"
        "        self.set_output_format('html')\n\n"
        "    def set_output_format(self, output_format):\n"
        "        def unreachable():\n"
        "            self.serializer = self.DEAD_FORMATS['dead']\n"
        "        try:\n"
        "            self.serializer = self.FORMATS[output_format]\n"
        "        except KeyError:\n"
        "            self.serializer = self.FORMATS['html']\n\n"
        "    def render(self):\n"
        "        return self.serializer('contoso')\n",
    )
    formatters = _source(
        "workflows/formatters.py",
        "from bifrost import config, tables\n\n"
        "def to_html(value):\n    return config.get(value)\n\n"
        "def to_xhtml(value):\n    return tables.get(value)\n\n"
        "def to_dead(value):\n    return tables.delete(value)\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "formatter", 1): formatter,
            (formatter.key, "formatters", 1): formatters,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)
    assert result["sdk_calls"] == ["bifrost.config.get", "bifrost.tables.get"]
    assert "bifrost.tables.delete" not in result["sdk_calls"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_imported_constructor_callback_is_followed_only_when_its_field_is_invoked() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from services.batch import BatchCoordinator\n"
        "from .callbacks import audit, unused\n\n"
        "def run():\n"
        "    return BatchCoordinator(audit_sink=audit, unused_sink=unused).run()\n",
    )
    batch = _source(
        "services/batch.py",
        "class BatchCoordinator:\n"
        "    def __init__(self, audit_sink, unused_sink):\n"
        "        self.audit_sink = audit_sink\n"
        "        self.unused_sink = unused_sink\n\n"
        "    def run(self):\n"
        "        return self.audit_sink()\n",
    )
    callbacks = _source(
        "workflows/callbacks.py",
        "from bifrost import config, tables\n\n"
        "def audit():\n    return config.get('contoso')\n\n"
        "def unused():\n    return tables.get('contoso')\n",
    )

    async def resolve_import(source_file, module, level):
        return {
            (entry.key, "services.batch", 0): batch,
            (entry.key, "callbacks", 1): callbacks,
        }.get((source_file.key, module, level))

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)
    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert "bifrost.tables.get" not in result["sdk_calls"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_static_local_method_return_continuation_uses_actual_sdk_shape_not_annotation() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from bifrost import config\n\n"
        "class Service:\n"
        "    @staticmethod\n"
        "    def _catalog() -> dict:\n"
        "        return config\n\n"
        "def run():\n"
        "    return Service._catalog().get('contoso')\n",
    )

    async def resolve_import(_source_file, _module, _level):
        return None

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)
    assert result["sdk_calls"] == ["bifrost.config.get"]
    assert result["unknowns"] == []


@pytest.mark.asyncio
async def test_babel_is_external_unless_a_local_source_is_resolved() -> None:
    entry = _source(
        "workflows/contoso.py",
        "from babel.numbers import format_decimal\n\n"
        "def run():\n"
        "    return format_decimal(1)\n",
    )
    local_babel = _source(
        "workflows/babel/numbers.py",
        "from bifrost import config\n\n"
        "def format_decimal(value):\n    return config.get('contoso')\n",
    )

    async def absent(_source_file, _module, _level):
        return None

    async def present(source_file, module, level):
        return (
            local_babel
            if (source_file.key, module, level) == (entry.key, "babel.numbers", 0)
            else None
        )

    async def summarize(source_file):
        return summarize_source(source_file.source)

    absent_result = await analyze_workflow(entry, "run", absent, summarize)
    local_result = await analyze_workflow(entry, "run", present, summarize)
    assert absent_result["sdk_calls"] == []
    assert absent_result["unknowns"] == []
    assert local_result["sdk_calls"] == ["bifrost.config.get"]
    assert local_result["unknowns"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [
    "from bifrost.client import get_client\n"
    "async def run():\n"
    "    client = get_client()\n"
    "    return await client.future_capability()\n",
    "from bifrost import integrations\n"
    "async def run():\n"
    "    integration = await integrations.get('contoso')\n"
    "    return await integration.oauth.future_capability()\n",
])
async def test_unknown_capability_methods_on_sdk_clients_remain_unresolved(source) -> None:
    entry = _source("workflows/contoso.py", source)

    async def resolve_import(_source_file, _module, _level):
        return None

    async def summarize(source_file):
        return summarize_source(source_file.source)

    result = await analyze_workflow(entry, "run", resolve_import, summarize)

    assert any("unrecognized SDK call" in note for note in result["unknowns"])
