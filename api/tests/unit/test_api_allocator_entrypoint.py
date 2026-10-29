"""API allocator startup contracts for the shared image entrypoint."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _repo_file(path: str) -> Path:
    """Locate a repository file in both source and test-runner mounts."""
    candidates = [
        Path("/app") / path,
        Path("/app") / path.removeprefix("api/"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(path)


ENTRYPOINT = _repo_file("api/entrypoint.sh")
ALLOCATOR_HELPER = _repo_file("api/scripts/api-allocator.sh")
UVICORN_SHIM = _repo_file("api/scripts/uvicorn-shim.sh")
DOCKERFILES = (_repo_file("api/Dockerfile"), _repo_file("api/Dockerfile.dev"))
DEV_COMPOSE = _repo_file("docker-compose.dev.yml")


def _write_command(bin_dir: Path, name: str) -> None:
    (bin_dir / name).write_text(
        "#!/bin/sh\n"
        "python3 - \"$@\" <<'PY'\n"
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['ENTRYPOINT_CAPTURE']).write_text(json.dumps({\n"
        "    'argv': os.sys.argv[1:],\n"
        "    'pythonmalloc': os.environ.get('PYTHONMALLOC'),\n"
        "    'ld_preload': os.environ.get('LD_PRELOAD'),\n"
        "    'malloc_conf': os.environ.get('MALLOC_CONF'),\n"
        "}))\n"
        "PY\n"
    )
    (bin_dir / name).chmod(0o755)


def _run_entrypoint(
    tmp_path: Path, command: list[str], *, extra_env: dict[str, str] | None = None
) -> dict[str, object]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "capture.json"
    command_path = Path(command[0])
    _write_command(bin_dir, command_path.name)
    (bin_dir / "id").write_text("#!/bin/sh\nprintf '1000\\n'\n")
    (bin_dir / "id").chmod(0o755)

    entrypoint_command = command.copy()
    if command_path.is_absolute():
        entrypoint_command[0] = str(bin_dir / command_path.name)

    environment = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ENTRYPOINT_CAPTURE": str(capture),
    }
    environment.update(extra_env or {})

    subprocess.run(
        ["sh", str(ENTRYPOINT), *entrypoint_command],
        check=True,
        env=environment,
    )
    return json.loads(capture.read_text())


def _run_dev_api_command(tmp_path: Path, command: str, *, enable_debug: str) -> dict[str, object]:
    """Run the development API shell command with non-networking executables."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    capture = tmp_path / "capture.json"
    _write_command(bin_dir, "python")
    _write_command(bin_dir, "uvicorn")
    (bin_dir / "id").write_text("#!/bin/sh\nprintf '1000\\n'\n")
    (bin_dir / "id").chmod(0o755)

    rendered_command = command.replace("$$", "$")
    subprocess.run(
        ["sh", str(ENTRYPOINT), "/bin/sh", "-c", rendered_command],
        check=True,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "ENTRYPOINT_CAPTURE": str(capture),
            "BIFROST_API_PROCESS": "1",
            "ENABLE_DEBUG": enable_debug,
        },
    )
    return json.loads(capture.read_text())


def _run_uvicorn_shim(
    tmp_path: Path, *, extra_env: dict[str, str] | None = None
) -> dict[str, object]:
    """Run the image's direct-Uvicorn path without the image entrypoint."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shutil.copyfile(UVICORN_SHIM, shim_dir / "uvicorn")
    (shim_dir / "uvicorn").chmod(0o755)
    shutil.copyfile(ALLOCATOR_HELPER, shim_dir / "api-allocator.sh")
    _write_command(shim_dir, "uvicorn-real")
    capture = tmp_path / "capture.json"
    environment = {"ENTRYPOINT_CAPTURE": str(capture)}
    environment.update(extra_env or {})

    subprocess.run(
        [str(shim_dir / "uvicorn"), "src.main:app", "--port", "8000"],
        check=True,
        env=environment,
    )
    return json.loads(capture.read_text())


@pytest.mark.parametrize("uvicorn", ["uvicorn", "/usr/local/bin/uvicorn"])
def test_uvicorn_startup_uses_the_api_allocator_configuration(
    tmp_path: Path, uvicorn: str
) -> None:
    captured = _run_entrypoint(tmp_path, [uvicorn, "src.main:app", "--port", "8000"])

    assert captured == {
        "argv": ["src.main:app", "--port", "8000"],
        "pythonmalloc": "malloc",
        "ld_preload": "libjemalloc.so.2",
        "malloc_conf": "background_thread:true,dirty_decay_ms:1000,muzzy_decay_ms:1000",
    }


def test_uvicorn_startup_preserves_operator_allocator_configuration(tmp_path: Path) -> None:
    captured = _run_entrypoint(
        tmp_path,
        ["uvicorn", "src.main:app"],
        extra_env={
            "PYTHONMALLOC": "debug",
            "LD_PRELOAD": "libinstrumentation.so",
            "MALLOC_CONF": "dirty_decay_ms:5000",
        },
    )

    assert captured == {
        "argv": ["src.main:app"],
        "pythonmalloc": "debug",
        "ld_preload": "libinstrumentation.so:libjemalloc.so.2",
        "malloc_conf": "dirty_decay_ms:5000",
    }


def test_direct_uvicorn_shim_configures_the_api_without_the_image_entrypoint(
    tmp_path: Path,
) -> None:
    captured = _run_uvicorn_shim(tmp_path)

    assert captured == {
        "argv": ["src.main:app", "--port", "8000"],
        "pythonmalloc": "malloc",
        "ld_preload": "libjemalloc.so.2",
        "malloc_conf": "background_thread:true,dirty_decay_ms:1000,muzzy_decay_ms:1000",
    }


@pytest.mark.parametrize(
    "preload",
    [
        "libinstrumentation.so:libjemalloc.so.2",
        "libinstrumentation.so libjemalloc.so.2",
        "libinstrumentation.so\tlibjemalloc.so.2",
        "libinstrumentation.so:/usr/lib/x86_64-linux-gnu/libjemalloc.so.2",
    ],
)
def test_direct_uvicorn_shim_preserves_operator_settings_without_duplicate_preload(
    tmp_path: Path, preload: str
) -> None:
    captured = _run_uvicorn_shim(
        tmp_path,
        extra_env={
            "PYTHONMALLOC": "debug",
            "MALLOC_CONF": "dirty_decay_ms:5000",
            "LD_PRELOAD": preload,
        },
    )

    assert captured == {
        "argv": ["src.main:app", "--port", "8000"],
        "pythonmalloc": "debug",
        "ld_preload": preload,
        "malloc_conf": "dirty_decay_ms:5000",
    }


@pytest.mark.parametrize(
    ("enable_debug", "expected_argv"),
    [
        (
            "false",
            [
                "src.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--reload",
                "--reload-dir",
                "/app/src",
                "--reload-dir",
                "/app/shared",
            ],
        ),
        (
            "true",
            [
                "-m",
                "debugpy",
                "--listen",
                "0.0.0.0:5678",
                "--wait-for-client",
                "-m",
                "uvicorn",
                "src.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--reload",
                "--reload-dir",
                "/app/src",
                "--reload-dir",
                "/app/shared",
            ],
        ),
    ],
)
def test_dev_api_shell_startup_receives_the_api_allocator_configuration(
    tmp_path: Path, enable_debug: str, expected_argv: list[str]
) -> None:
    compose = yaml.safe_load(DEV_COMPOSE.read_text())
    api = compose["services"]["api"]

    assert api["environment"]["BIFROST_API_PROCESS"] == "1"
    assert api["command"].lstrip().startswith("sh -c")

    captured = _run_dev_api_command(tmp_path, api["command"], enable_debug=enable_debug)

    assert captured == {
        "argv": expected_argv,
        "pythonmalloc": "malloc",
        "ld_preload": "libjemalloc.so.2",
        "malloc_conf": "background_thread:true,dirty_decay_ms:1000,muzzy_decay_ms:1000",
    }


@pytest.mark.parametrize(
    ("command", "expected_argv"),
    [
        (["python", "-m", "scripts.init_container"], ["-m", "scripts.init_container"]),
        (["python", "-m", "src.worker.main"], ["-m", "src.worker.main"]),
        (["bifrost", "workflows", "list"], ["workflows", "list"]),
    ],
)
def test_non_api_commands_do_not_receive_api_allocator_configuration(
    tmp_path: Path, command: list[str], expected_argv: list[str]
) -> None:
    captured = _run_entrypoint(tmp_path, command)

    assert captured == {
        "argv": expected_argv,
        "pythonmalloc": None,
        "ld_preload": None,
        "malloc_conf": None,
    }


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_api_runtime_image_installs_jemalloc(dockerfile: Path) -> None:
    source = dockerfile.read_text()

    runtime = source.split("# Install runtime dependencies", 1)[1].split(
        "# Match the production", 1
    )[0]
    assert "libjemalloc2" in runtime
    assert "uvicorn-real" in source
    assert "api/scripts/api-allocator.sh" in source
    assert "api/scripts/uvicorn-shim.sh" in source
