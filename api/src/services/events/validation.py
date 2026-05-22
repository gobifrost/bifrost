import re

TOPIC_REGEX = re.compile(r"^[a-z0-9_.]+$")
TOPIC_MAX_LEN = 100


def validate_topic(topic: str) -> None:
    """Raise ValueError if topic is invalid."""
    if not topic or len(topic) > TOPIC_MAX_LEN:
        raise ValueError(f"Topic must be 1-{TOPIC_MAX_LEN} chars")
    if not TOPIC_REGEX.match(topic):
        raise ValueError("Topic must match ^[a-z0-9_.]+$")
    if "." not in topic:
        raise ValueError("Topic must contain at least one dot (e.g. 'user.invited')")
