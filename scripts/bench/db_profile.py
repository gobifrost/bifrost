"""Collect the frozen benchmark hook's parameter-free counters on every API.

The context measures one bounded 60-second window after warmup. It never changes
pool settings. Hook and runtime identity belong to the campaign image receipt.
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path


def command(cluster, pod: str, label: str, action: str) -> dict | None:
    if not re.fullmatch(r"[a-z0-9-]{1,50}", label) or action not in {"start", "stop"}:
        raise ValueError("invalid counter label or action")
    code = (
        "import json,pathlib,os,signal,time; "
        "ready=json.loads(pathlib.Path('/tmp/bench-profile-ready.json').read_text()); "
        f"pathlib.Path('/tmp/bench-profile-command.json').write_text(json.dumps({{'action':{action!r},'label':{label!r}}})); "
        "os.kill(ready['pid'],signal.SIGUSR2)"
    )
    if action == "stop":
        code += (
            f"\np=pathlib.Path('/tmp/bench-profile-{label}.json');end=time.monotonic()+15"
            "\nwhile not p.exists():"
            "\n if time.monotonic()>end:raise TimeoutError('counter receipt absent')"
            "\n time.sleep(.02)"
            "\nprint(p.read_text())"
        )
    value = cluster.kubectl("exec", pod, "-c", "api", "--", "python", "-c", code, timeout_s=30)
    return json.loads(value) if action == "stop" else None


@contextmanager
def measure(cluster, label: str, out: Path, *, delay_s: float = 105, duration_s: float = 60):
    """Retain each replica immediately; a probe error invalidates this window."""
    if out.exists():
        raise FileExistsError(out)
    if delay_s < 0 or duration_s <= 0:
        raise ValueError("counter timing must be positive")
    expected = cluster.api_pod_records()
    evidence = {"status": "pending", "api_pods": expected, "replicas": [],
                "delay_s": delay_s, "duration_s": duration_s,
                "scope": "per-replica sequential 60-second counter windows; no SQL parameters"}
    stopped = threading.Event()
    errors: list[BaseException] = []

    def save():
        pending = out.with_suffix('.pending')
        pending.write_text(json.dumps(evidence, indent=2) + '\n')
        pending.replace(out)

    def collect():
        active = []
        try:
            if stopped.wait(delay_s):
                raise RuntimeError("load ended before counter window")
            if cluster.api_pod_records() != expected:
                raise RuntimeError("API identities changed before counters")
            for record in expected:
                command(cluster, record['pod'], label, 'start')
                active.append(record)
            interrupted = stopped.wait(duration_s)
            for record in active[:]:
                raw = command(cluster, record['pod'], label, 'stop')
                evidence['replicas'].append({**record, 'raw': raw})
                active.remove(record)
                save()
            if interrupted:
                raise RuntimeError("load ended before counter window completed")
            if cluster.api_pod_records() != expected:
                raise RuntimeError("API identities changed during counters")
            evidence['status'] = 'pass'
        except BaseException as error:
            errors.append(error)
            evidence.update(status='invalid', error_class=type(error).__name__, error=str(error))
        finally:
            for record in active:
                try:
                    raw = command(cluster, record['pod'], label, 'stop')
                    evidence['replicas'].append({**record, 'raw': raw, 'partial': True})
                except Exception as error:
                    evidence.setdefault('cleanup_errors', []).append(type(error).__name__)
            save()

    thread = threading.Thread(target=collect, daemon=True)
    save()
    thread.start()
    try:
        yield evidence
    finally:
        stopped.set()
        thread.join()
    if errors:
        raise RuntimeError("per-API counter window invalid; see retained evidence") from errors[0]
