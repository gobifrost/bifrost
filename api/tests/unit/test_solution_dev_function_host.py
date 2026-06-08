import asyncio
import textwrap
from pathlib import Path

import pytest

from bifrost.solution_dev.function_host import discover_functions


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

    fns = discover_functions(tmp_path)

    assert "functions/hello.py::main" in fns
    assert "modules/sub/calc.py::add" in fns
    assert callable(fns["functions/hello.py::main"])


from bifrost.solution_dev.function_host import FunctionHost


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
