"""Fresh, append-only D3 workload declaration and effect-row contract.

The source is deliberately separate from capacity workloads.  Each invocation
appends an effect document; no unique key, upsert, or retry hides a duplicate.
The host collector queries only rows for generated benchmark request IDs.
"""

from __future__ import annotations

import hashlib


WORKLOAD_VERSION = "recovery-effects-v1"
EFFECT_TABLE = "bench_recovery_effects_v1"

WORKFLOW_SOURCE = '''from bifrost import tables, workflow

@workflow(name="bench_wf_recovery_effects_v1", description="Append-only recovery effect probe.")
async def bench_wf_recovery_effects_v1(bench_request_id: str, effect_token: str, effect_index: int = 0) -> dict:
    await tables.insert("bench_recovery_effects_v1", {
        "bench_request_id": bench_request_id,
        "effect_token": effect_token,
        "effect_index": effect_index,
        "workload_version": "recovery-effects-v1",
        "class": "wf-recovery-effects",
    })
    return {"bench_request_id": bench_request_id, "effect_token": effect_token, "effect_index": effect_index}
'''


def effect_token(request_id: str, effect_index: int = 0) -> str:
    if not request_id or effect_index < 0:
        raise ValueError("effect token needs a nonempty request ID and nonnegative index")
    return hashlib.sha256(f"{WORKLOAD_VERSION}:{request_id}:{effect_index}".encode()).hexdigest()


def declaration() -> dict:
    """The exact fresh workload/table identity a recovery runner must declare."""
    return {"workload_version": WORKLOAD_VERSION, "workflow_name": "bench_wf_recovery_effects_v1",
            "table": EFFECT_TABLE, "append_only": True, "source": WORKFLOW_SOURCE}
