"""_convert_workflow_orm_to_schema must report a stored cache_ttl_seconds of 0
verbatim rather than folding it into the 300s default.

0 is a meaningful value, not "unset": PATCH /api/workflows/{id} documents and
accepts the range 0-86400, and the execution engine skips caching entirely when
the TTL is 0 (`if is_data_provider and request.cache_ttl_seconds > 0` in
services/execution/engine.py). Coalescing with `or 300` therefore made the API
report 300 for a data provider that is genuinely uncached — and because this
field round-trips through the workflow settings UI, saving that form wrote the
displayed 300 back, silently re-enabling caching on a provider that had been
deliberately opted out.

This is the same defect #27 fixed one line above for timeout_seconds.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import src.routers.workflows as wf


def _orm(**overrides):
    """Minimal stand-in exposing the attributes the converter reads."""
    base = dict(
        id=uuid4(),
        name="example_provider",
        function_name="example_provider",
        display_name=None,
        description=None,
        category=None,
        tags=None,
        type="data_provider",
        organization_id=None,
        solution_id=None,
        access_level=None,
        parameters_schema=None,
        execution_mode=None,
        timeout_seconds=None,
        endpoint_enabled=None,
        allowed_methods=None,
        disable_global_key=None,
        public_endpoint=None,
        tool_description=None,
        cache_ttl_seconds=300,
        time_saved=None,
        value=None,
        path="providers/example.py",
        created_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_zero_ttl_is_preserved_not_defaulted():
    assert wf._convert_workflow_orm_to_schema(_orm(cache_ttl_seconds=0)).cache_ttl_seconds == 0


def test_unset_ttl_falls_back_to_default():
    assert wf._convert_workflow_orm_to_schema(_orm(cache_ttl_seconds=None)).cache_ttl_seconds == 300


def test_explicit_ttl_is_passed_through():
    assert wf._convert_workflow_orm_to_schema(_orm(cache_ttl_seconds=60)).cache_ttl_seconds == 60


def test_zero_timeout_is_preserved_not_defaulted():
    """Regression guard for the sibling field fixed in #27; 0 = no timeout."""
    assert wf._convert_workflow_orm_to_schema(_orm(timeout_seconds=0)).timeout_seconds == 0
