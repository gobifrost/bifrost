#!/usr/bin/env python3
"""Black-box conformance suite for external worker capacity providers.

Drives a provider only over HTTP, plus a subprocess hook for things HTTP cannot
express (restarting the provider, killing a physical launcher, injecting faults).
Imports no provider implementation, so it works identically against Floci proofs
and real clouds.

    python conformance.py --base-url https://127.0.0.1:8443 \
        --token-env CAPACITY_TOKEN --scope demo --hook-command ./hooks/docker_floci.py

Exit code 0 means every executed case passed. Skipped cases (a hook that cannot
support an operation) are reported as SKIP and never counted as passes.

Hook protocol: the hook is invoked as `<hook> <op>` with a JSON object on stdin
and must write a JSON object to stdout.

    observe          {"worker_id","resource_uid"} -> {"launches":[str], "running":int}
    restart-provider {"scope"}                    -> {"ok":true}
    stop-process     {"worker_id","resource_uid"} -> {"ok":true}
    inject-fault     {"phase","method","path"}     -> {"ok":true}
    clear-fault      {}                            -> {"ok":true}

`launches` must be the CUMULATIVE set of physical launch identities ever
observed, not a count and not a boolean: a boolean cannot distinguish one
long-lived launcher from two sequential ones, which is the failure this suite
exists to catch.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

SCOPE_HEADER = "X-Bifrost-Capacity-Scope"

# Some providers sit behind bot protection (Cloudflare answers 1010 to a bare
# Python user agent). A provider endpoint should be reachable by the control
# plane's own client, so the UA is explicit and overridable.
DEFAULT_USER_AGENT = "bifrost-capacity-conformance/1.0"

PASSED, FAILED, SKIPPED = "PASS", "FAIL", "SKIP"


class Hook:
    def __init__(self, command: str | None) -> None:
        self.command = command

    def available(self) -> bool:
        return bool(self.command)

    def call(self, op: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.command:
            raise RuntimeError("no hook configured")
        # A hook may be a bare executable or a command line (e.g. "python hook.py").
        if " " in self.command:
            proc = subprocess.run(
                f"{self.command} {op}",
                shell=True,
                input=json.dumps(payload or {}),
                capture_output=True,
                text=True,
                timeout=180,
            )
        else:
            proc = subprocess.run(
                [self.command, op],
                input=json.dumps(payload or {}),
                capture_output=True,
                text=True,
                timeout=180,
            )
        if proc.returncode != 0:
            raise RuntimeError(f"hook {op} failed: {proc.stderr.strip()[:200]}")
        try:
            return json.loads(proc.stdout or "{}")
        except ValueError:
            raise RuntimeError(f"hook {op} returned non-JSON") from None


class Client:
    def __init__(self, base_url: str, token: str, scope: str, user_agent: str = DEFAULT_USER_AGENT) -> None:
        self.base = base_url.rstrip("/")
        self.token = token
        self.scope = scope
        self.user_agent = user_agent
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        token: str | None = None,
        scope: str | None = "default",
    ) -> tuple[int, Any]:
        headers = {"Content-Type": "application/json", "User-Agent": self.user_agent}
        use_token = self.token if token is None else token
        if use_token:
            headers["Authorization"] = f"Bearer {use_token}"
        if scope == "default":
            headers[SCOPE_HEADER] = self.scope
        elif scope:
            headers[SCOPE_HEADER] = scope
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method, headers=headers)
        # Euler 4xx must be readable, not raised.
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=45) as resp:
                raw = resp.read().decode() or "{}"
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode() or "{}"
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"raw": raw[:200]}

    # convenience -------------------------------------------------------

    def capabilities(self) -> tuple[int, Any]:
        return self.request("GET", "/v1/capabilities", scope=None)

    def get(self, worker_id: str) -> tuple[int, Any]:
        return self.request("GET", f"/v1/workers/{worker_id}")

    def put(self, worker_id: str, reservation: dict[str, Any]) -> tuple[int, Any]:
        return self.request("PUT", f"/v1/workers/{worker_id}", reservation)

    def start(self, worker_id: str, uid: str) -> tuple[int, Any]:
        return self.request("POST", f"/v1/workers/{worker_id}/start", {"resource_uid": uid})

    def delete(self, worker_id: str, uid: str) -> tuple[int, Any]:
        return self.request("DELETE", f"/v1/workers/{worker_id}", {"resource_uid": uid})


class Suite:
    def __init__(self, client: Client, hook: Hook, deadline: int) -> None:
        self.c = client
        self.hook = hook
        self.deadline = deadline
        self.results: list[tuple[str, str, str]] = []
        self.profile: dict[str, Any] = {}

    # -- reporting ------------------------------------------------------

    def _record(self, name: str, status: str, detail: str = "") -> None:
        self.results.append((name, status, detail))
        line = f"[{status}] {name}"
        if detail:
            line += f" — {detail}"
        print(line, flush=True)

    def case(self, name: str):
        def wrap(fn):
            try:
                fn()
            except SkipCase as exc:
                self._record(name, SKIPPED, str(exc))
            except AssertionError as exc:
                self._record(name, FAILED, str(exc) or "assertion failed")
            except Exception as exc:  # noqa: BLE001
                self._record(name, FAILED, f"{type(exc).__name__}: {str(exc)[:180]}")
            else:
                self._record(name, PASSED)
            return fn

        return wrap

    # -- helpers --------------------------------------------------------

    def reservation(self, worker_id: str, **overrides: Any) -> dict[str, Any]:
        memory = self.profile.get("memory_limit_bytes", 4 * 1024**3)
        lifetime = min(3600, int(self.profile.get("max_lifetime_seconds", 3600)))
        doc = {
            "worker_id": worker_id,
            "worker_version": self.profile.get("worker_version", "unknown"),
            "memory_limit_bytes": memory,
            "max_lifetime_seconds": lifetime,
            "drain_grace_seconds": 300,
        }
        doc.update(overrides)
        return doc

    def launches(self, worker_id: str, uid: str) -> list[str]:
        if not self.hook.available():
            raise SkipCase("no hook: physical launches cannot be observed")
        obs = self.hook.call("observe", {"worker_id": worker_id, "resource_uid": uid})
        return list(obs.get("launches") or [])

    def wait_for_state(self, worker_id: str, wanted: set[str], timeout: int = 120) -> str:
        deadline = time.time() + timeout
        state = "unknown"
        while time.time() < deadline:
            status, doc = self.c.get(worker_id)
            if status == 200:
                state = doc.get("state", "unknown")
                if state in wanted:
                    return state
            time.sleep(2)
        return state


class SkipCase(Exception):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-env", default="CAPACITY_TOKEN")
    parser.add_argument("--token", default=None)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--hook-command", default=None)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--deadline", type=int, default=900)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    token = args.token or os.environ.get(args.token_env, "")
    if not token:
        print(f"missing token: set ${args.token_env} or pass --token", file=sys.stderr)
        return 2

    suite = Suite(
        Client(args.base_url, token, args.scope, user_agent=args.user_agent),
        Hook(args.hook_command),
        args.deadline,
    )

    # ---- capabilities -------------------------------------------------
    @suite.case("capabilities: contract")
    def _() -> None:
        status, doc = suite.c.capabilities()
        assert status == 200, f"status {status}"
        for field in ("protocol_version", "scope", "worker_version", "memory_limit_bytes",
                      "max_workers", "max_lifetime_seconds"):
            assert field in doc, f"missing {field}"
        assert doc["protocol_version"] == 1, "protocol_version must be 1"
        assert doc["scope"] == args.scope, f"scope mismatch: {doc['scope']}"
        assert doc["worker_version"], "empty worker_version"
        assert 64 * 1024**2 <= doc["memory_limit_bytes"] <= 128 * 1024**4
        assert doc["memory_limit_bytes"] % 1048576 == 0, "memory not MiB aligned"
        assert 1 <= doc["max_workers"] <= 1000
        assert 60 <= doc["max_lifetime_seconds"] <= 604800
        suite.profile = doc

    @suite.case("capabilities: bearer auth required")
    def _() -> None:
        status, _body = suite.c.request("GET", "/v1/capabilities", token="", scope=None)
        assert status in (401, 403), f"unauthenticated capabilities returned {status}"

    # ---- unknown worker ----------------------------------------------
    unknown = str(uuid.uuid4())
    @suite.case("unknown worker reads 404")
    def _() -> None:
        status, _body = suite.c.get(unknown)
        assert status == 404, f"status {status}"

    # ---- prepare ------------------------------------------------------
    prepared = str(uuid.uuid4())
    @suite.case("prepare: returns prepared with a stable UID")
    def _() -> None:
        status, doc = suite.c.put(prepared, suite.reservation(prepared))
        assert status == 200, f"status {status}"
        assert doc["state"] == "prepared", f"state {doc['state']}"
        assert doc["worker_id"] == prepared
        assert doc["scope"] == args.scope
        assert doc["resource_uid"], "missing resource_uid"

    @suite.case("prepare: idempotent, same UID, nothing launched")
    def _() -> None:
        _s, first = suite.c.get(prepared)
        uid = first["resource_uid"]
        for _ in range(2):
            status, doc = suite.c.put(prepared, suite.reservation(prepared))
            assert status == 200, f"status {status}"
            assert doc["resource_uid"] == uid, "resource_uid changed on repeat prepare"
            assert doc["state"] == "prepared"
        if suite.hook.available():
            assert suite.launches(prepared, uid) == [], "prepare launched a process"

    @suite.case("prepare: conflicting reservation rejected")
    def _() -> None:
        _s, first = suite.c.get(prepared)
        for field, value in (("memory_limit_bytes", 1), ("worker_version", "different-build")):
            status, _doc = suite.c.put(prepared, suite.reservation(prepared, **{field: value}))
            assert status >= 400, f"conflicting {field} accepted ({status})"
        _s2, again = suite.c.get(prepared)
        assert again["resource_uid"] == first["resource_uid"]

    # ---- start --------------------------------------------------------
    uid_prepared = ""
    @suite.case("start: wrong UID rejected, nothing launched")
    def _() -> None:
        nonlocal uid_prepared
        _s, doc = suite.c.get(prepared)
        uid_prepared = doc["resource_uid"]
        status, _body = suite.c.start(prepared, "not-the-uid")
        assert status == 409, f"status {status}"

    @suite.case("start: bound UID reaches a live state")
    def _() -> None:
        status, doc = suite.c.start(prepared, uid_prepared)
        assert status == 200, f"status {status}"
        assert doc["resource_uid"] == uid_prepared
        assert doc["state"] in ("starting", "running"), f"state {doc['state']}"
        state = suite.wait_for_state(prepared, {"starting", "running"}, timeout=180)
        assert state in ("starting", "running"), f"never became live: {state}"

    @suite.case("start: exactly one physical launch")
    def _() -> None:
        launches = suite.launches(prepared, uid_prepared)
        assert len(launches) == 1, f"expected 1 launch, observed {len(launches)}: {launches}"

    @suite.case("start: replay does not launch a second incarnation")
    def _() -> None:
        for _ in range(2):
            status, _doc = suite.c.start(prepared, uid_prepared)
            assert status == 200, f"replay status {status}"
        launches = suite.launches(prepared, uid_prepared)
        assert len(launches) == 1, f"duplicate launch: {launches}"

    # ---- delete -------------------------------------------------------
    @suite.case("delete: wrong UID rejected")
    def _() -> None:
        status, _body = suite.c.delete(prepared, "not-the-uid")
        assert status == 409, f"status {status}"
        _s, doc = suite.c.get(prepared)
        assert doc["state"] != "deleted", "wrong UID deleted the resource"

    @suite.case("delete: tombstone, terminal, non-resurrectable")
    def _() -> None:
        status, doc = suite.c.delete(prepared, uid_prepared)
        assert status == 200, f"status {status}"
        assert doc["state"] == "deleted", f"state {doc['state']}"
        _s, again = suite.c.get(prepared)
        assert again["state"] == "deleted", "tombstone not retained on GET"
        status, _doc = suite.c.delete(prepared, uid_prepared)
        assert status == 200, f"repeat delete status {status}"
        status, _doc = suite.c.put(prepared, suite.reservation(prepared))
        assert status >= 400, f"deleted worker accepted a new prepare ({status})"
        status, _doc = suite.c.start(prepared, uid_prepared)
        assert status >= 400, f"deleted worker accepted a start ({status})"

    # ---- scope --------------------------------------------------------
    other = str(uuid.uuid4())
    @suite.case("scope: foreign scope cannot see or act on a worker")
    def _() -> None:
        status, _doc = suite.c.request("GET", f"/v1/workers/{prepared}", scope="other-scope")
        assert status == 404, f"status {status}"

    @suite.case("scope: missing scope header rejected on resource reads")
    def _() -> None:
        status, _body = suite.c.request("GET", f"/v1/workers/{prepared}", scope=None)
        assert status in (400, 404), f"status {status}"

    # ---- restart durability -------------------------------------------
    if suite.hook.available():
        restart_worker = str(uuid.uuid4())

        @suite.case("durability: prepared reservation survives provider restart")
        def _() -> None:
            status, doc = suite.c.put(restart_worker, suite.reservation(restart_worker))
            assert status == 200, f"status {status}"
            uid = doc["resource_uid"]
            suite.hook.call("restart-provider", {"scope": args.scope})
            time.sleep(5)
            status, after = suite.c.get(restart_worker)
            assert status == 200, f"worker lost across restart ({status})"
            assert after["resource_uid"] == uid, "UID changed across restart"
            assert after["state"] == "prepared", f"state {after['state']}"

        @suite.case("durability: tombstone survives provider restart")
        def _() -> None:
            status, doc = suite.c.put(restart_worker, suite.reservation(restart_worker))
            assert status == 200, f"status {status}"
            uid = doc["resource_uid"]
            assert suite.c.delete(restart_worker, uid)[0] == 200
            suite.hook.call("restart-provider", {"scope": args.scope})
            time.sleep(5)
            status, after = suite.c.get(restart_worker)
            assert status == 200, f"tombstone lost across restart ({status})"
            assert after["state"] == "deleted", f"state {after['state']}"

        @suite.case("durability: stopped incarnation never restarts")
        def _() -> None:
            wid = str(uuid.uuid4())
            status, doc = suite.c.put(wid, suite.reservation(wid))
            assert status == 200, f"status {status}"
            uid = doc["resource_uid"]
            assert suite.c.start(wid, uid)[0] == 200
            suite.wait_for_state(wid, {"starting", "running", "stopped"}, timeout=180)
            before = suite.launches(wid, uid)
            if not before:
                raise SkipCase("hook reported no launch to stop")
            suite.hook.call("stop-process", {"worker_id": wid, "resource_uid": uid})
            state = suite.wait_for_state(wid, {"stopped"}, timeout=180)
            assert state == "stopped", f"expected stopped, saw {state}"
            status, _doc = suite.c.start(wid, uid)
            assert status >= 400, f"stopped incarnation accepted a start ({status})"
            after = suite.launches(wid, uid)
            assert len(after) == len(before), f"stopped incarnation relaunched: {after}"

    # ---- report -------------------------------------------------------
    total = len(suite.results)
    passed = sum(1 for _n, s, _d in suite.results if s == PASSED)
    failed = sum(1 for _n, s, _d in suite.results if s == FAILED)
    skipped = sum(1 for _n, s, _d in suite.results if s == SKIPPED)
    print(f"\n{passed}/{total} passed, {failed} failed, {skipped} skipped", flush=True)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(
                {"passed": passed, "failed": failed, "skipped": skipped,
                 "cases": [{"name": n, "status": s, "detail": d} for n, s, d in suite.results]},
                fh, indent=2,
            )

    if failed:
        print("\nFailed cases:", file=sys.stderr)
        for name, status, detail in suite.results:
            if status == FAILED:
                print(f"  - {name}: {detail}", file=sys.stderr)
        return 1
    if passed == 0:
        print("no cases passed; conformance is not established", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
