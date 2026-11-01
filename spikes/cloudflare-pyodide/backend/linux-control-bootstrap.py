"""Live Linux reference preflight: signed hello + native templates, no dispatch."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import websockets


async def main():
    target = urlsplit(os.environ.get('BIFROST_DATABASE_URL', ''))
    if (os.environ.get('BIFROST_ENVIRONMENT') != 'testing' or os.environ.get('TEST_API_URL') != 'http://api:8000'
            or target.hostname != 'pgbouncer' or target.path != '/bifrost_test'):
        raise RuntimeError('Own disposable test stack required')
    from sqlalchemy import delete, func, select
    from src.core.database import init_db, close_db, get_db_context
    from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
    from src.services.worker_pool.claims import register_static_worker
    from src.worker.remote.identity import LauncherIdentity
    from src.worker.remote.launcher import Launcher, proc_stat
    from shared.version import get_version

    await init_db()
    worker_id = await register_static_worker(hostname='pyodide-linux-reference-preflight',
        incarnation_id=uuid4(), supported_handlers=[], version=get_version())
    report = {'runtime': 'actual-linux-launcher-bootstrap', 'cloud_invocations': 0,
              'workflow_invocations': 0, 'inventory_sent': False, 'execution_admission_qualified': False,
              'pyodide_physical_termination_verified': False, 'qualification_passed': False}
    launcher = None
    socket = None
    try:
        socket = await websockets.connect('ws://api:8000/api/worker-control', open_timeout=10, close_timeout=10)
        identity = LauncherIdentity(uuid4(), worker_id, None, None, 'pyodide-linux-reference-preflight', 'static')
        launcher = Launcher(socket, identity=identity, secret=os.environ['BIFROST_SECRET_KEY'])
        await launcher.hello({})
        report['signed_session_generation'] = launcher.session_generation
        await asyncio.wait_for(launcher.start(), timeout=45)
        pids = [launcher.template.pid, launcher.agent_template.pid]
        if any(not isinstance(pid, int) or pid <= 0 or proc_stat(pid) is None for pid in pids):
            raise RuntimeError('Native templates did not start')
        report['native_template_pids'] = pids
        report['baseline_bytes'] = launcher.baseline_bytes
        async with get_db_context() as db:
            worker = await db.get(ExecutionWorker, worker_id)
            assert worker is not None and worker.state == 'starting' and worker.ready_at is None
            assert worker.supported_handlers == []
            assert worker.remote_launcher_id == identity.launcher_id
            assert worker.remote_session_generation == launcher.session_generation
            claims = await db.scalar(select(func.count()).select_from(ExecutionWorkerClaim).where(
                ExecutionWorkerClaim.worker_id == worker_id))
            assert claims == 0
        assert not launcher.children and not launcher.jobs
        report['signed_owner_and_unadmitted_bootstrap_verified'] = True
        report['qualification_passed'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        if launcher is not None:
            await launcher.stop()
            report['native_templates_stopped'] = not launcher.template.is_alive() and not launcher.agent_template.is_alive()
        if socket is not None:
            await socket.close()
        async with get_db_context() as db:
            await db.execute(delete(ExecutionWorker).where(ExecutionWorker.id == worker_id))
        async with get_db_context() as db:
            assert await db.get(ExecutionWorker, worker_id) is None
        report['fixture_worker_cleanup_verified'] = True
        await close_db()
        report['cgroup'] = {key:Path('/sys/fs/cgroup/' + key).read_text() for key in ['memory.peak', 'memory.max', 'memory.events']}
        print(json.dumps(report))


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({'runtime': 'linux-bootstrap-error', 'error_type': type(error).__name__}))
        raise SystemExit(1)
