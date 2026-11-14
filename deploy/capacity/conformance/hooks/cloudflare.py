"""Cloudflare Container hook for the capacity conformance suite.

The suite cannot see physical launches over the capacity protocol, so it asks a
hook. On Cloudflare that means asking the Containers API which instances exist
for the application.

Supported operations
    observe   -> {"launches": [<instance id>...], "running": <int>}
    restart-provider, stop-process, inject-fault
              -> unsupported on Cloudflare

Why the last three are unsupported: a deployed Worker cannot be restarted from
inside itself, and container instances are destroyed through the provider's own
Durable Object. Claiming support would let the suite report a pass for a case it
never exercised, so these return an error and the suite records SKIP.

Environment
    CLOUDFLARE_API_TOKEN   bearer token with Workers Containers Read
    CF_ACCOUNT_ID          account id
    CF_CONTAINER_APP       container application id (see `wrangler containers list`)
    CF_HOOK_STATE          optional path for the cumulative launch log
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
STATE_PATH = os.environ.get("CF_HOOK_STATE", "/tmp/cf-capacity-launches.json")


class Unsupported(Exception):
    pass


def _get(path: str) -> dict:
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise Unsupported("CLOUDFLARE_API_TOKEN is not set")
    request = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise Unsupported(f"Cloudflare API {exc.code}") from None
    if not body.get("success", True):
        raise Unsupported("Cloudflare API returned success=false")
    return body


def _instances() -> list[dict]:
    account = os.environ.get("CF_ACCOUNT_ID")
    app = os.environ.get("CF_CONTAINER_APP")
    if not account or not app:
        raise Unsupported("CF_ACCOUNT_ID and CF_CONTAINER_APP are required")
    body = _get(f"/accounts/{account}/containers/applications/{app}/instances")
    return body.get("result") or []


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    tmp = f"{STATE_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, STATE_PATH)


def observe(payload: dict) -> dict:
    """Cumulative launch identities for one worker.

    Cloudflare names each instance after the worker UUID, so the mapping is
    direct. Identities accumulate across calls: an instance that has since
    stopped still counts, which is what makes a sequential duplicate launch
    detectable.
    """
    worker_id = payload.get("worker_id", "")
    instances = _instances()
    state = _load_state()
    known = set(state.get(worker_id, []))
    running = 0
    for instance in instances:
        if instance.get("name") != worker_id:
            continue
        if instance.get("state") == "running":
            running += 1
        instance_id = instance.get("id")
        if instance_id:
            known.add(instance_id)
    state[worker_id] = sorted(known)
    _save_state(state)
    return {"launches": state[worker_id], "running": running}


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: cloudflare.py <op>"}), file=sys.stderr)
        return 2
    op = sys.argv[1]
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}

    try:
        if op == "observe":
            print(json.dumps(observe(payload)))
            return 0
        if op in ("restart-provider", "stop-process", "inject-fault", "clear-fault"):
            raise Unsupported(f"{op} is not available on Cloudflare")
    except Unsupported as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 3

    print(json.dumps({"error": f"unknown op {op}"}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
