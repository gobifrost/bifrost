"""`timeout_seconds = 0` means "no timeout" and must survive every read path.

The execution stack implements 0-as-no-timeout deliberately:

- ``services/execution/service.py`` preserves it with an ``is not None`` check
  in three separate places.
- ``services/execution/service.py`` gates on ``if workflow_meta.timeout_seconds > 0``.
- ``services/execution/process_pool.py`` treats ``timeout_seconds > 0`` as the
  precondition for expiring an execution.
- ``jobs/schedulers/execution_cleanup.py`` skips rows entirely, with the comment
  "timeout_seconds == 0 means no timeout".

Any path that coalesces the value with ``or 1800`` silently overrides an
explicit "no timeout" configuration. #27 fixed one such site; these tests cover
the remainder and guard the whole class from returning.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from src.services.workflow_validation import _convert_workflow_metadata_to_model

API_SRC = Path(__file__).resolve().parents[2] / "src"

# Fields where 0 is a meaningful, documented configuration value rather than
# "unset", so `or <default>` is always a bug.
ZERO_MEANINGFUL_FIELDS = ("timeout_seconds", "cache_ttl_seconds")


def _meta(**overrides):
    base = dict(
        name="example_workflow",
        description=None,
        category="General",
        tags=None,
        parameters=None,
        execution_mode="sync",
        timeout_seconds=0,
        time_saved=None,
        value=None,
        source_file_path="workflows/example.py",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_validation_preserves_zero_timeout() -> None:
    assert _convert_workflow_metadata_to_model(_meta(timeout_seconds=0)).timeout_seconds == 0


def test_validation_defaults_only_when_unset() -> None:
    assert _convert_workflow_metadata_to_model(_meta(timeout_seconds=None)).timeout_seconds == 1800


def test_validation_passes_explicit_timeout_through() -> None:
    assert _convert_workflow_metadata_to_model(_meta(timeout_seconds=60)).timeout_seconds == 60


def test_no_falsy_coalescing_on_zero_meaningful_fields() -> None:
    """Source guard: `<field> ... or <nonzero>` silently discards a stored 0.

    Use `X if X is not None else <default>` instead. This catches the pattern in
    any read path — including ones with no cheap unit-test seam, such as the
    endpoint metadata builder in routers/endpoints.py, whose coalesced value was
    additionally persisted into the Redis endpoint cache.
    """
    pattern = re.compile(
        r"\b(" + "|".join(ZERO_MEANINGFUL_FIELDS) + r")\b[^\n]*?\bor\s+[1-9][0-9]*\b"
    )
    offenders = []
    for path in sorted(API_SRC.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if pattern.search(line):
                offenders.append(f"{path.relative_to(API_SRC)}:{lineno}: {stripped}")

    assert not offenders, (
        "Found falsy-coalescing on a field where 0 is meaningful. Use\n"
        "`X if X is not None else <default>`:\n  " + "\n  ".join(offenders)
    )
