"""Which archived or current audit lines an export may carry."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.models.contracts.audit_retention import AuditExportRequest
from src.services.audit_retention.export import ReachSnapshot, line_matches
from src.services.audit_retention.format import ArchiveRow
from src.services.authorization.reach import EVERYTHING, OrgReach

IN_REACH = UUID("11111111-1111-1111-1111-111111111111")
OUT_OF_REACH = UUID("22222222-2222-2222-2222-222222222222")
OPERATOR = OrgReach(organization_ids=frozenset({IN_REACH}))
ACCESS_CHECKS_IN_2001 = AuditExportRequest(
    start_date=datetime(2001, 1, 1, tzinfo=UTC),
    end_date=datetime(2001, 12, 31, tzinfo=UTC),
    action="access.check",
)


def _row(
    *,
    org: UUID | None = IN_REACH,
    action: str = "access.check",
    at: datetime = datetime(2001, 6, 1, tzinfo=UTC),
) -> ArchiveRow:
    return ArchiveRow(
        id=UUID(int=1), created_at=at, organization_id=org, user_id=None, action=action,
        resource_type="table", resource_id=None, outcome="success", source="workflow",
        operation_id=None, surface="workflow", execution_id=None, ip_address=None,
        user_agent=None, details=None, actor_email=None, actor_name=None,
        organization_name="Example Org",
    )


def test_operator_reads_an_access_check_in_reach() -> None:
    assert line_matches(_row(), ACCESS_CHECKS_IN_2001, OPERATOR) is True


def test_operator_does_not_read_an_org_out_of_reach() -> None:
    assert line_matches(_row(org=OUT_OF_REACH), ACCESS_CHECKS_IN_2001, OPERATOR) is False


def test_operator_does_not_read_other_actions() -> None:
    every_action = ACCESS_CHECKS_IN_2001.model_copy(update={"action": None})

    assert line_matches(_row(action="user.update"), every_action, OPERATOR) is False
    assert line_matches(_row(action="user.update"), every_action, EVERYTHING) is True


def test_global_rows_need_a_global_reach() -> None:
    assert line_matches(_row(org=None), ACCESS_CHECKS_IN_2001, OPERATOR) is False
    assert line_matches(_row(org=None), ACCESS_CHECKS_IN_2001, OrgReach(include_global=True)) is True


def test_rows_outside_the_range_do_not_match() -> None:
    for at in (datetime(2000, 12, 31, 23, 59, tzinfo=UTC), datetime(2002, 1, 1, tzinfo=UTC)):
        assert line_matches(_row(at=at), ACCESS_CHECKS_IN_2001, EVERYTHING) is False


def test_organization_filter_narrows_an_everything_reach() -> None:
    one_org = ACCESS_CHECKS_IN_2001.model_copy(update={"organization_id": IN_REACH})

    assert line_matches(_row(), one_org, EVERYTHING) is True
    assert line_matches(_row(org=OUT_OF_REACH), one_org, EVERYTHING) is False


@pytest.mark.parametrize(
    "reach",
    [
        EVERYTHING,
        OPERATOR,
        OrgReach(organization_ids=frozenset({OUT_OF_REACH, IN_REACH}), managed=True, include_global=True),
    ],
)
def test_reach_snapshot_round_trips(reach: OrgReach) -> None:
    snapshot = ReachSnapshot.of(reach)

    assert snapshot.to_reach() == reach
    assert ReachSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_request_range_is_bounded() -> None:
    start = datetime(2001, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        AuditExportRequest(start_date=start, end_date=start)
    with pytest.raises(ValidationError):
        AuditExportRequest(start_date=start, end_date=datetime(2002, 1, 3, tzinfo=UTC))
    with pytest.raises(ValidationError):
        AuditExportRequest(start_date=datetime(2001, 1, 1), end_date=datetime(2001, 2, 1))
    assert AuditExportRequest(start_date=start, end_date=datetime(2002, 1, 2, tzinfo=UTC))
