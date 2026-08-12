"""Local HTTP and Git fixtures for scheduler integration tests.

This process deliberately behaves like external OAuth and Git providers while
remaining entirely inside the debug/test Compose network.
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs


ROOT = Path(tempfile.gettempdir()) / "bifrost-scheduler-fixtures"
WORK_REPO = ROOT / "solution-update-work"
BARE_REPO = ROOT / "solution-update.git"


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


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = "BifrostSchedulerFixture/1.0"

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health":
            self._json(200, {"status": "ok"})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/oauth/token":
            self._json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode())
        expected = {
            "grant_type": "refresh_token",
            "refresh_token": "scheduler-fixture-refresh",
            "client_id": "scheduler-fixture-client",
            "client_secret": "scheduler-fixture-secret",
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
                "refresh_token": "scheduler-fixture-refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": "fixture.read",
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
