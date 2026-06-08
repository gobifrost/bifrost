"""Re-discover local functions when a workspace .py file changes."""
from __future__ import annotations

from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git"}


class _PyChangeHandler(FileSystemEventHandler):
    def __init__(self, host) -> None:
        self._host = host

    def _maybe_reload(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        path = str(getattr(event, "src_path", ""))
        if not path.endswith(".py"):
            return
        if any(f"/{d}/" in path for d in _SKIP_DIRS):
            return
        self._host.reload()

    on_modified = _maybe_reload
    on_created = _maybe_reload
    on_moved = _maybe_reload


def start_function_watch(workspace: Path, host) -> Observer:
    observer = Observer()
    observer.schedule(_PyChangeHandler(host), str(workspace), recursive=True)
    observer.start()
    return observer
