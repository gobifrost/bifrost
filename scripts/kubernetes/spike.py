"""Shared helpers for the local Kind experiments in this directory."""
from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / "scripts/kubernetes/local-kind.sh"
NS = "bifrost-local"


def kubectl(*args: str, data: str | None = None) -> str:
    result = subprocess.run(
        [str(LOCAL), "kubectl", "--", "-n", NS, *args], input=data,
        text=True, capture_output=True, check=True, timeout=60,
    )
    return result.stdout
