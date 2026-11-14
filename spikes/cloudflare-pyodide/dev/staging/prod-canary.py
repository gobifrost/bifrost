#!/usr/bin/env python3
"""Stage 3 shadow canary (runs on the production host, not in the app stack).

Runs ONE read-only production workflow twice: on the production Linux worker
(via the public API) and on the deployed Cloudflare per-tenant worker. Compares
status and result, then appends a hash-only record.

No customer data is stored: each result is reduced to a canonical sha256 and an
organization count. Mismatches keep only status/error summaries.

Kill switch: remove /opt/bifrost-canary/ENABLED, or stop the timer. The worker
also fails closed when SPIKE_EXPIRES_AT lapses or SPIKE_AUTH rotates.

Install (production):
  /opt/bifrost-canary/canary.py           this file, 0700
  /etc/bifrost-canary/last.json           device-flow session, 0600
  /etc/bifrost-canary/worker-secret       canary worker SPIKE_AUTH, 0600
  /opt/bifrost-canary/ENABLED             presence enables runs
  /opt/bifrost-canary/runs.jsonl          evidence, appended
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

API = "http://127.0.0.1:3000"
WORKER = "https://bifrost-pyodide-canary-ddf316c1.midtown-technology-group.workers.dev"
SESSION = "/etc/bifrost-canary/last.json"
WORKER_SECRET = "/etc/bifrost-canary/worker-secret"
OUT = "/opt/bifrost-canary/runs.jsonl"
ENABLED = "/opt/bifrost-canary/ENABLED"
WORKFLOW = "docs_list_organizations"
SOLUTION = "44832b44-87db-4837-afab-b22fc47f88db"
# The dispatcher passes the LOGICAL path plus a solution scope; the child maps
# it to `_solutions/<id>/functions/...` inside get_module_sync. Passing the
# already-scoped path doubles the prefix and resolves to nothing.
LOGICAL = "functions/catalog.py"
MODULE = f"_solutions/{SOLUTION}/{LOGICAL}"


def call(method, url, *, token=None, body=None, headers=None, timeout=60):
    data = None
    # Cloudflare's edge blocks the stock Python-urllib signature with 1010 on
    # workers.dev; any explicit UA passes, so this client identifies itself.
    hdrs = {"User-Agent": "bifrost-canary/1.0", **dict(headers or {})}
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else None)
        except Exception:
            return e.code, {"raw_len": len(raw)}


def load_session():
    with open(SESSION) as f:
        return json.load(f)


def save_session(s):
    tmp = SESSION + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(s, f)
    os.replace(tmp, SESSION)


def access_token():
    s = load_session()
    tok = s.get("access_token")
    if tok:
        status, _ = call("GET", f"{API}/auth/me", token=tok, timeout=30)
        if status == 200:
            return tok
    rt = s.get("refresh_token")
    if not rt:
        raise RuntimeError("no refresh token on the box")
    status, body = call("POST", f"{API}/auth/refresh", body={"refresh_token": rt}, timeout=30)
    if status != 200 or not (body or {}).get("access_token"):
        raise RuntimeError(f"refresh failed: {status}")
    s["access_token"] = body["access_token"]
    if body.get("refresh_token"):
        s["refresh_token"] = body["refresh_token"]
    save_session(s)
    return s["access_token"]


def canonical(value, *, sort_lists=False):
    if isinstance(value, list):
        items = [canonical(v, sort_lists=sort_lists) for v in value]
        if sort_lists:
            items.sort()
        return "[" + ",".join(items) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(k) + ":" + canonical(value[k], sort_lists=sort_lists) for k in sorted(value)
        ) + "}"
    return json.dumps(value)


def digest(value, *, sort_lists=False):
    return hashlib.sha256(canonical(value, sort_lists=sort_lists).encode()).hexdigest()


def run_linux(token, workflow_id):
    started = time.time()
    status, body = call("POST", f"{API}/api/workflows/execute", token=token,
                        body={"workflow_id": workflow_id, "sync": False}, timeout=60)
    if status != 200 or not (body or {}).get("execution_id"):
        return {"status": f"SubmitFailed({status})", "result": None,
                "ms": int((time.time() - started) * 1000)}
    exec_id = body["execution_id"]
    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(2)
        st, ex = call("GET", f"{API}/api/executions/{exec_id}", token=token, timeout=30)
        if st == 200 and ex and ex.get("status") not in ("Pending", "Running", None):
            return {"status": ex.get("status"), "result": ex.get("result"),
                    "error": ex.get("error") or ex.get("error_message"),
                    "ms": int((time.time() - started) * 1000)}
    return {"status": "Timeout", "result": None, "ms": int((time.time() - started) * 1000)}


def run_cloud(token, me, pin):
    started = time.time()
    org_id = me.get("organization_id")
    context = {
        "caller": {"user_id": me["id"], "email": me["email"],
                   "name": me.get("name") or me["email"]},
        "organization_id": org_id,
        "organization": ({"id": org_id, "name": "Provider", "is_active": True, "is_provider": True}
                         if org_id else None),
        "name": WORKFLOW,
        "function_name": WORKFLOW,
        "file_path": LOGICAL,
        "content_hash": pin,
        "solution_id": SOLUTION,
        "engine_token": token,
        "transient": True,
        "parameters": {},
    }
    body = {
        "tenant_id": "prod-canary",
        "action": "engine",
        "execution_id": str(uuid.uuid4()),
        "requirements": {"modules": [MODULE]},
        "manifest": {MODULE: pin},
        "context": context,
        "public_url": "https://bifrost.midtowntg.com",
        "egress": {"hosts": []},
    }
    with open(WORKER_SECRET) as f:
        secret = f.read().strip()
    status, payload = call("POST", WORKER, body=body,
                           headers={"X-Spike-Auth": secret}, timeout=300)
    out = (payload or {}).get("output") or {}
    return {
        "http": status,
        "status": out.get("status") or (payload or {}).get("phase"),
        "result": out.get("result"),
        "error": out.get("error_type") or out.get("error_message"),
        "reused": (payload or {}).get("worker_reused"),
        "queue_ms": (payload or {}).get("queue_wait_ms"),
        "ms": int((time.time() - started) * 1000),
    }


def main():
    if not os.path.exists(ENABLED):
        print("DISABLED")
        return 0
    token = access_token()
    status, me = call("GET", f"{API}/auth/me", token=token, timeout=30)
    if status != 200:
        raise RuntimeError(f"auth/me {status}")
    status, wfs = call("GET", f"{API}/api/workflows", token=token, timeout=60)
    rows = wfs if isinstance(wfs, list) else (wfs or {}).get("workflows") or []
    match = [w for w in rows if w.get("name") == WORKFLOW]
    if not match:
        raise RuntimeError("canary workflow missing")
    workflow_id = match[0]["id"]
    status, module = call("GET", f"{API}/api/sdk/modules/{MODULE}", token=token, timeout=60)
    if status != 200 or not (module or {}).get("hash"):
        raise RuntimeError(f"module pin {status}")
    pin = module["hash"]

    linux = run_linux(token, workflow_id)
    cloud = run_cloud(token, me, pin)

    same_status = linux["status"] == cloud["status"]
    same_result = (canonical(linux["result"], sort_lists=True)
                   == canonical(cloud["result"], sort_lists=True))
    orgs = (linux.get("result") or {}).get("organizations")
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workflow": WORKFLOW,
        "solution": SOLUTION,
        "module_sha256": pin,
        "linux_status": linux["status"],
        "cloud_status": cloud["status"],
        "match": bool(same_status and same_result),
        "same_status": same_status,
        "same_result": same_result,
        "linux_result_sha256": (digest(linux["result"], sort_lists=True)
                                if linux["result"] is not None else None),
        "cloud_result_sha256": (digest(cloud["result"], sort_lists=True)
                                if cloud["result"] is not None else None),
        "org_count": len(orgs) if isinstance(orgs, list) else None,
        "linux_ms": linux["ms"],
        "cloud_ms": cloud["ms"],
        "worker_reused": cloud.get("reused"),
        "cloud_http": cloud.get("http"),
        "linux_error": (str(linux["error"])[:160] if linux.get("error") else None),
        "cloud_error": (str(cloud["error"])[:160] if cloud.get("error") else None),
    }
    with open(OUT, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(json.dumps({k: record[k] for k in ("match", "linux_status", "cloud_status",
                                             "org_count", "linux_ms", "cloud_ms",
                                             "worker_reused")}))
    return 0 if record["match"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - the record is the diagnostic
        print("CANARY ERROR", type(exc).__name__, str(exc)[:200])
        sys.exit(2)
