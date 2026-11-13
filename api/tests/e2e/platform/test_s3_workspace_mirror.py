"""One real-storage round trip through the worker's subprocess boundary (no jobs)."""
import asyncio
import os
import sys
from uuid import uuid4

import pytest

from src.config import get_settings
from src.core.storage.s3 import S3ObjectStore
from src.core.storage.s3_mirror import S3WorkspaceMirror


@pytest.mark.e2e
async def test_s3_workspace_round_trip_from_worker_subprocess(tmp_path):
    settings = get_settings()
    prefix = f'tests/workspace-mirror/{uuid4().hex}/'
    source = tmp_path / 'source'
    source.mkdir()
    (source / '.git').mkdir()
    (source / '.git/HEAD').write_bytes(b'ref: refs/heads/main\n')
    (source / 'café + %.txt').write_bytes(b'v1')
    (source / 'multipart.bin').write_bytes(b'chunk' * (2 * 1024 * 1024))
    mirror = S3WorkspaceMirror(settings)
    try:
        # Launch the same interpreter/environment used by execution subprocesses.
        # This catches accidentally retaining a dependency on an `aws` executable.
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c',
            'import asyncio,sys; from pathlib import Path; '
            'from src.config import get_settings; '
            'from src.core.storage.s3_mirror import S3WorkspaceMirror; '
            'asyncio.run(S3WorkspaceMirror(get_settings()).upload(Path(sys.argv[1]), sys.argv[2]))',
            str(source), prefix, env={**os.environ, 'PATH': '/nonexistent'},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        assert process.returncode == 0, (stdout.decode(), stderr.decode())
        target = tmp_path / 'target'
        await mirror.download(prefix, target)
        assert (target / '.git/HEAD').read_bytes() == (source / '.git/HEAD').read_bytes()
        assert (target / 'multipart.bin').read_bytes() == (source / 'multipart.bin').read_bytes()
        assert (target / 'café + %.txt').read_bytes() == b'v1'
        (source / 'café + %.txt').write_bytes(b'v2')
        (source / '.git/HEAD').unlink()
        await mirror.upload(source, prefix)
        await mirror.download(prefix, target)
        assert (target / 'café + %.txt').read_bytes() == b'v2'
        assert not (target / '.git/HEAD').exists()
    finally:
        await mirror.delete(prefix)
    async with S3ObjectStore.from_settings(settings) as store:
        page = await store.list_page(prefix)
        assert not page.objects
