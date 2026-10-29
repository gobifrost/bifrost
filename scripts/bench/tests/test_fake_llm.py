from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "incluster"))

import fake_llm  # noqa: E402


def _tool(name: str, description: str = "") -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {}}}


class DecideTest(unittest.TestCase):
    def test_no_tools_answers_text(self) -> None:
        message = fake_llm.decide({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(message["content"], "bench complete")

    def test_calls_each_bench_tool_once(self) -> None:
        message = fake_llm.decide({
            "messages": [{"role": "user", "content": "go"}],
            "tools": [_tool("bench_tool_lookup"), _tool("bench_tool_count"), _tool("search_tools")],
        })
        names = [call["function"]["name"] for call in message["tool_calls"]]
        self.assertEqual(names, ["bench_tool_lookup", "bench_tool_count"])

    def test_answers_after_tool_result(self) -> None:
        message = fake_llm.decide({
            "messages": [{"role": "user", "content": "go"}, {"role": "tool", "content": "{}"}],
            "tools": [_tool("bench_tool_lookup")],
        })
        self.assertEqual(message["content"], "bench complete")

    def test_code_mode_calls_catalogued_functions(self) -> None:
        message = fake_llm.decide({
            "messages": [
                {"role": "system", "content": "Functions:\nasync def bench_tool_count() -> dict: ...\ndef bench_tool_lookup(): ..."},
                {"role": "user", "content": "go"},
            ],
            "tools": [_tool("run_code", "Run Python.")],
        })
        call = message["tool_calls"][0]["function"]
        self.assertEqual(call["name"], "run_code")
        code = json.loads(call["arguments"])["code"]
        self.assertIn("results['bench_tool_count'] = bench_tool_count()", code)
        self.assertIn("results['bench_tool_lookup'] = bench_tool_lookup()", code)
        self.assertTrue(code.endswith("results"))


class StreamTest(unittest.TestCase):
    def test_tool_call_stream_ends_with_usage(self) -> None:
        chunks = fake_llm._stream_chunks(fake_llm._tool_calls([("bench_tool_count", {})]), include_usage=True)
        self.assertEqual(chunks[0]["choices"][0]["delta"]["tool_calls"][0]["index"], 0)
        self.assertEqual(chunks[1]["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(chunks[-1]["usage"]["total_tokens"], 120)


if __name__ == "__main__":
    unittest.main()
