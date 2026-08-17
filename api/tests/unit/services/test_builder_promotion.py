"""Focused promotion review helpers."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.builder.promotion import _promotion_build_ids


def test_promotion_uses_every_build_from_the_canonical_deploy_result() -> None:
    first = uuid4()
    second = uuid4()
    turn = SimpleNamespace(build_job_id=first)
    deploy = SimpleNamespace(result={"build_job_ids": [str(first), str(second)]})

    assert _promotion_build_ids(turn, deploy) == [first, second]


def test_promotion_falls_back_to_legacy_first_build_projection() -> None:
    build_id = uuid4()

    assert _promotion_build_ids(SimpleNamespace(build_job_id=build_id), None) == [
        build_id
    ]


def test_promotion_rejects_malformed_build_identifiers() -> None:
    with pytest.raises(ValueError, match="malformed build identifiers"):
        _promotion_build_ids(
            SimpleNamespace(build_job_id=None),
            SimpleNamespace(result={"build_job_ids": ["not-a-uuid"]}),
        )
