"""Actual fixed overlay plus tamper refusals; no platform calls."""
import json
from pathlib import Path

import pytest

from engine_context_input import validate_engine_context_overlay

SOURCE = Path('/source-packet/project/dev/executor/engine-context-overlay.mjs')


@pytest.fixture
def packet(tmp_path):
    target = tmp_path / 'project/dev/executor'
    target.mkdir(parents=True)
    (target / 'engine-context-overlay.mjs').write_bytes(SOURCE.read_bytes())
    (tmp_path / 'project/cloudflare.config.ts').write_text("entrypoint: './dev/executor/engine-context-local-entry.mjs'")
    return tmp_path


def test_real_fixed_overlay_remains_explicitly_experimental(packet):
    value = validate_engine_context_overlay(packet)
    assert value['accepted_bundle_promoted'] is False


@pytest.mark.parametrize('change', ['source', 'bytes', 'extra-module', 'entrypoint'])
def test_altered_overlay_refused_before_runtime_creation(packet, change):
    path = packet / 'project/dev/executor/engine-context-overlay.mjs'
    value = json.loads(path.read_text()[len('export default '):-2])
    if change == 'source':
        value['source'] = 'unqualified'
    elif change == 'bytes':
        value['files']['src/services/execution/engine.py']['py'] += '\n'
    elif change == 'extra-module':
        value['files']['unapproved.py'] = {'py': 'value = 7'}
    else:
        (packet / 'project/cloudflare.config.ts').write_text('other entrypoint')
    path.write_text('export default '+json.dumps(value)+';\n')
    with pytest.raises(RuntimeError):
        validate_engine_context_overlay(packet)
