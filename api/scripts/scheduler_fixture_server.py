"""Local HTTP and Git fixtures for integration tests.

This process deliberately behaves like external OAuth, embeddings, and Git
providers while remaining entirely inside the debug/test Compose network.
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse


ROOT = Path(tempfile.gettempdir()) / "bifrost-scheduler-fixtures"
WORK_REPO = ROOT / "solution-update-work"
BARE_REPO = ROOT / "solution-update.git"

FIXTURE_OAUTH_CLIENT_ID = "scheduler-fixture-client"
FIXTURE_OAUTH_CLIENT_SECRET = "scheduler-fixture-secret"
FIXTURE_OAUTH_REFRESH_TOKEN = "scheduler-fixture-refresh"
FIXTURE_OAUTH_CODE = "scheduler-fixture-code"
FIXTURE_OAUTH_SCOPE = "fixture.read"
FIXTURE_MCP_ACCESS_TOKEN = "scheduler-fixture-access-service"
FIXTURE_MCP_TOOL_NAME = "scheduler_fixture_echo"


def mcp_tool_catalog() -> list[dict[str, object]]:
    return [
        {
            "name": FIXTURE_MCP_TOOL_NAME,
            "description": "Echoes deterministic scheduler fixture input.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                },
                "required": ["message"],
            },
        }
    ]


def mcp_jsonrpc_response(request: dict[str, object]) -> dict[str, object] | None:
    request_id = request.get("id")
    method = request.get("method")
    if request_id is None:
        return None

    if method == "initialize":
        params = request.get("params")
        protocol_version = "2024-11-05"
        if isinstance(params, dict) and isinstance(params.get("protocolVersion"), str):
            protocol_version = params["protocolVersion"]
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "scheduler-fixture-mcp",
                    "version": "1.0.0",
                },
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": mcp_tool_catalog()},
        }

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found"},
    }


def _run_git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def prepare_solution_repository() -> None:
    """Create a deterministic remote whose descriptor is newer than the install."""
    shutil.rmtree(ROOT, ignore_errors=True)
    WORK_REPO.mkdir(parents=True)
    (WORK_REPO / "bifrost.solution.yaml").write_text(
        "slug: scheduler-update-fixture\n"
        "name: Scheduler Update Fixture\n"
        "version: 2.0.0\n",
        encoding="utf-8",
    )
    _run_git("init", "--initial-branch=main", cwd=WORK_REPO)
    _run_git("config", "user.email", "scheduler-fixture@gobifrost.local", cwd=WORK_REPO)
    _run_git("config", "user.name", "Bifrost Scheduler Fixture", cwd=WORK_REPO)
    _run_git("add", "bifrost.solution.yaml", cwd=WORK_REPO)
    _run_git("commit", "-m", "Fixture Solution 2.0.0", cwd=WORK_REPO)
    _run_git("clone", "--bare", str(WORK_REPO), str(BARE_REPO))
    (BARE_REPO / "git-daemon-export-ok").touch()


def _chat_content(request: dict[str, object]) -> str:
    if request.get("model") == "fixture-summary":
        return json.dumps(
            {
                "asked": "Run the fixture chat agent.",
                "did": "Called the fixture chat model.",
                "answered": "ok",
                "confidence": 0.9,
                "metadata": {"fixture": "summary"},
            }
        )
    return "ok"


def chat_completion_payload(request: dict[str, object]) -> dict[str, object]:
    return {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "created": 0,
        "model": request.get("model", "fixture-chat"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": _chat_content(request)},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


def chat_completion_stream_events(
    request: dict[str, object],
) -> list[dict[str, object] | str]:
    model = request.get("model", "fixture-chat")
    return [
        {
            "id": "chatcmpl-fixture",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": ""},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-fixture",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": _chat_content(request)},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-fixture",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        },
        "[DONE]",
    ]


def encode_sse_events(events: list[dict[str, object] | str]) -> bytes:
    lines = []
    for event in events:
        data = event if isinstance(event, str) else json.dumps(event)
        lines.append(f"data: {data}\n\n")
    return "".join(lines).encode()


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = "BifrostSchedulerFixture/1.0"

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        if "\r" in location or "\n" in location:
            self._json(400, {"error": "invalid_request"})
            return
        self.send_response(302)
        self.send_header("Location", location.replace("\r", "%0D").replace("\n", "%0A"))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"status": "ok"})
            return

        if parsed.path == "/oauth/authorize":
            query = parse_qs(parsed.query)
            redirect_uri = query.get("redirect_uri", [""])[0]
            state = query.get("state", [""])[0]
            client_id = query.get("client_id", [""])[0]
            response_type = query.get("response_type", [""])[0]
            code_challenge = query.get("code_challenge", [""])[0]
            code_challenge_method = query.get("code_challenge_method", [""])[0]
            if (
                client_id != FIXTURE_OAUTH_CLIENT_ID
                or response_type != "code"
                or not state
                or not redirect_uri.endswith("/api/mcp/oauth/callback")
                or not code_challenge
                or code_challenge_method != "S256"
            ):
                self._json(400, {"error": "invalid_request"})
                return
            self._redirect(
                redirect_uri
                + "?"
                + urlencode({"code": FIXTURE_OAUTH_CODE, "state": state})
            )
            return

        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        if self.path == "/v1/embeddings":
            request = json.loads(body or b"{}")
            raw_input = request.get("input", [])
            inputs = raw_input if isinstance(raw_input, list) else [raw_input]
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "object": "embedding",
                            "index": index,
                            # Match OpenAI's default embedding dimension so this
                            # deterministic fixture does not force an unrelated
                            # global reindex when earlier tests created real
                            # text-embedding-3-small rows.
                            "embedding": [1.0] + [0.0] * 1535,
                        }
                        for index, _text in enumerate(inputs)
                    ],
                    "model": request.get("model", "fixture-embedding"),
                    "usage": {
                        "prompt_tokens": len(inputs),
                        "total_tokens": len(inputs),
                    },
                },
            )
            return

        if self.path == "/v1/chat/completions":
            request = json.loads(body or b"{}")
            if request.get("stream") is True:
                body_bytes = encode_sse_events(chat_completion_stream_events(request))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
                return
            self._json(200, chat_completion_payload(request))
            return

        if self.path == "/mcp":
            authorization = self.headers.get("Authorization", "")
            if authorization != f"Bearer {FIXTURE_MCP_ACCESS_TOKEN}":
                self._json(401, {"error": "unauthorized"})
                return
            try:
                request_payload = json.loads(body or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid_json"})
                return

            if isinstance(request_payload, list):
                responses = [
                    response
                    for item in request_payload
                    if isinstance(item, dict)
                    for response in [mcp_jsonrpc_response(item)]
                    if response is not None
                ]
                if responses:
                    self._json(200, responses)
                else:
                    self._empty(202)
                return

            if not isinstance(request_payload, dict):
                self._json(400, {"error": "invalid_jsonrpc"})
                return
            response = mcp_jsonrpc_response(request_payload)
            if response is None:
                self._empty(202)
                return
            self._json(200, response)
            return

        if self.path != "/oauth/token":
            self._json(404, {"error": "not_found"})
            return
        form = parse_qs(body.decode())
        grant_type = form.get("grant_type", [""])[0]
        if grant_type == "refresh_token":
            expected = {
                "grant_type": "refresh_token",
                "refresh_token": FIXTURE_OAUTH_REFRESH_TOKEN,
                "client_id": FIXTURE_OAUTH_CLIENT_ID,
                "client_secret": FIXTURE_OAUTH_CLIENT_SECRET,
            }
            if any(form.get(key) != [value] for key, value in expected.items()):
                self._json(
                    400,
                    {
                        "error": "invalid_grant",
                        "error_description": "Unexpected scheduler fixture credentials",
                    },
                )
                return
            self._json(
                200,
                {
                    "access_token": "scheduler-fixture-access-refreshed",
                    "refresh_token": FIXTURE_OAUTH_REFRESH_TOKEN,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": FIXTURE_OAUTH_SCOPE,
                },
            )
            return

        if grant_type == "client_credentials":
            expected = {
                "grant_type": "client_credentials",
                "client_id": FIXTURE_OAUTH_CLIENT_ID,
                "client_secret": FIXTURE_OAUTH_CLIENT_SECRET,
                "scope": FIXTURE_OAUTH_SCOPE,
            }
            if any(form.get(key) != [value] for key, value in expected.items()):
                self._json(
                    400,
                    {
                        "error": "invalid_client",
                        "error_description": "Unexpected scheduler fixture client credentials",
                    },
                )
                return
            self._json(
                200,
                {
                    "access_token": FIXTURE_MCP_ACCESS_TOKEN,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": FIXTURE_OAUTH_SCOPE,
                },
            )
            return

        if grant_type == "authorization_code":
            expected = {
                "grant_type": "authorization_code",
                "code": FIXTURE_OAUTH_CODE,
                "client_id": FIXTURE_OAUTH_CLIENT_ID,
                "client_secret": FIXTURE_OAUTH_CLIENT_SECRET,
            }
            redirect_uri = form.get("redirect_uri", [""])[0]
            code_verifier = form.get("code_verifier", [""])[0]
            if (
                any(form.get(key) != [value] for key, value in expected.items())
                or not redirect_uri.endswith("/api/mcp/oauth/callback")
                or len(code_verifier) < 32
            ):
                self._json(
                    400,
                    {
                        "error": "invalid_grant",
                        "error_description": "Unexpected scheduler fixture authorization code request",
                    },
                )
                return
            self._json(
                200,
                {
                    "access_token": "scheduler-fixture-access-authorized",
                    "refresh_token": FIXTURE_OAUTH_REFRESH_TOKEN,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": FIXTURE_OAUTH_SCOPE,
                },
            )
            return

        self._json(
            400,
            {
                "error": "unsupported_grant_type",
                "error_description": "Unsupported scheduler fixture OAuth grant",
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    prepare_solution_repository()
    git_daemon = subprocess.Popen(
        [
            "git",
            "daemon",
            "--reuseaddr",
            "--export-all",
            f"--base-path={ROOT}",
            "--listen=0.0.0.0",
            "--port=9418",
            str(ROOT),
        ]
    )
    server = ThreadingHTTPServer(("0.0.0.0", 8080), FixtureHandler)

    def stop(*_: object) -> None:
        raise SystemExit

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        # Signals and Ctrl+C are the expected clean shutdown paths.
        pass
    finally:
        git_daemon.terminate()
        git_daemon.wait(timeout=5)


if __name__ == "__main__":
    main()
