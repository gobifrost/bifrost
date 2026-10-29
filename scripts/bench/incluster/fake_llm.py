"""Deterministic OpenAI-compatible model for benchmark agent classes.

Stdlib only; runs on the Bifrost image as ``python fake_llm.py``. The reply is a
pure function of the request, so agent class timings measure the platform, not
the model:

- a tool result is the newest message, or no tools are offered: final text;
- ``run_code`` is offered (CodeMode): one ``run_code`` call whose code calls
  every catalogued ``bench_tool`` function and returns their results;
- otherwise: one call to each offered ``bench_tool`` tool.

``/v1/responses`` answers 404 so Bifrost selects Chat Completions.
"""

from __future__ import annotations

import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "bench-fake"
LATENCY_S = int(os.environ.get("FAKE_LLM_LATENCY_MS", "0")) / 1000
USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
_FUNCTION = re.compile(r"(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def decide(body: dict) -> dict:
    """The assistant message for a chat-completions request."""
    messages = body.get("messages", [])
    tools = [tool["function"] for tool in body.get("tools") or [] if tool.get("type") == "function"]
    if not tools or (messages and messages[-1].get("role") == "tool"):
        return {"role": "assistant", "content": "bench complete"}
    names = [tool["name"] for tool in tools]
    if "run_code" in names:
        catalog = "\n".join(_text(m.get("content")) for m in messages if m.get("role") in ("system", "developer"))
        catalog += "\n" + next(tool.get("description", "") for tool in tools if tool["name"] == "run_code")
        functions = sorted({name for name in _FUNCTION.findall(catalog) if "bench_tool" in name})
        code = "results = {}\n" + "".join(f"results['{name}'] = {name}()\n" for name in functions) + "results"
        return _tool_calls([("run_code", {"code": code})])
    return _tool_calls([(name, {}) for name in names if "bench_tool" in name])


def _tool_calls(calls: list[tuple[str, dict]]) -> dict:
    if not calls:
        return {"role": "assistant", "content": "bench complete"}
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"call_{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for index, (name, args) in enumerate(calls)
        ],
    }


def _completion(message: dict) -> dict:
    finish = "tool_calls" if message.get("tool_calls") else "stop"
    return {
        "id": "chatcmpl-bench",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": USAGE,
    }


def _stream_chunks(message: dict, include_usage: bool) -> list[dict]:
    base = {"id": "chatcmpl-bench", "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL}
    delta: dict = {"role": "assistant"}
    if message.get("tool_calls"):
        delta["tool_calls"] = [dict(call, index=index) for index, call in enumerate(message["tool_calls"])]
        finish = "tool_calls"
    else:
        delta["content"] = message["content"]
        finish = "stop"
    chunks: list[dict] = [
        dict(base, choices=[{"index": 0, "delta": delta, "finish_reason": None}]),
        dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": finish}]),
    ]
    if include_usage:
        chunks.append(dict(base, choices=[], usage=USAGE))
    return chunks


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        """Silence per-request logging."""

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "bench"}]})
        else:
            self._json(404, {"error": {"message": "unknown endpoint"}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": {"message": "unknown endpoint"}})
            return
        if LATENCY_S:
            time.sleep(LATENCY_S)
        message = decide(body)
        if not body.get("stream"):
            self._json(200, _completion(message))
            return
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        payload = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in _stream_chunks(message, include_usage))
        payload += "data: [DONE]\n\n"
        data = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
