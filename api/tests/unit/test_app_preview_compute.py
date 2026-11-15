"""Source-only per-file compiler transport and current-owner publication."""
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from shared import app_preview_compute as codec
from shared.file_compute import FileComputeResult
from shared.worker_transfer import FileTransferManifest, ReceivedFileTransfer
from src.jobs.platform.base import PlatformJobCancelled
from src.services.app_compiler import AppCompilerInfrastructureError, CompileResult
from src.worker.remote import app_preview_compile as worker


def _received(transfer):
    return ReceivedFileTransfer(transfer.manifest, transfer.path)


@pytest.mark.asyncio
async def test_fixed_recipe_compiles_only_source_and_binds_output(tmp_path):
    operation = worker.app_preview_operation()
    assert operation.name == codec.OPERATION
    assert operation.workspace_max_bytes == codec.MAX_RESULT_BYTES
    assert not ({"BIFROST_DATABASE_URL", "BIFROST_S3_SECRET_KEY", "BIFROST_API_KEY", "CLOUDFLARE_API_TOKEN"} & operation.child().kwargs["env"].keys())
    files = [{"path": "pages/index.tsx", "source": "export default function Page() {return <div>Hello</div>}"}]
    with codec.encode_inputs(files) as transfer:
        assert codec.decode_inputs(_received(transfer)) == files
        output = tmp_path / 'result.json'
        receipt = await worker._preview_instruction({"input_path": str(transfer.path),
            "input_manifest": transfer.manifest.to_dict(), "output_path": str(output)})
        assert receipt['state'] == 'succeeded'
        manifest = FileTransferManifest.from_dict(receipt['result_manifest'], max_total_size=codec.MAX_RESULT_BYTES)
        with ReceivedFileTransfer(manifest, output) as result:
            decoded = codec.decode_results(result, input_digest=transfer.manifest.sha256, expected_paths={'pages/index.tsx'})
        assert decoded[0].success and 'Hello' in decoded[0].compiled and '<div>' not in decoded[0].compiled


@pytest.mark.parametrize('path', ['../leak.ts', '/absolute.ts', 'config.env', 'a\\b.ts'])
def test_inputs_reject_non_source_paths(path):
    with pytest.raises(ValueError):
        codec.encode_inputs([{'path': path, 'source': 'export default null'}])


@pytest.mark.parametrize('fault', ['digest', 'path', 'duplicate', 'missing', 'output', 'exports'])
def test_result_rejects_wrong_binding_or_incomplete_compilation(fault):
    results = [CompileResult('a.ts', True, 'compiled')]
    digest = 'a' * 64
    if fault == 'path':
        results[0].path = 'b.ts'
    if fault == 'duplicate':
        results *= 2
    if fault == 'missing':
        results = []
    if fault == 'output':
        results[0].compiled = None
    if fault == 'exports':
        results[0].named_exports = [1]
    with codec.encode_results(results, digest) as transfer:
        with pytest.raises(AppCompilerInfrastructureError):
            codec.decode_results(_received(transfer), input_digest='b'*64 if fault == 'digest' else digest, expected_paths={'a.ts'})


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [PlatformJobCancelled, asyncio.CancelledError])
async def test_compute_cancellation_closes_source_transfer(failure):
    captured = []
    async def compute(operation, transfer):
        assert operation == codec.OPERATION
        captured.append(transfer.path)
        raise failure
    with pytest.raises(failure):
        await codec.compile_preview_using([{'path': 'a.ts', 'source': 'export default null'}], compute)
    assert captured and not captured[0].exists()


@pytest.mark.asyncio
async def test_remote_preview_owner_loss_preserves_existing_artifacts(monkeypatch):
    from tests.unit.test_app_storage_preview_sync import _PreviewClient, _storage
    client = _PreviewClient(b'export default null')
    storage = _storage(client)
    async def compute(operation, transfer):
        result = codec.encode_results([CompileResult('pages/index.tsx', True, 'compiled')], transfer.manifest.sha256)
        return FileComputeResult(_received(result), {})
    @asynccontextmanager
    async def lost_owner():
        raise PlatformJobCancelled
        yield
    monkeypatch.setattr('src.services.app_compiler.AppCompilerService.compile_batch', AsyncMock(side_effect=AssertionError('API compiler must not run')))
    with pytest.raises(PlatformJobCancelled):
        await storage.sync_preview_compiled('app-1', 'apps/demo', file_compute=compute, publish_authorization=lost_owner)
    client.write.assert_not_awaited()
    client.delete.assert_not_awaited()
    storage.invalidate_render_cache.assert_not_awaited()


def test_transfer_digest_rejects_corrupted_source():
    with codec.encode_inputs([{'path': 'a.ts', 'source': 'export default null'}]) as transfer:
        body = transfer.path.read_bytes()
        transfer.path.write_bytes(body.replace(b'null', b'true'))
        with pytest.raises(ValueError, match='digest'):
            codec.decode_inputs(_received(transfer))


def test_result_output_limit_is_enforced(monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(codec, 'DEFAULT_APP_ARTIFACT_LIMITS',
        replace(codec.DEFAULT_APP_ARTIFACT_LIMITS, max_file_bytes=2))
    with codec.encode_results([CompileResult('a.ts', True, 'large')], 'a'*64) as transfer:
        with pytest.raises(AppCompilerInfrastructureError, match='artifact limits'):
            codec.decode_results(_received(transfer), input_digest='a'*64, expected_paths={'a.ts'})
