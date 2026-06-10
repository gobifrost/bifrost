"""Minimal pgvector column type — no numpy dependency.

pgvector.sqlalchemy imports numpy at module level (~35MB), and this
module is in every role's import closure via src.models.orm. Bifrost
only ever binds list[float] and reads back list[float], and only uses
the <=> (cosine distance) operator, so a 40-line UserDefinedType
replicates the exact wire behavior (text in, text out — pgvector's
own SQLAlchemy type does the same string round-trip when no asyncpg
codec is registered, which Bifrost never registers).
"""
from __future__ import annotations

from sqlalchemy.types import Float, UserDefinedType


class Vector(UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw) -> str:
        return "VECTOR"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            return "[" + ",".join(repr(float(v)) for v in value) + "]"
        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None or not isinstance(value, str):
                return value
            inner = value.strip()[1:-1]
            return [float(v) for v in inner.split(",")] if inner else []
        return process

    class comparator_factory(UserDefinedType.Comparator):
        def cosine_distance(self, other):
            return self.op("<=>", return_type=Float)(other)
