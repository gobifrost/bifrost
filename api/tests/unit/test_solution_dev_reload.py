from pathlib import Path

from bifrost.solution_dev.reload import _PyChangeHandler


class _RecordingHost:
    def __init__(self):
        self.reloads = 0

    def reload(self):
        self.reloads += 1


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
