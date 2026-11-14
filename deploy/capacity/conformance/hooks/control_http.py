"""Control-plane hook: drives a provider's admin endpoints.

The capacity protocol has no side channel, so conformance needs one to restart a
provider, kill a physical launcher, and inject faults. This hook talks to a
small admin server that a provider-under-test exposes.

Environment:
    CONTROL_URL   base URL of the admin server
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

OPS = {"observe": "observe", "restart-provider": "restart", "stop-process": "stop",
       "inject-fault": "fault", "clear-fault": "clear-fault"}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in OPS:
        print(json.dumps({"error": "usage: control_http.py <op>"}), file=sys.stderr)
        return 2
    payload = json.loads(sys.stdin.read() or "{}")
    base = os.environ.get("CONTROL_URL")
    if not base:
        print(json.dumps({"error": "CONTROL_URL is not set"}), file=sys.stderr)
        return 3

    suffix = OPS[sys.argv[1]]
    url = f"{base.rstrip('/')}/{suffix}?{urllib.parse.urlencode(payload)}"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            sys.stdout.write(resp.read().decode() or "{}")
            return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
