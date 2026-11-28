"""Real isolated Linux process ownership regression, without shared subreaper state."""
import asyncio
import json
import sys

import pytest


_PROBE = r"""
import asyncio,ctypes,json,os,signal,sys
from pathlib import Path
from src.worker.remote.process_cleanup import kill_process_group

if ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)!=0:
    raise RuntimeError('Cannot isolate adopted-child ownership')
child="import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready',flush=True); signal.pause()"
primary="import subprocess,sys,signal; child=subprocess.Popen([sys.executable,'-c',"+repr(child)+"],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True); assert child.stdout.readline().strip()=='ready'; print(child.pid,flush=True); "+("sys.exit(0)" if sys.argv[1]=='already_exited' else "signal.pause()")
async def main():
    unrelated=await asyncio.create_subprocess_exec(sys.executable,'-c','import signal; signal.pause()',start_new_session=True)
    process=await asyncio.create_subprocess_exec(sys.executable,'-c',primary,start_new_session=True,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    descendant=int(await asyncio.wait_for(process.stdout.readline(),10))
    try:
        if sys.argv[1]=='already_exited':await process.wait()
        await asyncio.wait_for(kill_process_group(process),10)
        assert process.returncode is not None
        assert not (Path('/proc')/str(descendant)).exists(),'Owned descendant remains after cleanup'
        os.kill(unrelated.pid,0)
        assert unrelated.returncode is None,'Cleanup consumed an unrelated child'
        print(json.dumps({'owned_group_gone':True,'unrelated_child_preserved':True}))
    finally:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        await process.wait()
        try:os.waitpid(descendant,0)
        except ChildProcessError:pass
        unrelated.kill()
        assert await unrelated.wait()==-signal.SIGKILL
asyncio.run(main())
"""


@pytest.mark.parametrize("primary_state", ["exits_on_term", "already_exited"])
async def test_cleanup_removes_owned_descendants_and_preserves_other_groups(primary_state):
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _PROBE, primary_state,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
    assert process.returncode == 0, stderr.decode()
    assert json.loads(stdout) == {
        "owned_group_gone": True, "unrelated_child_preserved": True,
    }
