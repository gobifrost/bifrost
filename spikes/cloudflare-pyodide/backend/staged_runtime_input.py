"""Exact experimental staging input, not runtime authorization or admission."""
import hashlib
import json


def validate_staged_runtime(packet):
    root = packet / 'project/backend'
    expected = {
        'generated-runtime.mjs': '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731',
        'runtime-code.mjs': '8cffaf4f9f08b505554e124347ad4324df64d5ebce63aa7b2118f6a7f547a52f',
        'sdk-cleanup-code.mjs': 'fb86cd80e5d06cd8b7dc3f83a6a70196b8ee5b65e30339fcce5539994ac4862f',
    }
    for name, digest in expected.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('Exact staged runtime input changed: ' + name)
    provenance = json.loads((root / 'runtime-provenance.json').read_text())
    if (provenance.get('bundle_sha256') != expected['generated-runtime.mjs']
        or provenance.get('sdk_source') != '139596497d57b219ddcc3616aa66ec58e1b3cf3c'
        or provenance.get('engine_source') != 'f49d4ba99cebd24aa497f1244833b940e640885f'
        or provenance.get('artifact_bytes_preserved') is not True
        or provenance.get('loader_files_sha256') != {name: digest for name, digest in expected.items() if name != 'generated-runtime.mjs'}):
        raise RuntimeError('Exact staged runtime provenance required')
    return provenance
