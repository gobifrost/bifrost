from pathlib import Path

from bifrost.solution_dev.reload import _PyChangeHandler


class _RecordingHost:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1

    def refs(self):
        return ["functions/hello.py::main"]

    def failures(self):
        return {}


def test_handler_reloads_on_py_change(tmp_path: Path):
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = str(tmp_path / "functions/hello.py")

    handler.on_modified(_Evt())
    assert host.reloads == 1


def test_handler_ignores_non_py(tmp_path: Path):
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = str(tmp_path / "src/App.tsx")

    handler.on_modified(_Evt())
    assert host.reloads == 0


def test_handler_skips_skip_dirs_on_windows_paths():
    # watchdog emits native (backslash) paths on Windows; a "/{d}/" substring
    # check never matches them → .venv churn triggers reload storms.
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = r"C:\ws\.venv\Lib\site-packages\x.py"

    handler.on_modified(_Evt())
    assert host.reloads == 0


def test_handler_skips_bifrost_dir():
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/.bifrost/state.py"

    handler.on_modified(_Evt())
    assert host.reloads == 0


def test_handler_still_reloads_normal_workspace_py():
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/workflows/x.py"

    handler.on_modified(_Evt())
    assert host.reloads == 1


def test_handler_reloads_on_workflow_manifest_change():
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/.bifrost/workflows.yaml"

    handler.on_modified(_Evt())
    assert host.reloads == 1


def test_handler_ignores_other_manifests():
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/.bifrost/apps.yaml"

    handler.on_modified(_Evt())
    assert host.reloads == 0


def test_handler_echoes_reload_summary(capsys):
    host = _RecordingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/functions/hello.py"

    handler.on_modified(_Evt())
    out = capsys.readouterr().out
    assert "1 local function(s)" in out


def test_handler_echoes_import_failures(capsys):
    class _FailingHost(_RecordingHost):
        def refs(self):
            return []

        def failures(self):
            return {"functions/broken.py": "ImportError: boom"}

    host = _FailingHost()
    handler = _PyChangeHandler(host)

    class _Evt:
        is_directory = False
        src_path = "/ws/functions/broken.py"

    handler.on_modified(_Evt())
    err = capsys.readouterr().err
    assert "functions/broken.py" in err
    assert "boom" in err
