#!/usr/bin/env python3
"""Disposable Cloudflare hook for the normal-image conformance harness.

This hook is intentionally separate from ``conformance/hooks/cloudflare.py``.
It delegates observation and provider restart to that hook, while its one
additional mutation calls the disposable Worker's exact stop route.  It never
uses the provider DELETE endpoint, so a process-stop fault remains distinct
from a provider tombstone.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any


UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
RESOURCE_UID_RE = re.compile(r"^[a-zA-Z0-9._:-]{1,255}$")
SCOPE_RE = re.compile(r"^[a-zA-Z0-9._:-]{1,128}$")
TOKEN_RE = re.compile(r"^[^\s]{32,512}$")
STOP_PREFIX = "/__conformance/v1/workers/"


class Unsupported(Exception):
    pass


def _base_hook() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "hooks" / "cloudflare.py"
    spec = importlib.util.spec_from_file_location("bifrost_cloudflare_conformance_hook", path)
    if spec is None or spec.loader is None:
        raise Unsupported("Cloudflare conformance hook is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _required(name: str, pattern: re.Pattern[str]) -> str:
    value = os.environ.get(name)
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Unsupported(f"{name} is invalid or missing")
    return value


def _endpoint() -> str:
    value = _required("CF_CONFORMANCE_WORKER_URL", re.compile(r"^https://[^\s/]+/?$"))
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise Unsupported("CF_CONFORMANCE_WORKER_URL is invalid or missing")
    return value.rstrip("/")


def _require_mutations() -> None:
    if os.environ.get("CF_HOOK_ALLOW_MUTATIONS") != "1":
        raise Unsupported("stop-process requires CF_HOOK_ALLOW_MUTATIONS=1")


def _require_bundled_wrapper() -> None:
    """Keep restart on the disposable wrapper, never production capacity.mjs.

    ``hooks/cloudflare.py`` deliberately uploads only ``capacity.mjs`` and
    ``capacity-core.mjs``.  A live harness must therefore provide a dry-run
    bundle under exactly those names, with the conformance route compiled into
    its ``capacity.mjs``.  Falling back to the checked-in production entry
    would silently remove the stop seam after a provider restart.
    """
    source = os.environ.get("CF_PROVIDER_SOURCE")
    if not source:
        raise Unsupported("CF_PROVIDER_SOURCE must name a bundled conformance wrapper")
    root = Path(source)
    wrapper, core = root / "capacity.mjs", root / "capacity-core.mjs"
    try:
        wrapper_source = wrapper.read_text(encoding="utf-8")
        core.read_bytes()
    except OSError:
        raise Unsupported("CF_PROVIDER_SOURCE requires bundled capacity.mjs and capacity-core.mjs") from None
    if "CONFORMANCE_CONTROL_TOKEN" not in wrapper_source or STOP_PREFIX not in wrapper_source:
        raise Unsupported("CF_PROVIDER_SOURCE capacity.mjs is not the conformance wrapper")


def _delegate(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    base = _base_hook()
    try:
        return getattr(base, operation)(payload)
    except base.Unsupported as exc:
        raise Unsupported(str(exc)) from None


def observe(payload: dict[str, Any]) -> dict[str, Any]:
    return _delegate("observe", payload)


def restart_provider(payload: dict[str, Any]) -> dict[str, Any]:
    _require_mutations()
    _require_bundled_wrapper()
    return _delegate("restart_provider", payload)


def stop_process(payload: dict[str, Any]) -> dict[str, Any]:
    _require_mutations()
    worker_id = payload.get("worker_id")
    resource_uid = payload.get("resource_uid")
    if not isinstance(worker_id, str) or UUID_RE.fullmatch(worker_id) is None:
        raise Unsupported("worker_id is invalid or missing")
    if not isinstance(resource_uid, str) or RESOURCE_UID_RE.fullmatch(resource_uid) is None:
        raise Unsupported("resource_uid is invalid or missing")
    endpoint = _endpoint()
    scope = _required("CF_CONFORMANCE_SCOPE", SCOPE_RE)
    control_token = _required("CF_CONFORMANCE_CONTROL_TOKEN", TOKEN_RE)
    request = urllib.request.Request(
        f"{endpoint}{STOP_PREFIX}{worker_id}/stop",
        method="POST",
        data=json.dumps({"resource_uid": resource_uid}).encode(),
        headers={
            "Authorization": f"Bearer {control_token}",
            "Content-Type": "application/json",
            "X-Bifrost-Capacity-Scope": scope,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 202:
                raise Unsupported("conformance stop request was rejected")
    except urllib.error.HTTPError as exc:
        raise Unsupported(f"conformance stop request was rejected ({exc.code})") from None
    except (urllib.error.URLError, TimeoutError):
        raise Unsupported("conformance stop request was unavailable") from None
    return {"ok": True}


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: cloudflare_live.py <op>"}), file=sys.stderr)
        return 2
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        if sys.argv[1] == "observe":
            print(json.dumps(observe(payload)))
            return 0
        if sys.argv[1] == "restart-provider":
            print(json.dumps(restart_provider(payload)))
            return 0
        if sys.argv[1] == "stop-process":
            print(json.dumps(stop_process(payload)))
            return 0
        raise Unsupported(f"{sys.argv[1]} is not available on Cloudflare")
    except Unsupported as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
