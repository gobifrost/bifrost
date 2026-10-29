"""A marked execution child never reads parent Redis or S3 for source."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx

import src.core.module_cache_sync as module_cache_sync


def test_marked_network_child_uses_scoped_engine_transport_without_redis_or_s3(monkeypatch) -> None:
    from bifrost import client as bifrost_client

    engine = MagicMock()
    engine.engine_request_sync.side_effect = [
        httpx.Response(
            200,
            json={
                "kind": "module",
                "path": "modules/tool.py",
                "storage_path": "_solutions/sol-1/modules/tool.py",
                "content": "VALUE = 1\n",
                "hash": "abc",
            },
        ),
        httpx.Response(
            200,
            json={"content": "VALUE = 1\n", "path": "modules/tool.py", "hash": "abc"},
        ),
    ]
    monkeypatch.setattr(module_cache_sync, "_execution_child", False)
    monkeypatch.setattr(bifrost_client, "get_engine_socket_path", lambda: None)
    monkeypatch.setattr(bifrost_client, "get_client", lambda: engine)
    monkeypatch.setattr(
        module_cache_sync,
        "_get_sync_redis",
        lambda: (_ for _ in ()).throw(AssertionError("child must not read Redis")),
    )
    monkeypatch.setattr(
        module_cache_sync,
        "_get_s3_module",
        lambda _: (_ for _ in ()).throw(AssertionError("child must not read S3")),
    )

    module_cache_sync.mark_execution_child()
    module_cache_sync.set_solution_context("sol-1", global_repo_access=False)
    try:
        resolution = module_cache_sync.resolve_module_sync("modules.tool")
        module = module_cache_sync.get_module_sync("modules/tool.py")
    finally:
        module_cache_sync.clear_solution_context()

    assert resolution.storage_path == "_solutions/sol-1/modules/tool.py"
    assert module is not None and module["content"] == "VALUE = 1\n"
    assert engine.engine_request_sync.call_args_list[0].args == (
        "GET",
        "/api/sdk/modules-resolve",
    )
    assert engine.engine_request_sync.call_args_list[0].kwargs == {
        "params": {
            "name": "modules.tool",
            "solution_id": "sol-1",
            "global_repo_access": False,
        }
    }
    assert engine.engine_request_sync.call_args_list[1].args == (
        "GET",
        "/api/sdk/modules/_solutions/sol-1/modules/tool.py",
    )
