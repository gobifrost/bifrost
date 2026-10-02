import pytest

from src.models.contracts.editor import SearchRequest
from src.services.source_search.cursor import SearchPosition, decode_cursor, encode_cursor, fingerprint


def test_round_trip():
    req = SearchRequest(query="x")
    pos = SearchPosition(rank=1, scope="3f0c0000-0000-0000-0000-000000000000", path="a.py", line=4, column=2, seen=25)
    assert decode_cursor(encode_cursor(fingerprint(req), pos), fingerprint(req)) == pos


def test_cursor_rejected_for_different_query():
    token = encode_cursor(fingerprint(SearchRequest(query="x")), SearchPosition(0, "", "a", 1, 0, 25))
    with pytest.raises(ValueError, match="does not belong to this search"):
        decode_cursor(token, fingerprint(SearchRequest(query="y")))


def test_cursor_rejected_for_different_filters():
    token = encode_cursor(fingerprint(SearchRequest(query="x")), SearchPosition(0, "", "a", 1, 0, 25))
    with pytest.raises(ValueError, match="does not belong to this search"):
        decode_cursor(token, fingerprint(SearchRequest(query="x", include_pattern="*.py")))


def test_limit_change_keeps_cursor_valid():
    a, b = SearchRequest(query="x", limit=25), SearchRequest(query="x", limit=100)
    assert fingerprint(a) == fingerprint(b)


def test_garbage_cursor_is_value_error():
    with pytest.raises(ValueError, match="cursor is invalid"):
        decode_cursor("not-base64!!", "f")
