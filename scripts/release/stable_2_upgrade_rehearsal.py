#!/usr/bin/env python3
"""Qualify a direct 1.4.1-to-target release-image upgrade.

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
import time
from uuid import UUID


SOURCE = "ghcr.io/gobifrost/bifrost-api@sha256:ff8c8aee94c594678f9fe7254e297bad9e1f04c04f9029f8c1e6a66a8d7521c0"
ROOT = Path(__file__).resolve().parents[2]

class Rehearsal:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.project = "bifrost-upgrade-" + args.run_id
        self.evidence = args.evidence_dir / args.run_id
        self.evidence.mkdir(mode=0o700)
        self.password = secrets.token_hex(24)
        self.secret = secrets.token_hex(32)
        self.environment = dict(
            os.environ,
            REHEARSAL_IMAGE=SOURCE,
            REHEARSAL_DATABASE="bifrost",
            REHEARSAL_OLD_WORKER_IMAGE=SOURCE,
            REHEARSAL_CLIENT_IMAGE=args.target_client_image,
            REHEARSAL_PASSWORD=self.password,
            REHEARSAL_SECRET=self.secret,
        )
        self.compose = [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--env-file",
            os.devnull,
            "-f",
            str(Path(__file__).with_name("stable_2_upgrade_compose.yml")),
        ]
        self.report: dict = {
            "source": SOURCE,
            "target": args.target_api_image,
            "target_source": args.target_source,
            "target_client": args.target_client_image,
            "browser_runner": args.browser_runner_image,
            "phases": [],
            "passed": False,
        }
        self.owns_project = False
        self.population_payload: dict = {}

    def run(
        self,
        command: list[str],
        *,
        stdin: str | None = None,
        check: bool = True,
        timeout: int = 600,
        private_stdout: bool = False,
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
                (("[private helper stdout omitted]\n" if private_stdout else result.stdout) + result.stderr)
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

    def population(self, phase: str, *, helper_phase: str | None = None) -> None:
        """Run HTTP assertions in the selected image without recording credentials."""
        helper_phase = helper_phase or phase
        helper = Path(__file__).with_name("stable_2_upgrade_population.py").read_text()
        result = self.dc(
            "exec", "-T", "api", "python", "-c", helper, helper_phase,
            stdin=json.dumps(self.population_payload),
            private_stdout=True,
        )
        payload = json.loads(result.stdout)
        if helper_phase == "seed":
            self.population_payload = payload
            self.report["seeded_entities"] = payload["state"]
        self.report.setdefault("population", {})[phase] = payload["evidence"]
        self.save()

    def browser(self) -> None:
        self.dc("up", "-d", "--no-deps", "client")
        client_id = self.dc("ps", "-q", "client").stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{64}", client_id):
            raise RuntimeError("Expected one isolated production client container")
        helper = Path(__file__).with_name("stable_2_upgrade_population.py").read_text()
        bootstrap = self.dc(
            "exec", "-T", "api", "python", "-c", helper, "browser-bootstrap",
            stdin=json.dumps(self.population_payload),
            private_stdout=True,
        ).stdout
        # Keep private bootstrap IPC out of argv, evidence, logs and disk. The
        # browser helper emits safe phase diagnostics, never request details.
        result = self.run([
            "docker", "run", "--rm", "-i", "--network", f"container:{client_id}",
            "--entrypoint", "node", self.args.browser_runner_image, "--input-type=module", "-e",
            Path(__file__).with_name("stable_2_upgrade_browser.mjs").read_text(),
        ], stdin=bootstrap, timeout=240, private_stdout=True)
        self.record_browser_proof(json.loads(result.stdout))
        self.save()
        self.population("verify-app")

    def record_browser_proof(self, proof: dict) -> None:
        assertions = (
            "passed", "initial_sdk2_query_token_verified", "expiry_recovered",
            "epoch_recovered", "local_state_preserved", "revoked_family_refused",
        )
        if not all(proof.get(key) is True for key in assertions):
            raise RuntimeError("Original App browser qualification omitted a required assertion")
        counters = {"browser_refreshes": 2, "table_subscription_acks": 3}
        if any(type(proof.get(key)) is not int or proof[key] < minimum for key, minimum in counters.items()) or proof.get("page_errors") != 0:
            raise RuntimeError("Original App browser qualification has insufficient observations")
        # Only the reviewed, typed, non-secret proof fields enter evidence.
        self.report["original_app_browser"] = {key: proof[key] for key in (*assertions, *counters, "page_errors")}

    def enqueue_execution(self, name: str, code: str) -> str:
        """Submit a durable source-API execution without emitting its credentials."""
        script = f"""
import json, os, urllib.parse, urllib.request
def request(path, data, token=None):
    headers = {{"Content-Type": "application/json" if token else "application/x-www-form-urlencoded"}}
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request("http://localhost:8000" + path, data=data, headers=headers), timeout=30) as response:
        return json.load(response)
auth = request("/auth/login", urllib.parse.urlencode({{"username": os.environ["BIFROST_DEFAULT_USER_EMAIL"], "password": os.environ["BIFROST_DEFAULT_USER_PASSWORD"]}}).encode())
result = request("/api/workflows/execute", json.dumps({{"code": {code!r}, "script_name": {name!r}, "transient": False, "sync": False}}).encode(), auth["access_token"])
print(json.dumps({{"execution_id": result["execution_id"], "status": result["status"]}}))
"""
        payload = json.loads(
            self.dc("exec", "-T", "api", "python", "-c", script).stdout
        )
        return str(UUID(payload["execution_id"]))

    def old_worker_probe_code(self) -> str:
        state = self.population_payload["state"]
        key = state["configs"]["a_key"]
        scope = str(UUID(state["organizations"]["a"]))
        # Derive the comparison in the private controller. The seed helper
        # returns only the config key, never a credential-derived proof.
        fixture_value = hashlib.pbkdf2_hmac(
            "sha256", self.password.encode(), b"upgrade-secret:config-a", 600_000
        ).hex()
        expected = hashlib.sha256(fixture_value.encode()).hexdigest()
        return f'''import asyncio
import hashlib
import logging
from pathlib import Path
from bifrost import config

logging.info("STABLE_2_OLD_WORKER_BARRIER_REACHED")
Path("/tmp/stable2-upgrade-reached").touch()
for _ in range(180):
    if Path("/tmp/stable2-upgrade-release").exists():
        break
    await asyncio.sleep(1)
else:
    raise RuntimeError("stable upgrade old worker barrier did not release")
secret = await config.get("{key}", scope="{scope}")
secret_ok = hashlib.sha256(secret.encode()).hexdigest() == "{expected}"
if not secret_ok:
    raise RuntimeError("old worker could not read preserved synthetic secret")
logging.info("STABLE_2_OLD_WORKER_TARGET_CONFIG_SUCCESS")
result = {{"old_worker_target_secret_ok": secret_ok}}
'''

    def execution_marker_count(self, execution_id: str, marker: str) -> int:
        return int(self.sql(
            f"SELECT count(*) FROM execution_logs WHERE execution_id='{execution_id}' AND message LIKE '%{marker}%'"
        ))

    def old_worker_at_barrier(self, execution_id: str) -> bool:
        # Stable 1.4.1 flushes execution logs to PostgreSQL only at completion.
        # A file in this isolated source container observes the running child
        # without waiting on the very completion that the barrier prevents.
        status = self.sql(f"SELECT status FROM executions WHERE id='{execution_id}'")
        if status in ("Failed", "Cancelled", "Timeout", "Success"):
            self.report["old_worker_barrier_terminal_status"] = status
            self.save()
            raise RuntimeError(f"Source worker ended before release as {status}")
        return self.dc(
            "exec", "-T", "old-worker", "test", "-f", "/tmp/stable2-upgrade-reached",
            check=False,
        ).returncode == 0

    def assert_project_unused(self) -> None:
        label = f"label=com.docker.compose.project={self.project}"
        for inventory in (["ps", "-aq"], ["network", "ls", "-q"], ["volume", "ls", "-q"]):
            existing = self.run(["docker", *inventory, "--filter", label]).stdout
            if existing.strip():
                raise RuntimeError("Refusing to reuse an existing Compose project")

    def secret_record_fingerprints(self) -> list[dict]:
        """Compare seeded rows without retrieving plaintext or ciphertext values."""
        state = self.population_payload["state"]
        integration_ids = [str(UUID(state["integrations"][key])) for key in ("a", "b")]
        records = json.loads(self.sql(
            "SELECT coalesce(json_agg(proof ORDER BY id), '[]') FROM ("
            "SELECT id, key, organization_id, integration_id, config_schema_id, "
            "md5(value::text) AS stored_value_checksum, "
            "length(value->>'value') > 64 AS longer_than_fixture_plaintext "
            "FROM configs WHERE config_type='secret' AND ("
            "key IN ('stable2_upgrade_org_a', 'stable2_upgrade_org_b') OR "
            f"integration_id IN ('{integration_ids[0]}','{integration_ids[1]}'))"
            ") proof"
        ))
        if len(records) != 4 or not all(r["longer_than_fixture_plaintext"] for r in records):
            raise RuntimeError("Expected four non-plaintext seeded secret records")
        return records

    def rehearse_pending_recovery(self) -> None:
        """Run legacy queued-dispatch recovery in a second isolated source DB.

        PostgreSQL cannot clone a database while the source worker holds its
        active connections.  A fresh disposable Compose project keeps this
        recovery qualification independent from the concurrent-worker probe.
        """
        self.cleanup()
        self.owns_project = False
        self.project += "-recovery"
        self.compose[3] = self.project
        self.password = secrets.token_hex(24)
        self.secret = secrets.token_hex(32)
        self.environment = dict(
            os.environ,
            REHEARSAL_IMAGE=SOURCE,
            REHEARSAL_OLD_WORKER_IMAGE=SOURCE,
            REHEARSAL_CLIENT_IMAGE=self.args.target_client_image,
            REHEARSAL_DATABASE="bifrost",
            REHEARSAL_PASSWORD=self.password,
            REHEARSAL_SECRET=self.secret,
        )
        self.assert_project_unused()
        self.owns_project = True
        self.dc("up", "-d", "--wait", "postgres", "redis", "rabbit", "seaweedfs")
        self.start_api(SOURCE)
        execution_id = self.enqueue_execution(
            "stable_2_upgrade_pending_recovery",
            "import logging\nlogging.info('STABLE_2_PENDING_RECOVERY_EXECUTED_ONCE')\nresult = {'pending_recovery': True}\n",
        )
        source_queues = self.queues()
        if sum(q["messages"] for q in source_queues if q["name"] == "workflow-executions") != 1:
            raise RuntimeError("Source API did not leave exactly one Rabbit workflow message")
        if self.dc(
            "exec", "-T", "redis", "redis-cli", "EXISTS", f"bifrost:exec:{execution_id}:pending"
        ).stdout.strip() != "1":
            raise RuntimeError("Source API did not retain its pending Redis context")
        self.dc("stop", "api")
        self.dc("rm", "-f", "api")
        self.start_api(self.args.target_api_image)
        self.wait(
            lambda: self.sql(
                f"SELECT count(*) FROM work_dispatches WHERE subject_id='{execution_id}'"
            ) == "1",
            "recovered durable workflow dispatch",
        )
        dispatch = json.loads(self.sql(
            f"SELECT row_to_json(proof) FROM (SELECT e.id, e.executed_by, e.run_user_id, e.started_by_user_id, e.root_execution_id, e.status, d.id AS dispatch_id FROM executions e JOIN work_dispatches d ON d.subject_id=e.id WHERE e.id='{execution_id}') proof"
        ))
        if not (
            dispatch["executed_by"]
            and dispatch["executed_by"] == dispatch["run_user_id"] == dispatch["started_by_user_id"]
            and dispatch["root_execution_id"] == execution_id
        ):
            raise RuntimeError("Recovered source dispatch lineage is incomplete")
        self.dc("up", "-d", "--no-deps", "worker")

        def terminal() -> bool:
            status = self.sql(f"SELECT status FROM executions WHERE id='{execution_id}'")
            if status in ("Failed", "Cancelled", "Timeout"):
                raise RuntimeError(f"Recovered source dispatch ended as {status}")
            return status == "Success"

        self.wait(terminal, "recovered source dispatch success", timeout=180)
        terminal_execution = json.loads(self.sql(
            f"SELECT row_to_json(proof) FROM (SELECT id,status,result,completed_at FROM executions WHERE id='{execution_id}') proof"
        ))
        marker_count = self.execution_marker_count(execution_id, "STABLE_2_PENDING_RECOVERY_EXECUTED_ONCE")
        if terminal_execution["result"].get("pending_recovery") is not True or marker_count != 1:
            raise RuntimeError("Recovered source dispatch did not complete exactly once")
        if self.sql(f"SELECT count(*) FROM work_dispatches WHERE subject_id='{execution_id}'") != "0":
            raise RuntimeError("Recovered source dispatch still has an unclaimed work dispatch")
        final_queues = self.queues()
        if any(queue["messages"] or queue["messages_unacknowledged"] for queue in final_queues):
            raise RuntimeError("Unexpected messages remain after pending recovery")
        self.report["pending_recovery"] = {
            "execution_id": execution_id,
            "source_queues": source_queues,
            "recovered_dispatch": dispatch,
            "terminal_execution": terminal_execution,
            "marker_count": marker_count,
            "final_queues": final_queues,
        }
        self.dc("stop", "worker")
        self.dc("rm", "-f", "worker")

    def execute(self) -> None:
        for name in ("stable_2_upgrade_population.py", "stable_2_upgrade_browser.mjs", "stable_2_upgrade_compose.yml"):
            if not Path(__file__).with_name(name).is_file():
                raise RuntimeError(f"Missing qualification helper: {name}")
        self.assert_project_unused()
        self.owns_project = True
        self.phase("Verify immutable images and target source")
        self.report["runtime_versions"] = {}
        for image in (SOURCE, self.args.target_api_image):
            self.run(["docker", "pull", image])
            version = self.run([
                "docker", "run", "--rm", "--entrypoint", "python", image,
                "-c", "from shared.version import get_version; print(get_version())",
            ]).stdout.strip()
            self.report["runtime_versions"][image] = version
        if self.report["runtime_versions"][SOURCE] != "1.4.1":
            raise RuntimeError("Source artifact is not Bifrost 1.4.1")
        witnesses = [
            "api/src/main.py",
            "api/src/services/legacy_broker_drain.py",
            "api/alembic/versions/20261031_authz_claims_gate.py",
            "api/src/jobs/platform/application_sdk_update.py",
            "api/src/services/application_source_artifact.py",
            "api/src/services/solutions/app_build.py",
            "api/src/services/authorization/runtime.py",
            "api/src/services/authorization/runtime_auth.py",
            "api/src/jobs/consumers/workflow_execution.py",
            "api/shared/run_lineage.py",
            "api/alembic/versions/20261107_running_run_lineage.py",
            "api/src/core/auth.py",
            "api/src/routers/websocket.py",
            "api/src/services/websocket_auth_recovery.py",
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
        self.phase("Boot actual 1.4.1 and seed a representative installation")
        self.dc("up", "-d", "--wait", "postgres", "redis", "rabbit", "seaweedfs")
        self.start_api(SOURCE)
        self.dc("up", "-d", "--no-deps", "worker", "scheduler")
        self.population("seed")
        self.report["seed_secret_record_fingerprints"] = self.secret_record_fingerprints()
        self.dc("stop", "worker", "scheduler")
        self.dc("rm", "-f", "worker", "scheduler")
        self.phase("Start a real 1.4.1 worker and hold a durable SDK execution")
        self.dc("up", "-d", "--no-deps", "old-worker")
        active_execution_id = self.enqueue_execution(
            "stable_2_upgrade_old_worker",
            self.old_worker_probe_code(),
        )
        self.report["active_old_worker_execution_id"] = active_execution_id
        self.save()
        self.wait(
            lambda: self.old_worker_at_barrier(active_execution_id),
            "source worker barrier",
            timeout=120,
        )
        self.report["source_revision"] = self.sql(
            "SELECT version_num FROM alembic_version"
        )
        self.dc("stop", "api")
        self.dc("rm", "-f", "api")
        self.phase("Replace the API directly and release the source worker")
        self.start_api(self.args.target_api_image)
        if self.secret_record_fingerprints() != self.report["seed_secret_record_fingerprints"]:
            raise RuntimeError("Seeded secret rows or stored-value fingerprints changed")
        self.report["secret_record_fingerprints_preserved"] = True
        self.report["target_revision"] = self.sql(
            "SELECT version_num FROM alembic_version"
        )
        protocol_counts = json.loads(self.sql(
            "SELECT json_build_object('protocol_1', count(*) FILTER (WHERE protocol=1), 'protocol_2', count(*) FILTER (WHERE protocol=2)) FROM authz_protocol_state"
        ))
        if protocol_counts != {"protocol_1": 0, "protocol_2": 1}:
            raise RuntimeError("Direct upgrade recorded an unexpected authorization protocol state")
        self.report["target_authorization_protocols"] = protocol_counts
        self.dc("exec", "-T", "old-worker", "touch", "/tmp/stable2-upgrade-release")

        def old_worker_terminal():
            status = self.sql(
                f"SELECT status FROM executions WHERE id='{active_execution_id}'"
            )
            if status in ("Failed", "Cancelled", "Timeout"):
                raise RuntimeError(f"Source worker execution ended as {status}")
            return status == "Success"

        self.wait(old_worker_terminal, "source worker target-API success", timeout=180)
        old_worker_terminal = json.loads(
            self.sql(
                f"SELECT row_to_json(proof) FROM (SELECT id,status,result,completed_at FROM executions WHERE id='{active_execution_id}') proof"
            )
        )
        if old_worker_terminal["result"].get("old_worker_target_secret_ok") is not True:
            raise RuntimeError("Source worker did not read the target API secret")
        marker_count = self.execution_marker_count(
            active_execution_id, "STABLE_2_OLD_WORKER_TARGET_CONFIG_SUCCESS"
        )
        self.report["active_old_worker"] = {
            "terminal_execution": old_worker_terminal,
            "target_config_marker_count": marker_count,
        }
        self.save()
        if marker_count != 1:
            raise RuntimeError("Source worker target API marker did not occur exactly once")
        self.dc("stop", "old-worker")
        self.dc("rm", "-f", "old-worker")
        self.phase("Verify populated data and its active legacy App without rebuilding")
        self.dc("up", "-d", "--no-deps", "worker", "scheduler")
        self.population("verify")
        self.phase("Exercise the original App through expiry, epoch change and revocation")
        self.browser()
        self.phase("Qualify isolated pending source dispatch recovery")
        self.rehearse_pending_recovery()
        self.report["passed"] = True
        self.phase("Rehearsal assertions passed")

    def cleanup(self) -> None:
        try:
            if self.owns_project:
                self.report["cleanup_completed"] = False
                self.report.setdefault("project_cleanup", {})[self.project] = False
                self.dc("down", "--volumes", "--remove-orphans")
                self.report["project_cleanup"][self.project] = True
                self.report["cleanup_completed"] = True
                self.owns_project = False
        except BaseException:
            self.report["passed"] = False
            raise
        finally:
            self.save()

    def capture_failure_diagnostics(self) -> None:
        if not self.owns_project:
            return
        result = self.dc("logs", "--no-color", "--tail", "120", "api", "worker", "old-worker", "client", check=False)
        detail = result.stdout + result.stderr
        detail = detail.replace(self.password, "[redacted]").replace(self.secret, "[redacted]")
        detail = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted-jwt]", detail)
        detail = re.sub(r"([?&]token=)[^&\s\"']+", r"\1[redacted]", detail)
        (self.evidence / "service-diagnostics.log").write_text(detail)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target-api-image", required=True)
    parser.add_argument("--target-client-image", required=True)
    parser.add_argument("--browser-runner-image", required=True)
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
    if not re.fullmatch(r"ghcr.io/gobifrost/bifrost-client@sha256:[0-9a-f]{64}", args.target_client_image):
        parser.error("client target must be an immutable Bifrost client digest")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.browser_runner_image):
        parser.error("browser runner must be an immutable local image ID")
    if (
        not args.evidence_dir.is_absolute()
        or not args.evidence_dir.is_dir()
        or args.evidence_dir.resolve().is_relative_to(ROOT)
    ):
        parser.error(
            "evidence-dir must be an existing absolute directory outside the repository"
        )
    rehearsal = Rehearsal(args)

    def terminate(_signum, _frame):
        raise SystemExit("Rehearsal interrupted")

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGHUP, terminate)
    try:
        rehearsal.execute()
    except BaseException as error:
        rehearsal.report["error"] = str(error)
        try:
            rehearsal.capture_failure_diagnostics()
        except Exception as diagnostic_error:
            rehearsal.report["diagnostics_error"] = type(diagnostic_error).__name__
        raise
    finally:
        rehearsal.cleanup()


if __name__ == "__main__":
    main()
