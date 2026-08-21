"""Static contract guards for the Cloudflare Builder runner image.

The runner is executed outside the API container, so these tests protect the
source-level contract that unit tests in ``src`` cannot exercise directly.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.models.contracts.sandbox_runner import SandboxBuilderTurnCompletion


def _find_repo_file(path: str) -> Path:
    candidates = [
        Path("/app") / path,
        Path(__file__).resolve().parents[3] / path,
    ]
    if path.startswith("api/"):
        candidates.insert(1, Path("/app") / path.removeprefix("api/"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"{path} not found in test harness mounts")


def _calls_in_async_function(module: ast.Module, name: str) -> set[str]:
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )
    calls: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
    return calls


def test_cloudflare_turn_uses_builder_turn_hydration_contract() -> None:
    runner_path = _find_repo_file("builder-runner/runner.py")
    tree = ast.parse(runner_path.read_text(encoding="utf-8"))

    run_turn_calls = _calls_in_async_function(tree, "run_turn")
    run_build_calls = _calls_in_async_function(tree, "run_build")

    assert "hydrate_builder_turn_workspace" in run_turn_calls
    assert "materialize_build_input" not in run_turn_calls
    assert "materialize_build_input" in run_build_calls


def test_cloudflare_turn_supports_secretless_workspace_broker_contract() -> None:
    runner_path = _find_repo_file("builder-runner/runner.py")
    runner_source = runner_path.read_text(encoding="utf-8")
    tree = ast.parse(runner_source)

    envelope = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Envelope"
    )
    envelope_fields = {
        target.id
        for stmt in envelope.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        for target in (stmt.target,)
    }
    assert {
        "runner_sandbox_id",
        "workspace_sandbox_id",
        "workspace_broker_url",
        "runner_allowed_hosts",
        "workspace_allowed_hosts",
    }.issubset(envelope_fields)

    run_turn_source = ast.get_source_segment(
        runner_source,
        next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_turn"
        ),
    )
    assert run_turn_source is not None
    assert "WorkspaceBrokerClient" in run_turn_source
    assert "broker.hydrate(" in run_turn_source
    assert "BROKER_SETUP_ATTEMPTS" in run_turn_source
    assert 'client.stream("/input")' in run_turn_source
    assert "_retryable_broker_setup_error" in run_turn_source

    run_with_workspace_source = ast.get_source_segment(
        runner_source,
        next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_run_turn_with_workspace"
        ),
    )
    assert run_with_workspace_source is not None
    assert "broker.execute_tool(" in run_with_workspace_source
    assert "broker.archive_to_callback(client)" in run_with_workspace_source
    assert "TEST_SOLUTION_BUILD_TOOL_ID" in run_with_workspace_source
    assert '"/workspace-build"' in run_with_workspace_source
    assert "SandboxBuilderWorkspaceBuildRequest" in run_with_workspace_source
    assert "SandboxBuilderWorkspaceBuildResult" in run_with_workspace_source
    assert "_sandbox_compute_ms(" in run_with_workspace_source

    main_source = ast.get_source_segment(
        runner_source,
        next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        ),
    )
    assert main_source is not None
    assert "--workspace-hydrate" in main_source
    assert "--workspace-tool" in main_source
    assert "--workspace-archive" in main_source
    assert "create_subprocess_exec" in runner_source
    assert "shell=True" not in runner_source
    assert "WORKSPACE_COMMAND_MAX_OUTPUT_BYTES" in runner_source
    assert "_read_limited_stream" in runner_source
    assert "start_new_session=True" in runner_source
    assert "os.killpg" in runner_source
    assert "process.communicate()" not in runner_source

    broker_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WorkspaceBrokerClient"
    )
    archive_method_source = ast.get_source_segment(
        runner_source,
        next(
            node
            for node in broker_class.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "archive_to_callback"
        ),
    )
    assert archive_method_source is not None
    assert "self.http.stream(" in archive_method_source
    assert "content=response.aiter_bytes" in archive_method_source
    assert "response.content" not in archive_method_source


def test_local_and_cloudflare_turns_delegate_semantic_loop_to_coordinator() -> None:
    runner_source = _find_repo_file("builder-runner/runner.py").read_text(
        encoding="utf-8"
    )
    agent_executor_source = _find_repo_file("api/src/services/agent_executor.py").read_text(
        encoding="utf-8"
    )

    semantic_event_names = (
        "AgentRunResultEvent",
        "FunctionToolCallEvent",
        "PartDeltaEvent",
        "PartStartEvent",
        "TextPartDelta",
        "UsageLimitExceeded",
    )
    assert "AgentTurnCoordinator(" in runner_source
    assert "AgentTurnCoordinator(" in agent_executor_source
    assert all(name not in runner_source for name in semantic_event_names)
    assert all(name not in agent_executor_source for name in semantic_event_names)


def test_cloudflare_runner_image_includes_shared_builder_archive_module() -> None:
    dockerfile = _find_repo_file("builder-runner/Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "COPY api/shared/ ./shared/" in dockerfile
    assert "ENV HOME=/tmp" in dockerfile
    assert "PYTHONPATH=/opt/bifrost-build" in dockerfile


def test_cloudflare_runner_probe_dependencies_cover_shared_runtime_imports() -> None:
    runner_source = _find_repo_file("builder-runner/runner.py").read_text(
        encoding="utf-8"
    )
    requirements_in = _find_repo_file("builder-runner/requirements.in").read_text(
        encoding="utf-8"
    )
    requirements_lock = _find_repo_file("builder-runner/requirements.lock").read_text(
        encoding="utf-8"
    )

    assert "from src.services.agent_runtime import" in runner_source
    assert "The shared AgentTurnCoordinator imports src.models.contracts" in requirements_in
    assert "email-validator==2.3.0" in requirements_in
    assert "sqlalchemy[asyncio]==2.0.49" in requirements_in
    assert "email-validator==2.3.0" in requirements_lock
    assert "sqlalchemy==2.0.49" in requirements_lock
    assert "greenlet==" in requirements_lock


def test_cloudflare_turn_reports_authoritative_sandbox_compute_duration() -> None:
    runner_source = _find_repo_file("builder-runner/runner.py").read_text(
        encoding="utf-8"
    )
    run_with_workspace_source = ast.get_source_segment(
        runner_source,
        next(
            node
            for node in ast.parse(runner_source).body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_run_turn_with_workspace"
        ),
    )
    compute_helper_source = ast.get_source_segment(
        runner_source,
        next(
            node
            for node in ast.parse(runner_source).body
            if isinstance(node, ast.FunctionDef) and node.name == "_sandbox_compute_ms"
        ),
    )
    assert run_with_workspace_source is not None
    assert compute_helper_source is not None

    assert '"sandbox_compute_ms"' in run_with_workspace_source
    assert "_sandbox_compute_ms(" in run_with_workspace_source
    assert "multiplier = 2 if envelope.broker_url() is not None else 1" in compute_helper_source
    assert "result.duration_ms" in run_with_workspace_source


def test_builder_turn_completion_accepts_sandbox_compute_duration() -> None:
    completion = SandboxBuilderTurnCompletion.model_validate(
        {
            "status": "succeeded",
            "output_sha256": "a" * 64,
            "final_text": "done",
            "sandbox_compute_ms": 123,
        }
    )

    assert completion.sandbox_compute_ms == 123
