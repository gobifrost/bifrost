"""UUID native state serialization must survive the deployed interpreter."""
import copy
import pickle
from dataclasses import asdict, dataclass
from uuid import UUID, SafeUUID, uuid1, uuid4, uuid7

import pytest


@dataclass
class Row:
    id: UUID


@pytest.mark.parametrize('value', [uuid1(), uuid4(), uuid7(), UUID(int=1), UUID('d82579ce6642a0de7ddf490a7aec7aa5'), UUID(int=2, is_safe=SafeUUID.safe)])
def test_uuid_state_roundtrips(value):
    clones = [copy.copy(value), copy.deepcopy(value), asdict(Row(value))['id']]
    clones.extend(pickle.loads(pickle.dumps(value, protocol)) for protocol in range(pickle.HIGHEST_PROTOCOL + 1))
    for clone in clones:
        assert clone == value
        assert clone.is_safe == value.is_safe
