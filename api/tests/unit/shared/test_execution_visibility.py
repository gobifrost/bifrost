"""Which executions a caller reads as their own (``shared.execution_visibility``)."""

from __future__ import annotations

from uuid import uuid4

from shared.execution_visibility import is_own_execution, is_own_pending_execution

ME = uuid4()
COLLEAGUE = uuid4()
EXECUTION = uuid4()


def test_the_acting_user_owns_the_execution():
    owned = is_own_execution(
        ME, execution_id=EXECUTION, executed_by=ME, started_by_user_id=None, root_execution_id=None
    )

    assert owned is True


def test_the_initiator_owns_a_root_run_acting_as_someone_else():
    owned = is_own_execution(
        ME, execution_id=EXECUTION, executed_by=COLLEAGUE, started_by_user_id=ME, root_execution_id=EXECUTION
    )

    assert owned is True


def test_the_initiator_does_not_own_a_child_run():
    owned = is_own_execution(
        ME, execution_id=EXECUTION, executed_by=COLLEAGUE, started_by_user_id=ME, root_execution_id=uuid4()
    )

    assert owned is False


def test_someone_else_does_not_own_it():
    owned = is_own_execution(
        uuid4(), execution_id=EXECUTION, executed_by=COLLEAGUE, started_by_user_id=ME, root_execution_id=EXECUTION
    )

    assert owned is False


def test_a_row_without_lineage_is_decided_by_the_acting_user_only():
    owned = is_own_execution(
        ME, execution_id=EXECUTION, executed_by=COLLEAGUE, started_by_user_id=None, root_execution_id=None
    )

    assert owned is False


def test_a_pending_record_compares_its_string_ids():
    root = {
        "user_id": str(COLLEAGUE),
        "lineage": {
            "run_user_id": str(ME),
            "started_by_user_id": str(ME),
            "root_execution_id": str(EXECUTION),
        },
    }
    child = {**root, "lineage": {**root["lineage"], "root_execution_id": str(uuid4())}}

    root_owned = is_own_pending_execution(ME, str(EXECUTION), root)
    child_owned = is_own_pending_execution(ME, str(EXECUTION), child)
    legacy_owned = is_own_pending_execution(COLLEAGUE, str(EXECUTION), {"user_id": str(COLLEAGUE)})

    assert root_owned is True
    assert child_owned is False
    assert legacy_owned is True
