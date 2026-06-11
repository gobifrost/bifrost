"""Bind/result round-trip tests for the minimal pgvector Vector type.

Pure function tests — no DB. Wire-format parity with pgvector's own
SQLAlchemy type (text in, text out) is what keeps the swap in
src/models/orm/knowledge.py behavior-identical; the e2e knowledge
round-trip validates against a real pgvector database.
"""

import pytest

from src.models.orm.vector_type import Vector


@pytest.fixture
def bind():
    return Vector().bind_processor(dialect=None)


@pytest.fixture
def result():
    return Vector().result_processor(dialect=None, coltype=None)


def test_bind_none_passes_through(bind):
    assert bind(None) is None


def test_bind_formats_pgvector_text_literal(bind):
    assert bind([1.0, -2.5, 0.0]) == "[1.0,-2.5,0.0]"


def test_bind_coerces_ints_and_strings_to_float(bind):
    assert bind([1, 2]) == "[1.0,2.0]"
    assert bind(["0.25", "3"]) == "[0.25,3.0]"


def test_bind_repr_preserves_full_float_precision(bind):
    value = 0.1234567890123456789
    assert bind([value]) == f"[{value!r}]"


def test_result_none_passes_through(result):
    assert result(None) is None


def test_result_parses_pgvector_text_literal(result):
    assert result("[1,-2.5,0]") == [1.0, -2.5, 0.0]


def test_result_empty_vector(result):
    assert result("[]") == []


def test_result_non_string_passes_through(result):
    # If a driver-level codec ever materializes a list, pass it through.
    assert result([1.0, 2.0]) == [1.0, 2.0]


def test_round_trip_preserves_values(bind, result):
    original = [0.1, -0.000001, 123456.789, 0.0]
    assert result(bind(original)) == original


def test_cosine_distance_compiles_to_pgvector_operator():
    from sqlalchemy import Column, MetaData, Table

    table = Table("t", MetaData(), Column("embedding", Vector()))
    expr = table.c.embedding.cosine_distance([1.0, 2.0])
    assert "<=>" in str(expr.compile(compile_kwargs={"literal_binds": False}))


def test_column_spec_is_unconstrained_vector():
    assert Vector().get_col_spec() == "VECTOR"
