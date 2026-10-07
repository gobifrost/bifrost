"""Reads of the workflow an audit entry names spell it the way
ix_audit_logs_access_check_workflow indexes it: ``details ->> 'workflow_id'``
with the key a literal, not a bound parameter the planner cannot match."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects import postgresql


def _sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect()))


def test_the_named_workflow_is_the_indexed_expression() -> None:
    from src.models.orm.audit import AuditLog, named_workflow_id

    [index] = [index for index in AuditLog.__table__.indexes if index.name == "ix_audit_logs_access_check_workflow"]

    assert _sql(named_workflow_id()) == "audit_logs.details ->> 'workflow_id'"
    assert [_sql(expression) for expression in index.expressions] == ["(details ->> 'workflow_id')", "audit_logs.created_at"]
    assert _sql(index.dialect_options["postgresql"]["where"]) == "action = 'access.check'"


def test_the_audit_log_filters_by_the_indexed_expression() -> None:
    from src.models.orm.audit import AuditLog
    from src.repositories.audit_logs import _filtered

    filters = dict.fromkeys(
        ("action_prefix", "resource_type", "outcome", "user_id", "execution_id", "organization_id",
         "start_date", "end_date", "search", "organizations"),
    )
    query = _filtered(select(AuditLog.id), **filters, workflow_id=uuid4())

    assert "(audit_logs.details ->> 'workflow_id') = " in _sql(query)
