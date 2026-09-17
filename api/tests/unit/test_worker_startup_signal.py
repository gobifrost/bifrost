"""Worker startup signal handling."""

import os
import signal
import subprocess
import sys


def test_worker_startup_sigterm_exits_during_blocked_app_import():
    """SIGTERM during heavy app import should exit instead of being ignored."""

    code = r'''
import importlib.abc
import importlib.machinery
import sys
import threading


class BlockingWorkerAppFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path, target=None):
        if fullname == "src.worker.app":
            return importlib.machinery.ModuleSpec(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        print("blocked-app-import", flush=True)
        threading.Event().wait()


sys.meta_path.insert(0, BlockingWorkerAppFinder())

from src.worker.main import run

run()
'''
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd="/app",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "blocked-app-import"
        os.kill(proc.pid, signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)

    assert proc.returncode == 0, stderr
