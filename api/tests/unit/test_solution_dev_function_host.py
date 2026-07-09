import asyncio
import textwrap
from pathlib import Path

import pytest

from bifrost.solution_dev.function_host import (
    FunctionHost,
    LocalWorkflowImportError,
    LocalWorkflowResolutionError,
    discover_functions,
)


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body))


def test_discovers_decorated_functions_in_arbitrary_folders(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\nscope: org\n")
    _write(tmp_path / "functions/hello.py", '''
        from bifrost import workflow

        @workflow
        async def main():
            return {"message": "hi"}
    ''')
    _write(tmp_path / "modules/sub/calc.py", '''
        from bifrost import workflow

        @workflow
        async def add():
            return {"ok": True}
    ''')

    fns, _ = discover_functions(tmp_path)

    assert "functions/hello.py::main" in fns
    assert "modules/sub/calc.py::add" in fns
    assert callable(fns["functions/hello.py::main"])



def test_host_runs_a_function_and_returns_result(tmp_path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\nscope: org\n")
    _write(tmp_path / "functions/echo.py", '''
        from bifrost import workflow

        @workflow
        async def main(name: str = "world"):
            return {"hello": name}
    ''')
    host = FunctionHost(tmp_path)
    host.reload()

    result = asyncio.run(host.run("functions/echo.py::main", {"name": "bifrost"}))
    assert result == {"hello": "bifrost"}


def test_host_unknown_ref_raises_keyerror(tmp_path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\nscope: org\n")
    host = FunctionHost(tmp_path)
    host.reload()
    with pytest.raises(KeyError):
        asyncio.run(host.run("nope/missing.py::main", {}))


def test_discovery_records_import_failures(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/good.py", '''
        from bifrost import workflow

        @workflow
        async def main():
            return {"ok": True}
    ''')
    _write(tmp_path / "functions/broken.py", '''
        import does_not_exist_anywhere

        from bifrost import workflow

        @workflow
        async def main():
            return {"ok": True}
    ''')

    fns, failures = discover_functions(tmp_path)

    assert "functions/good.py::main" in fns
    assert not any(ref.startswith("functions/broken.py") for ref in fns)
    assert "functions/broken.py" in failures
    assert "does_not_exist_anywhere" in failures["functions/broken.py"]


def test_host_exposes_failures(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/broken.py", '''
        raise RuntimeError("boom at import time")
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    assert host.refs() == []
    assert "boom at import time" in host.failures()["functions/broken.py"]


def test_host_resolves_manifest_uuid_and_name_to_local_ref(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/preview.py", '''
        from bifrost import workflow

        @workflow
        async def recipients():
            return {"ok": True}
    ''')
    _write(tmp_path / ".bifrost/workflows.yaml", '''
        workflows:
          11111111-1111-1111-1111-111111111111:
            id: 11111111-1111-1111-1111-111111111111
            name: Preview Recipients
            path: functions/preview.py
            function_name: recipients
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    assert host.resolve("functions/preview.py::recipients") == "functions/preview.py::recipients"
    assert host.resolve("11111111-1111-1111-1111-111111111111") == "functions/preview.py::recipients"
    assert host.resolve("Preview Recipients") == "functions/preview.py::recipients"
    assert host.resolve("No Such Workflow") is None


def test_host_rejects_ambiguous_local_workflow_name(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/a.py", '''
        from bifrost import workflow

        @workflow
        async def main():
            return {"a": True}
    ''')
    _write(tmp_path / "functions/b.py", '''
        from bifrost import workflow

        @workflow
        async def main():
            return {"b": True}
    ''')
    _write(tmp_path / ".bifrost/workflows.yaml", '''
        workflows:
          11111111-1111-1111-1111-111111111111:
            name: Duplicate
            path: functions/a.py
            function_name: main
          22222222-2222-2222-2222-222222222222:
            name: Duplicate
            path: functions/b.py
            function_name: main
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    with pytest.raises(LocalWorkflowResolutionError, match="ambiguous"):
        host.resolve("Duplicate")


def test_host_raises_import_error_for_broken_target(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/broken.py", '''
        import does_not_exist_anywhere
    ''')
    _write(tmp_path / ".bifrost/workflows.yaml", '''
        workflows:
          11111111-1111-1111-1111-111111111111:
            name: Broken One
            path: functions/broken.py
            function_name: main
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    # All three ref shapes surface the import error instead of a clean miss.
    with pytest.raises(LocalWorkflowImportError, match="does_not_exist_anywhere"):
        host.resolve("functions/broken.py::main")
    with pytest.raises(LocalWorkflowImportError, match="does_not_exist_anywhere"):
        host.resolve("11111111-1111-1111-1111-111111111111")
    with pytest.raises(LocalWorkflowImportError, match="does_not_exist_anywhere"):
        host.resolve("Broken One")


def test_host_manifest_entry_without_local_file_is_a_clean_miss(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / ".bifrost/workflows.yaml", '''
        workflows:
          11111111-1111-1111-1111-111111111111:
            name: Ghost
            path: functions/ghost.py
            function_name: main
    ''')

    host = FunctionHost(tmp_path)
    host.reload()

    assert host.resolve("Ghost") is None


def test_host_survives_malformed_workflow_manifest(tmp_path: Path):
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\n")
    _write(tmp_path / "functions/hello.py", '''
        from bifrost import workflow

        @workflow
        async def main():
            return {"ok": True}
    ''')
    _write(tmp_path / ".bifrost/workflows.yaml", '''
        workflows:
          bad: [unclosed
    ''')

    host = FunctionHost(tmp_path)
    host.reload()  # must not raise

    assert host.resolve("functions/hello.py::main") == "functions/hello.py::main"
    assert host.resolve("hello") is None  # aliases unavailable, clean miss
    assert "invalid YAML" in host.failures()[".bifrost/workflows.yaml"]
