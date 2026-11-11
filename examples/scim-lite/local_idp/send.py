"""Arbitrary local IdP simulator: prints SCIM-lite webhook payloads.

Pipe a payload into a registered webhook workflow or `bifrost run`
to exercise scim_user_event without a real identity provider.
Usage: python3 local_idp/send.py hire | suspend | promote
"""

import json
import sys

SCENARIOS = {
    "hire": {
        "external_id": "idp-1001",
        "email": "ada@example.com",
        "name": "Ada",
        "active": True,
        "groups": ["support-tier1"],
    },
    "promote": {
        "external_id": "idp-1001",
        "email": "ada@example.com",
        "name": "Ada Lovelace",
        "active": True,
        "groups": ["support-tier1", "support-leads"],
    },
    "suspend": {
        "external_id": "idp-1001",
        "email": "ada@example.com",
        "name": "Ada Lovelace",
        "active": False,
        "groups": [],
    },
}


def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "hire"
    try:
        payload = SCENARIOS[scenario]
    except KeyError:
        raise SystemExit(f"unknown scenario {scenario!r}; choose {sorted(SCENARIOS)}")
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
