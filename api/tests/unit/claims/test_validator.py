import pytest
from pydantic import ValidationError

from src.models.contracts.claims import (
    ClaimQuery,
    CustomClaimCreate,
)


def test_name_must_match_pattern():
    with pytest.raises(ValidationError):
        CustomClaimCreate(
            name="Bad Name",
            type="list",
            query=ClaimQuery(table="t", select="x"),
        )


def test_name_lower_snake_ok():
    c = CustomClaimCreate(
        name="allowed_campus_ids",
        type="list",
        query=ClaimQuery(table="user_campus_access", select="campus_id"),
    )
    assert c.name == "allowed_campus_ids"


def test_query_where_uses_policy_expr_shape():
    c = CustomClaimCreate(
        name="allowed_campus_ids",
        type="list",
        query=ClaimQuery(
            table="user_campus_access",
            where={"eq": [{"row": "user_id"}, {"user": "user_id"}]},
            select="campus_id",
        ),
    )
    assert c.query.where is not None


def test_query_where_rejects_invalid_expr():
    with pytest.raises(ValidationError):
        CustomClaimCreate(
            name="allowed_campus_ids",
            type="list",
            query=ClaimQuery(
                table="user_campus_access",
                where={"unknown_op": [1, 2]},
                select="campus_id",
            ),
        )


def test_type_must_be_list_or_scalar():
    with pytest.raises(ValidationError):
        CustomClaimCreate(
            name="x",
            type="bag",  # type: ignore[arg-type]
            query=ClaimQuery(table="t", select="x"),
        )


def test_name_empty_string_rejected():
    with pytest.raises(ValidationError):
        CustomClaimCreate(name="", type="list", query=ClaimQuery(table="t", select="x"))


def test_query_table_required():
    with pytest.raises(ValidationError):
        ClaimQuery(select="x")  # missing table


def test_query_select_required():
    with pytest.raises(ValidationError):
        ClaimQuery(table="t")  # missing select
