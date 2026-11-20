"""Pure static graph rounds; dependency authority stays with the API resolver."""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from shared.json_compute_transfer import read_json_transfer, write_json_transfer
from shared.workflow_conversion_limits import ANALYZER_VERSION, MAX_GRAPH_STEPS, MAX_SOURCE_BYTES

if TYPE_CHECKING:
    from shared.workflow_conversion_analysis import SourceFile


OPERATION = 'workflow.graph.analyze.v1'
INPUT_KIND = 'workflow_graph_inputs'
RESULT_KIND = 'workflow_graph_results'
MAX_TRANSFER_BYTES = 64 * 1024 * 1024
MAX_ROUND_SOURCE_BYTES = 16 * 1024 * 1024


def _source(value: object, summaries: dict) -> SourceFile:
    from shared.workflow_conversion_analysis import SourceFile

    if not isinstance(value, dict) or set(value) != {'key', 'path', 'source', 'content_hash', 'kind'}:
        raise ValueError('Invalid graph source descriptor')
    if not all(isinstance(value[k], str) for k in value):
        raise ValueError('Graph source descriptor fields must be strings')
    key, path, source, digest, kind = (value[k] for k in ('key', 'path', 'source', 'content_hash', 'kind'))
    if (not key or len(key) > 4096 or not path or len(path) > 4096 or PurePosixPath(path).is_absolute()
            or '..' in PurePosixPath(path).parts or '\\' in path or kind not in {'module', 'package', 'namespace'}
            or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('Invalid graph source identity')
    if len(source.encode()) > MAX_SOURCE_BYTES:
        # Preserve the analyzer's existing oversized-source unknown result.
        if key not in summaries:
            raise ValueError('Graph source exceeds per-file transfer limit')
    if key not in summaries:
        expected = hashlib.sha256((f'namespace:{key}' if kind == 'namespace' else source).encode()).hexdigest()
        if digest != expected:
            raise ValueError('Graph source digest mismatch')
    elif source:
        raise ValueError('Cached graph source must omit raw source')
    return SourceFile(key, path, source, digest, kind)


def request_key(source_key: str, module: str, level: int) -> tuple[str, str, int]:
    if (not isinstance(source_key, str) or not isinstance(module, str) or len(module) > 4096
            or (module and not all(p.isidentifier() for p in module.split('.')))
            or type(level) is not int or not 0 <= level <= 256):
        raise ValueError('Invalid graph dependency request')
    return source_key, module, level


def _valid_summary(summary: object) -> bool:
    return (isinstance(summary, dict) and summary.get('version') == ANALYZER_VERSION
        and all(isinstance(summary.get(field), dict) for field in ('functions', 'classes', 'bindings'))
        and isinstance(summary.get('top_level'), list))


def validate_inputs(value: object) -> dict:
    if not isinstance(value, dict) or value.get('version') != ANALYZER_VERSION:
        raise ValueError('Graph analyzer version mismatch')
    summaries, files, resolutions = value.get('summaries'), value.get('files'), value.get('resolutions')
    if (not isinstance(summaries, dict) or not isinstance(files, list) or not 1 <= len(files) <= MAX_GRAPH_STEPS + 1
            or not isinstance(resolutions, list) or len(resolutions) > MAX_GRAPH_STEPS):
        raise ValueError('Graph snapshot exceeds its structural bounds')
    sources = [_source(item, summaries) for item in files]
    keys = {s.key for s in sources}
    if len(keys) != len(files) or not set(summaries).issubset(keys) or value.get('entry_key') not in keys:
        raise ValueError('Graph snapshot has conflicting source identities')
    if sum(len(s.source.encode()) for s in sources) > MAX_ROUND_SOURCE_BYTES:
        raise ValueError('Graph round exceeds source byte budget')
    if any(not _valid_summary(s) for s in summaries.values()):
        raise ValueError('Invalid graph source summary')
    seen = set()
    for item in resolutions:
        if not isinstance(item, list) or len(item) != 4:
            raise ValueError('Invalid graph resolution')
        key = request_key(*item[:3])
        if key in seen or key[0] not in keys or (item[3] is not None and item[3] not in keys):
            raise ValueError('Graph resolution is outside captured sources')
        seen.add(key)
    roots, function = value.get('roots'), value.get('function')
    if (not isinstance(roots, list) or len(roots) > 1024 or not all(isinstance(r, str) and r.isidentifier() for r in roots)
            or not isinstance(function, str) or not function.isidentifier()):
        raise ValueError('Invalid graph entry point or runtime inventory')
    return value


def encode_inputs(value: dict):
    return write_json_transfer(validate_inputs(value), INPUT_KIND, MAX_TRANSFER_BYTES)


def decode_inputs(transfer):
    return validate_inputs(read_json_transfer(transfer, INPUT_KIND, MAX_TRANSFER_BYTES))


async def analyze_round(value: dict) -> dict:
    from shared.workflow_conversion_analysis import analyze_workflow, summarize_source

    value = validate_inputs(value)
    summaries = dict(value['summaries'])
    files = {item['key']: _source(item, summaries) for item in value['files']}
    # Parse each supplied file once, including metadata-only resolutions. The
    # analyzer retains its own distinct limit on semantically loaded sources.
    for key, file in files.items():
        if key not in summaries:
            summaries[key] = summarize_source(file.source)
    resolutions = {tuple(item[:3]): item[3] for item in value['resolutions']}
    requests = set()

    async def resolve(source, module, level):
        key = request_key(source.key, module, level)
        if key not in resolutions:
            requests.add(key)
            if len(requests) > MAX_GRAPH_STEPS:
                raise ValueError('Graph dependency request budget exceeded')
            return None
        target = resolutions[key]
        return files[target] if target is not None else None

    async def summarize(source):
        return summaries[source.key]

    result = await analyze_workflow(files[value['entry_key']], value['function'], resolve, summarize,
        external_module_roots=frozenset(value['roots']))
    return {'analysis': result, 'requests': [list(r) for r in sorted(requests)], 'summaries': summaries}


def encode_results(value: dict, digest: str):
    return write_json_transfer({'input_sha256': digest, **value}, RESULT_KIND, MAX_TRANSFER_BYTES)


def decode_results(transfer, *, digest: str, snapshot: dict) -> dict:
    value = read_json_transfer(transfer, RESULT_KIND, MAX_TRANSFER_BYTES)
    if value.get('input_sha256') != digest:
        raise ValueError('Graph result input binding mismatch')
    files = {item['key']: item for item in snapshot['files']}
    requests, summaries, analysis = value.get('requests'), value.get('summaries'), value.get('analysis')
    if not isinstance(requests, list) or len(requests) > MAX_GRAPH_STEPS or not isinstance(summaries, dict) or not set(summaries).issubset(files):
        raise ValueError('Graph result exceeds source or request bounds')
    known = {tuple(item[:3]) for item in snapshot['resolutions']}
    seen = set()
    for item in requests:
        if not isinstance(item, list) or len(item) != 3:
            raise ValueError('Invalid graph result dependency')
        key = request_key(*item)
        if key[0] not in files or key in known or key in seen:
            raise ValueError('Graph result repeats or widens a dependency request')
        seen.add(key)
    if any(not _valid_summary(s) for s in summaries.values()):
        raise ValueError('Graph result has an invalid summary')
    if any(summaries.get(key) != summary for key, summary in snapshot['summaries'].items()):
        raise ValueError('Graph result changed a captured summary')
    if not isinstance(analysis, dict) or type(analysis.get('entrypoint_found')) is not bool:
        raise ValueError('Graph result omitted entry point state')
    dependencies = analysis.get('dependencies')
    if (not isinstance(dependencies, dict) or not dependencies or snapshot['entry_key'] not in dependencies
            or any(key not in files or files[key]['content_hash'] != digest for key, digest in dependencies.items())
            or not set(dependencies).issubset(summaries)):
        raise ValueError('Graph result dependencies differ from captured source')
    for field in ('sdk_calls', 'unknowns', 'warnings'):
        if not isinstance(analysis.get(field), list) or not all(isinstance(x, str) for x in analysis[field]):
            raise ValueError('Graph result omitted findings')
    for field in ('evidence', 'resource_calls'):
        if not isinstance(analysis.get(field), list) or not all(isinstance(x, dict) for x in analysis[field]):
            raise ValueError('Graph result omitted evidence')
    paths = {item['path'] for item in files.values()}
    calls = set(analysis['sdk_calls'])
    for item in analysis['evidence']:
        if (item.get('path') not in paths or item.get('call') not in calls
                or type(item.get('line')) is not int or item['line'] < 1
                or not isinstance(item.get('via'), list) or not all(isinstance(v, str) for v in item['via'])):
            raise ValueError('Graph evidence is outside captured source')
    for item in analysis['resource_calls']:
        key = item.get('source_key')
        if (key not in dependencies or item.get('path') != files[key]['path'] or item.get('call') not in calls
                or type(item.get('line')) is not int or item['line'] < 1
                or not isinstance(item.get('selectors'), dict) or not isinstance(item.get('selector_reasons'), dict)
                or not isinstance(item.get('dynamic_selectors'), list)):
            raise ValueError('Graph resource evidence is outside captured source')
    return value


def snapshot(entry: SourceFile, function: str, roots: frozenset[str]) -> dict:
    return {'version': ANALYZER_VERSION, 'entry_key': entry.key, 'function': function,
        'roots': sorted(roots), 'files': [asdict(entry)], 'summaries': {}, 'resolutions': []}
