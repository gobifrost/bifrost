"""Explicit source overlay qualification, separate from the accepted bundle pin."""
import hashlib
import json

SOURCE = '1796c6fcf68964aa2ff36421ccc7be697d6322f7'
EXPECTED = {
    'src/services/execution/engine.py': '443bf2b48e0f6ccc8eedf0160d6ac150466e110ba47de1321e31c0c2627709d2',
    'src/services/execution/worker.py': '4be5dda4b7089ff8e1668dd4f319083df46fe931d94adc6935d0aa6ca206c5cf',
    'bifrost/_execution_context.py': 'c8a5792bddcbbb3f2ffcfa7925d9124d034fdb8d368f515551e87c0da25809e0',
}


def validate_engine_context_overlay(packet):
    root = packet / 'project/dev/executor'
    text = (root / 'engine-context-overlay.mjs').read_text()
    prefix = 'export default '
    if not text.startswith(prefix) or not text.endswith(';\n'):
        raise RuntimeError('Explicit source overlay format required')
    value = json.loads(text[len(prefix):-2])
    if value.get('source') != SOURCE or set(value.get('files', {})) != set(EXPECTED):
        raise RuntimeError('Exact engine/context source required')
    for name, digest in EXPECTED.items():
        module = value['files'][name]
        if set(module) != {'py'} or hashlib.sha256(module['py'].encode()).hexdigest() != digest:
            raise RuntimeError('Engine/context source changed: '+name)
    if "entrypoint: './dev/executor/engine-context-local-entry.mjs'" not in (packet / 'project/cloudflare.config.ts').read_text():
        raise RuntimeError('Experimental entrypoint not selected')
    return {'source': SOURCE, 'files_sha256': EXPECTED, 'accepted_bundle_promoted': False}
