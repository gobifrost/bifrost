import subprocess
import sys
from pathlib import Path

_API = Path(__file__).resolve().parents[2]   # api/ on host; /app in container
_REPO = Path(__file__).resolve().parents[3]  # repo root on host; / in container
GEN = _REPO / ".claude/skills/bifrost-build/generated"

# Every committed appendix the generator owns. `--check` already proves each is
# byte-fresh; this list also guards against a generator silently dropping a file.
EXPECTED_APPENDICES = [
    "cli-reference.md",
    "openapi-digest.md",
    "python-sdk-signatures.md",
    "web-sdk-surface.md",
]


def _run_generator():
    return subprocess.run(
        [sys.executable, str(_API / "scripts/skill-truth/generate.py"), "--check"],
        capture_output=True, text=True,
    )


def test_appendices_are_fresh_and_present():
    # One generator run (it regenerates all appendices in-process) covers the
    # whole set — re-running per-file would re-boot app.openapi() + the node
    # subprocess for no extra coverage.
    result = _run_generator()
    assert result.returncode == 0, (
        f"generated/* is stale — run api/scripts/skill-truth/generate.py.\n"
        f"{result.stdout}\n{result.stderr}"
    )
    for fname in EXPECTED_APPENDICES:
        assert (GEN / fname).exists(), f"missing generated appendix: {fname}"
