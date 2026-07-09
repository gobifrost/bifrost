"""Re-discover local functions when a workspace .py file (or the workflow
manifest) changes."""
from __future__ import annotations

import pathlib
from pathlib import Path

import click
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

_SKIP_DIRS = {"node_modules", "dist", ".venv", "venv", "__pycache__", ".git", ".bifrost"}


class _PyChangeHandler(FileSystemEventHandler):
    def __init__(self, host) -> None:
        self._host = host

    def _maybe_reload(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        path = str(getattr(event, "src_path", ""))
        # watchdog emits native paths; PureWindowsPath parses BOTH separators,
        # so these checks work for / and \ regardless of host platform.
        parts = pathlib.PureWindowsPath(path).parts
        # .bifrost/workflows.yaml feeds the name/UUID alias index — editing it
        # must re-resolve, or the dev loop serves stale aliases with no signal.
        is_workflow_manifest = (
            len(parts) >= 2 and parts[-2] == ".bifrost" and parts[-1] == "workflows.yaml"
        )
        if not is_workflow_manifest:
            if not path.endswith(".py"):
                return
            if any(part in _SKIP_DIRS for part in parts):
                return
        try:
            self._host.reload()
        except Exception as exc:  # noqa: BLE001 — an escaped error kills the watcher thread
            click.echo(f"  ⚠ reload failed: {type(exc).__name__}: {exc}", err=True)
            return
        click.echo(f"  reloaded — {len(self._host.refs())} local function(s)")
        for rel, err in sorted(self._host.failures().items()):
            click.echo(f"  ⚠ import error in {rel}: {err}", err=True)

    on_modified = _maybe_reload
    on_created = _maybe_reload
    on_moved = _maybe_reload


def start_function_watch(workspace: Path, host) -> BaseObserver:
    observer = Observer()
    observer.schedule(_PyChangeHandler(host), str(workspace), recursive=True)
    observer.start()
    return observer
