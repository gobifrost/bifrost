"""API-only dependency resolution around pure, bounded worker graph rounds."""
from dataclasses import asdict, dataclass, replace

from shared import workflow_graph_compute as codec
from shared.file_compute import FileCompute, decode_file_result, encode_file_transfer
from shared.workflow_conversion_analysis import MAX_GRAPH_STEPS, SourceFile, ResolveImport, MAX_SOURCE_BYTES, summarize_source


@dataclass
class PreparedWorkflowGraph:
    analysis: dict
    sources: dict[str, SourceFile]
    summaries: dict[str, dict]
    allow_outbound_access: bool = False
    resolutions: tuple = ()


async def prepare_workflow_graph(entry: SourceFile, function: str, roots: frozenset[str],
                                 resolve: ResolveImport, compute: FileCompute) -> PreparedWorkflowGraph:
    state = codec.snapshot(entry, function, roots)
    sources = {entry.key: entry}
    if len(entry.source.encode()) > MAX_SOURCE_BYTES:
        state['summaries'][entry.key] = summarize_source(entry.source)
        sources[entry.key] = replace(entry, source='')
        state['files'] = [asdict(sources[entry.key])]
    # Each round must add an API-resolved answer or complete. Source and graph
    # limits bound this continuation; this is not polling or a retry on failure.
    for _ in range(MAX_GRAPH_STEPS + 1):
        transfer = await encode_file_transfer(codec.encode_inputs, state)
        try:
            outcome = await compute(codec.OPERATION, transfer)
            result = await decode_file_result(outcome, codec.decode_results,
                digest=transfer.manifest.sha256, snapshot=state)
        finally:
            transfer.close()
        state['summaries'] = result['summaries']
        # Once parsed, raw bytes never need another transfer or API allocation.
        for key in state['summaries']:
            sources[key] = replace(sources[key], source='')
        if not result['requests']:
            return PreparedWorkflowGraph(result['analysis'], sources, result['summaries'],
                resolutions=tuple(tuple(item) for item in state['resolutions']))
        raw_bytes = 0
        for key, module, level in result['requests']:
            resolved = await resolve(sources[key], module, level)
            if resolved is not None:
                previous = sources.get(resolved.key)
                if previous is not None and (previous.path, previous.content_hash, previous.kind) != (resolved.path, resolved.content_hash, resolved.kind):
                    raise ValueError('Dependency changed while capturing workflow graph')
                if previous is None:
                    if len(sources) >= MAX_GRAPH_STEPS + 1:
                        raise ValueError('Workflow graph source budget exceeded')
                    size = len(resolved.source.encode())
                    if raw_bytes and raw_bytes + size > codec.MAX_ROUND_SOURCE_BYTES:
                        break
                    raw_bytes += size
                    if size > MAX_SOURCE_BYTES:
                        state['summaries'][resolved.key] = summarize_source(resolved.source)
                        resolved = replace(resolved, source='')
                    sources[resolved.key] = resolved
            state['resolutions'].append([key, module, level, resolved.key if resolved else None])
            if len(state['resolutions']) > MAX_GRAPH_STEPS:
                raise ValueError('Workflow graph resolution budget exceeded')
        state['files'] = [asdict(s) for s in sources.values()]
    raise ValueError('Workflow graph did not complete within its continuation budget')
