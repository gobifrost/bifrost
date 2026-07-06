from unittest.mock import AsyncMock, patch


async def test_fetch_module_index_lists_solution_python_paths_when_redis_cold():
    from src.routers.sdk_modules import fetch_module_index

    solution_id = "12345678-1234-5678-1234-567812345678"
    solution_storage = AsyncMock()
    solution_storage.list.return_value = [
        "workflows/triage.py",
        "modules/helpers.py",
        "README.md",
    ]

    with (
        patch("src.routers.sdk_modules.get_all_module_paths", new_callable=AsyncMock, return_value=set()),
        patch("src.routers.sdk_modules.SolutionStorage", return_value=solution_storage) as storage_cls,
    ):
        response = await fetch_module_index(_user=object(), solution_id=solution_id)

    assert response.body
    assert response.status_code == 200
    assert response.media_type == "application/json"
    assert response.body.decode() == (
        '{"paths":["_solutions/12345678-1234-5678-1234-567812345678/modules/helpers.py",'
        '"_solutions/12345678-1234-5678-1234-567812345678/workflows/triage.py"]}'
    )
    storage_cls.assert_called_once_with(solution_id)
    solution_storage.list.assert_awaited_once_with("")
