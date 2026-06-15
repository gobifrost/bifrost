import subprocess
import sys
from pathlib import Path

import pytest

_API = Path(__file__).resolve().parents[2]   # api/ on host; /app in container
_REPO = Path(__file__).resolve().parents[3]  # repo root on host; / in container
GEN = _REPO / ".claude/skills/bifrost-build/generated"


def _run_generator():
    return subprocess.run(
        [sys.executable, str(_API / "scripts/skill-truth/generate.py"), "--check"],
        capture_output=True, text=True,
    )


def test_cli_reference_is_fresh():
    result = _run_generator()
    assert result.returncode == 0, (
        f"generated/* is stale — run scripts/skill-truth/generate.py.\n{result.stdout}\n{result.stderr}"
    )
    assert (GEN / "cli-reference.md").exists()


@pytest.mark.parametrize("fname", [
    "python-sdk-signatures.md",
    "openapi-digest.md",
    "web-sdk-surface.md",
])
def test_appendix_present_and_fresh(fname):
    result = _run_generator()
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert (GEN / fname).exists()
