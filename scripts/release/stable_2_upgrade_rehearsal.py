#!/usr/bin/env python3
"""Qualify real release images across the mandatory authority-protocol bridge.

Only synthetic data is used. Dependencies and credentials live in a unique
private Compose project, removed even after a failed assertion. Evidence never
includes authentication responses, environment dumps, or raw credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import tempfile
import time
from uuid import UUID


SOURCE = "ghcr.io/gobifrost/bifrost-api@sha256:ff8c8aee94c594678f9fe7254e297bad9e1f04c04f9029f8c1e6a66a8d7521c0"
BRIDGE = "ghcr.io/gobifrost/bifrost-api@sha256:9ca80613869946a0fcad769cdfe355ccaa6968a624df9f38b232c1702a9bda9a"
ROOT = Path(__file__).resolve().parents[2]

# Executed inside the old API, using its private environment. Never print tokens.
SEED = r"""
import json, os, urllib.parse, urllib.request
def request(path, data, token=None):
    headers = {"Content-Type": "application/json" if token else "application/x-www-form-urlencoded"}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request("http://localhost:8000" + path, data=data, headers=headers), timeout=30) as r:
        return json.load(r)
auth = request("/auth/login", urllib.parse.urlencode({"username": os.environ["BIFROST_DEFAULT_USER_EMAIL"], "password": os.environ["BIFROST_DEFAULT_USER_PASSWORD"]}).encode())
code = "import logging\nlogging.info('STABLE_2_REHEARSAL_EXECUTED_ONCE')\nresult = {'release_rehearsal': True}\n"
result = request("/api/workflows/execute", json.dumps({"code": code, "script_name": "stable_2_upgrade_rehearsal", "transient": False, "sync": False}).encode(), auth["access_token"])
print(json.dumps({"execution_id": result["execution_id"], "status": result["status"]}))
"""


class Rehearsal:
    def __init__(self, args: argparse.Namespace, private: str):
        self.args = args
        self.project = "bifrost-upgrade-" + args.run_id
        self.evidence = args.evidence_dir / args.run_id
        self.evidence.mkdir(mode=0o700)
        self.password = secrets.token_hex(24)
        self.secret = secrets.token_hex(32)
        self.environment = dict(os.environ, REHEARSAL_IMAGE=SOURCE)
        self.env_file = Path(private) / "compose.env"
        self.env_file.write_text(
            f"REHEARSAL_PASSWORD={self.password}\nREHEARSAL_SECRET={self.secret}\n"
        )
        self.env_file.chmod(0o600)
        self.compose = [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--env-file",
            str(self.env_file),
            "-f",
            str(Path(__file__).with_name("stable_2_upgrade_compose.yml")),
        ]
        self.report: dict = {
            "source": SOURCE,
            "bridge": BRIDGE,
            "target": args.target_api_image,
            "target_source": args.target_source,
            "phases": [],
            "passed": False,
        }
        self.owns_project = False

    def run(
        self,
        command: list[str],
        *,
        stdin: str | None = None,
        check: bool = True,
        timeout: int = 600,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            input=stdin,
            text=True,
            capture_output=True,
            env=self.environment,
            timeout=timeout,
        )
        if check and result.returncode:
            # Some product exceptions include connection URLs. Sanitize before evidence.
            detail = (
                (result.stdout + result.stderr)
                .replace(self.password, "[redacted]")
                .replace(self.secret, "[redacted]")
            )
            (self.evidence / "failure.log").write_text(detail)
            raise RuntimeError(
                f"Command failed; sanitized diagnostics: {self.evidence / 'failure.log'}"
            )
        return result

    def dc(self, *args: str, **kwargs) -> subprocess.CompletedProcess[str]:
        return self.run(self.compose + list(args), **kwargs)

    def save(self) -> None:
        (self.evidence / "receipt.json").write_text(
            json.dumps(self.report, indent=2) + "\n"
        )

    def phase(self, name: str) -> None:
        print(name, flush=True)
        self.report["phases"].append(name)
        self.save()

    def sql(self, statement: str, database: str = "bifrost") -> str:
        return self.dc(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-XAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "bifrost",
            "-d",
            database,
            "-c",
            statement,
        ).stdout.strip()

    def wait(self, predicate, description: str, timeout: int = 240) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(2)
        raise RuntimeError(f"Timed out waiting for {description}")

    def ready(self) -> bool:
        return (
            self.dc(
                "exec",
                "-T",
                "api",
                "curl",
                "-fsS",
                "http://localhost:8000/health/ready",
                check=False,
                timeout=10,
            ).returncode
            == 0
        )

    def start_api(self, image: str) -> None:
        self.environment["REHEARSAL_IMAGE"] = image
        self.dc("run", "--rm", "--no-deps", "init")
        self.dc("up", "-d", "--no-deps", "api")
        self.wait(self.ready, "API readiness")

    def queues(self) -> list[dict]:
        raw = self.dc(
            "exec",
            "-T",
            "rabbit",
            "rabbitmqctl",
            "list_queues",
            "--formatter=json",
            "name",
            "messages",
            "messages_unacknowledged",
        ).stdout
        # rabbitmqctl may prepend a human status line before its JSON array.
        return json.loads(raw[raw.index("[") :])

    def execute(self) -> None:
        label = f"label=com.docker.compose.project={self.project}"
        for inventory in (["ps", "-aq"], ["network", "ls", "-q"], ["volume", "ls", "-q"]):
            existing = self.run(["docker", *inventory, "--filter", label]).stdout
            if existing.strip():
                raise RuntimeError("Refusing to reuse an existing Compose project")
        self.owns_project = True
        self.phase("Verify immutable images and target source")
        for image in (SOURCE, BRIDGE, self.args.target_api_image):
            self.run(["docker", "pull", image])
        witnesses = [
            "api/src/main.py",
            "api/src/services/legacy_broker_drain.py",
            "api/alembic/versions/20261031_authz_claims_gate.py",
        ]
        expected = {
            p.removeprefix("api/"): hashlib.sha256(
                self.run(
                    ["git", "-C", str(ROOT), "show", f"{self.args.target_source}:{p}"]
                ).stdout.encode()
            ).hexdigest()
            for p in witnesses
        }
        probe = "import hashlib,json,sys; from pathlib import Path; print(json.dumps({p:hashlib.sha256(Path('/app',p).read_bytes()).hexdigest() for p in json.load(sys.stdin)}))"
        actual = json.loads(
            self.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-i",
                    "--entrypoint",
                    "python",
                    self.args.target_api_image,
                    "-c",
                    probe,
                ],
                stdin=json.dumps(list(expected)),
            ).stdout
        )
        if actual != expected:
            raise RuntimeError(
                "Target runtime witness hashes do not match selected source"
            )
        self.report["target_source_witnesses"] = expected
        self.phase("Boot actual 1.4.1 and enqueue one real HTTP execution")
        self.dc("up", "-d", "--wait", "postgres", "redis", "rabbit", "seaweedfs")
        self.start_api(SOURCE)
        seeded = json.loads(self.dc("exec", "-T", "api", "python", "-c", SEED).stdout)
        execution_id = str(UUID(seeded["execution_id"]))
        self.report["execution_id"] = execution_id
        self.report["source_revision"] = self.sql(
            "SELECT version_num FROM alembic_version"
        )
        before = self.queues()
        self.report["source_queues"] = before
        if (
            sum(q["messages"] for q in before if q["name"] == "workflow-executions")
            != 1
        ):
            raise RuntimeError(
                "Old API did not leave exactly one Rabbit workflow message"
            )
        if (
            self.dc(
                "exec",
                "-T",
                "redis",
                "redis-cli",
                "EXISTS",
                f"bifrost:exec:{execution_id}:pending",
            ).stdout.strip()
            != "1"
        ):
            raise RuntimeError("Old API did not retain its pending Redis context")
        self.dc("stop", "api")
        self.dc("rm", "-f", "api")
        self.phase("Prove a direct upgrade is rejected on a clone")
        self.sql("CREATE DATABASE direct_upgrade TEMPLATE bifrost", "postgres")
        self.environment["REHEARSAL_IMAGE"] = self.args.target_api_image
        self.environment["REHEARSAL_DATABASE"] = "direct_upgrade"
        rejected = self.dc("run", "--rm", "--no-deps", "init", check=False)
        self.environment["REHEARSAL_DATABASE"] = "bifrost"
        refusal = (rejected.stdout + rejected.stderr).replace(
            self.password, "[redacted]"
        )
        (self.evidence / "direct-upgrade-refusal.log").write_text(refusal)
        if (
            rejected.returncode == 0
            or "no API of that release has recorded its start" not in refusal
        ):
            raise RuntimeError(
                "Direct upgrade did not refuse at the expected authority-protocol gate"
            )
        self.sql("DROP DATABASE direct_upgrade", "postgres")
        self.phase("Start the real bridge API and preserve recovered durable dispatch")
        self.start_api(BRIDGE)
        if (
            self.sql("SELECT count(*) FROM authz_protocol_state WHERE protocol=1")
            != "1"
        ):
            raise RuntimeError("Bridge API did not record protocol 1")
        self.wait(
            lambda: (
                self.sql(
                    "SELECT count(*) FROM system_configs WHERE category='runtime' AND key='legacy_broker_drained'"
                )
                == "1"
            ),
            "canonical bridge broker drain",
            timeout=240,
        )
        self.report["bridge_revision"] = self.sql(
            "SELECT version_num FROM alembic_version"
        )
        self.dc("stop", "api")
        self.dc("rm", "-f", "api")
        self.phase("Upgrade to target; prove dispatch lineage before worker admission")
        self.start_api(self.args.target_api_image)
        dispatch = self.sql(
            f"SELECT row_to_json(proof) FROM (SELECT e.id, e.executed_by, e.run_user_id, e.started_by_user_id, e.root_execution_id, e.status, d.id AS dispatch_id FROM executions e JOIN work_dispatches d ON d.subject_id=e.id WHERE e.id='{execution_id}') proof"
        )
        row = json.loads(dispatch)
        if not (
            row["executed_by"]
            and row["executed_by"] == row["run_user_id"] == row["started_by_user_id"]
            and row["root_execution_id"] == execution_id
        ):
            raise RuntimeError("Recovered execution lineage is incomplete")
        self.report["recovered_dispatch"] = row
        self.report["target_revision"] = self.sql(
            "SELECT version_num FROM alembic_version"
        )
        self.phase("Start one target worker and verify terminal success")
        self.dc("up", "-d", "--no-deps", "worker")

        def terminal():
            status = self.sql(
                f"SELECT status FROM executions WHERE id='{execution_id}'"
            )
            if status in ("Failed", "Cancelled", "Timeout"):
                raise RuntimeError(f"Recovered execution ended as {status}")
            return status == "Success"

        self.wait(terminal, "recovered execution success", timeout=180)
        self.dc("stop", "worker")
        self.report["terminal_execution"] = json.loads(
            self.sql(
                f"SELECT row_to_json(proof) FROM (SELECT id,status,result,completed_at FROM executions WHERE id='{execution_id}') proof"
            )
        )
        logs = self.sql(
            f"SELECT count(*) FROM execution_logs WHERE execution_id='{execution_id}' AND message LIKE '%STABLE_2_REHEARSAL_EXECUTED_ONCE%'"
        )
        self.report["execution_marker_count"] = int(logs)
        self.save()
        if logs != "1":
            raise RuntimeError("Execution marker did not occur exactly once")
        self.report["final_queues"] = self.queues()
        if any(
            q["messages"] or q["messages_unacknowledged"]
            for q in self.report["final_queues"]
        ):
            raise RuntimeError("Unexpected messages remain in broker queues")
        if (
            self.sql(
                f"SELECT count(*) FROM work_dispatches WHERE subject_id='{execution_id}'"
            )
            != "0"
        ):
            raise RuntimeError("Recovered execution still has unclaimed dispatch")
        self.report["passed"] = True
        self.phase("Rehearsal assertions passed")

    def cleanup(self) -> None:
        try:
            if self.owns_project:
                self.report["cleanup_completed"] = False
                self.dc("down", "--volumes", "--remove-orphans")
                self.report["cleanup_completed"] = True
        except BaseException:
            self.report["passed"] = False
            raise
        finally:
            self.save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target-api-image", required=True)
    parser.add_argument("--target-source", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", args.run_id):
        parser.error("run-id must be 1-41 lowercase letters/digits/dashes")
    if not re.fullmatch(
        r"ghcr.io/gobifrost/bifrost-api@sha256:[0-9a-f]{64}", args.target_api_image
    ):
        parser.error("target must be an immutable Bifrost API digest")
    if not re.fullmatch(r"[0-9a-f]{40}", args.target_source):
        parser.error("target-source must be a full commit SHA")
    if (
        not args.evidence_dir.is_absolute()
        or not args.evidence_dir.is_dir()
        or args.evidence_dir.resolve().is_relative_to(ROOT)
    ):
        parser.error(
            "evidence-dir must be an existing absolute directory outside the repository"
        )
    with tempfile.TemporaryDirectory(prefix="bifrost-upgrade-private-") as private:
        rehearsal = Rehearsal(args, private)

        def terminate(_signum, _frame):
            raise SystemExit("Rehearsal interrupted")

        signal.signal(signal.SIGTERM, terminate)
        signal.signal(signal.SIGHUP, terminate)
        try:
            rehearsal.execute()
        except BaseException as error:
            rehearsal.report["error"] = str(error)
            raise
        finally:
            rehearsal.cleanup()


if __name__ == "__main__":
    main()
