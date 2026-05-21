import pytest

from src.services.events.validation import validate_topic


def test_valid_topics():
    validate_topic("user.invited")
    validate_topic("acme.deal_won")
    validate_topic("org.user.created")
    validate_topic("a.b")
    validate_topic("abc123.def")
    validate_topic("a" * 49 + "." + "b" * 49)  # exactly 100 chars


def test_empty_raises():
    with pytest.raises(ValueError, match="1-100 chars"):
        validate_topic("")


def test_too_long_raises():
    with pytest.raises(ValueError, match="1-100 chars"):
        validate_topic("a.b" + "c" * 99)  # 102 chars


def test_no_dot_raises():
    with pytest.raises(ValueError, match="at least one dot"):
        validate_topic("nodot")


def test_uppercase_raises():
    with pytest.raises(ValueError, match=r"\^"):
        validate_topic("User.invited")


def test_spaces_raises():
    with pytest.raises(ValueError, match=r"\^"):
        validate_topic("user invited")


def test_special_chars_raises():
    with pytest.raises(ValueError, match=r"\^"):
        validate_topic("user.inv!ted")


def test_hyphen_raises():
    with pytest.raises(ValueError, match=r"\^"):
        validate_topic("user-invited")


def test_exactly_100_chars_valid():
    # 49 chars + dot + 50 chars = 100
    topic = "a" * 49 + "." + "b" * 50
    validate_topic(topic)


def test_101_chars_raises():
    topic = "a" * 50 + "." + "b" * 50  # 101 chars
    with pytest.raises(ValueError, match="1-100 chars"):
        validate_topic(topic)
