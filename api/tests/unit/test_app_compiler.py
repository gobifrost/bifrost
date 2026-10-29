import json

import pytest
from src.services.app_compiler import AppCompilerService
from src.services.node_subprocess import run_node_script


@pytest.fixture
def compiler():
    return AppCompilerService()


class TestAppCompilerService:
    @pytest.mark.asyncio
    async def test_compile_simple_component(self, compiler):
        source = 'export default function Page() { return <div>Hello</div>; }'
        result = await compiler.compile_file(source, "pages/index.tsx")
        assert result.success is True
        assert result.compiled is not None
        assert "__defaultExport__" in result.compiled
        assert result.error is None

    @pytest.mark.asyncio
    async def test_compile_with_bifrost_imports(self, compiler):
        source = '''import { Button, useState } from "bifrost";
export default function Page() {
  const [count, setCount] = useState(0);
  return <Button onClick={() => setCount(count + 1)}>{count}</Button>;
}'''
        result = await compiler.compile_file(source, "pages/index.tsx")
        assert result.success is True
        assert "var {" in result.compiled or "var { " in result.compiled

    @pytest.mark.asyncio
    async def test_compile_syntax_error(self, compiler):
        source = 'export default function Page() { return <div>; }'
        result = await compiler.compile_file(source, "pages/index.tsx")
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_compile_batch(self, compiler):
        files = [
            {"path": "pages/index.tsx", "source": "export default function A() { return <div>A</div>; }"},
            {"path": "pages/about.tsx", "source": "export default function B() { return <div>B</div>; }"},
        ]
        results = await compiler.compile_batch(files)
        assert len(results) == 2
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_compile_batch_partial_failure(self, compiler):
        files = [
            {"path": "pages/good.tsx", "source": "export default function A() { return <div>A</div>; }"},
            {"path": "pages/bad.tsx", "source": "export default function B() { return <div>; }"},
        ]
        results = await compiler.compile_batch(files)
        assert results[0].success is True
        assert results[1].success is False

    @pytest.mark.asyncio
    async def test_compile_typescript_modern_operators_with_legacy_react_scope(
        self, compiler, tmp_path
    ):
        source = '''
type Account = { name?: string };
type Props = { account?: Account };

export default function Page({ account }: Props) {
  return <div>{account?.name ?? "Anonymous"}</div>;
}
'''
        result = await compiler.compile_file(source, "pages/index.tsx")

        assert result.success is True
        assert "react/jsx-runtime" not in result.compiled

        evaluator = tmp_path / "evaluate-compiled-page.js"
        evaluator.write_text(
            '''
let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const { compiled } = JSON.parse(input);
  const React = {
    createElement(type, props, ...children) {
      return { type, props: props || {}, children };
    },
  };
  const Page = new Function("React", `${compiled}\\nreturn __defaultExport__;`)(React);
  process.stdout.write(JSON.stringify(Page({})));
});
''',
            encoding="utf-8",
        )
        returncode, stdout, stderr = await run_node_script(
            evaluator, json.dumps({"compiled": result.compiled}).encode()
        )

        assert returncode == 0, stderr.decode()
        rendered = json.loads(stdout)
        assert rendered["type"] == "div"
        assert rendered["children"] == ["Anonymous"]
