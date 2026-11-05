"""Executable source cache updates precede fallible search-index maintenance."""
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.solutions.deploy import SolutionDeployer


@pytest.mark.parametrize("delete", [False, True])
async def test_source_cache_changes_survive_search_index_failure(delete):
    solution_id = uuid4()
    path = "workflows/current.py"
    source = "VALUE = 2\n"
    storage = AsyncMock()
    storage.list.return_value = [path] if delete else []
    storage.write.return_value = "source-hash"
    storage_key = f"_solutions/{solution_id}/{path}"
    storage._key = MagicMock(return_value=storage_key)
    index = AsyncMock()
    index.index_solution.side_effect = RuntimeError("index unavailable")
    index.unindex_solution.side_effect = RuntimeError("index unavailable")
    with (
        patch("src.services.solutions.deploy.SolutionStorage", return_value=storage),
        patch("src.services.file_index_service.FileIndexService", return_value=index),
        patch("src.core.module_cache.set_module", new_callable=AsyncMock) as cached,
        patch("src.core.module_cache.invalidate_module", new_callable=AsyncMock) as invalidated,
        pytest.raises(RuntimeError, match="index unavailable"),
    ):
        await SolutionDeployer(AsyncMock())._write_python(
            solution_id, {} if delete else {path: source},
        )

    if delete:
        storage.delete.assert_awaited_once_with(path)
        invalidated.assert_awaited_once_with(storage_key)
    else:
        storage.write.assert_awaited_once_with(path, source.encode())
        cached.assert_awaited_once_with(storage_key, source, "source-hash")
