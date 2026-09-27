"""Slice 1: @service discovery, type plumbing, and one-shot guards.

Covers decorator metadata (both SDK copies), entity detection, AST parsing,
indexer enrichment, package exports, manifest round-trip, and the
run_workflow one-shot guard. All tests are IO-free (mocked DB/Redis).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy import Update


SAMPLE_SERVICE = '''
from bifrost import service

@service(name="Telegram Bridge")
async def telegram_bridge():
    """Bridge Telegram messages into Bifrost events."""
    pass
'''

SAMPLE_SERVICE_BARE = '''
from bifrost import service

@service
async def mqtt_bridge():
    pass
'''


# --- Decorators (both SDK copies) ---


@pytest.mark.parametrize("module_path", ["src.sdk.decorators", "bifrost.decorators"])
def test_service_decorator_sets_service_type(module_path):
    """@service attaches _executable_metadata with type='service'."""
    mod = pytest.importorskip(module_path)

    @mod.service(name="My Service", category="Comms", tags=["telegram"])
    async def my_bridge():
        """Bridge things."""
        pass

    meta = my_bridge._executable_metadata
    assert meta.type == "service"
    assert meta.name == "My Service"
    assert meta.description == "Bridge things."
    assert meta.category == "Comms"
    assert meta.tags == ["telegram"]


@pytest.mark.parametrize("module_path", ["src.sdk.decorators", "bifrost.decorators"])
def test_service_decorator_bare_form_uses_function_name(module_path):
    """@service without parentheses derives identity from the function."""
    mod = pytest.importorskip(module_path)

    @mod.service
    async def mqtt_bridge():
        """MQTT bridge."""
        pass

    meta = mqtt_bridge._executable_metadata
    assert meta.type == "service"
    assert meta.name == "mqtt_bridge"


@pytest.mark.parametrize("module_path", ["src.sdk.decorators", "bifrost.decorators"])
def test_service_decorator_ignores_unknown_params(module_path):
    """Unknown @service params warn (not raise) for backwards compatibility."""
    mod = pytest.importorskip(module_path)

    @mod.service(name="X", restart="always")
    async def my_bridge():
        pass

    assert my_bridge._executable_metadata.type == "service"


def test_service_exported_from_both_packages():
    """`from src.sdk import service` and `from bifrost import service` work."""
    from src.sdk import service as sdk_service
    from src.sdk.decorators import service as sdk_service_direct

    assert sdk_service is sdk_service_direct

    import bifrost

    assert callable(bifrost.service)


def test_executable_type_literal_includes_service():
    """The single ExecutableType literal source admits 'service'."""
    from src.services.execution.module_loader import ExecutableType
    from typing import get_args

    assert "service" in get_args(ExecutableType)


def test_contracts_executable_type_includes_service():
    """API contract enum admits SERVICE."""
    from src.models.contracts.workflows import ExecutableType

    assert ExecutableType.SERVICE.value == "service"


# --- Entity detection ---


def test_entity_detector_classifies_service_file():
    """A @service-only file is detected as executable source, not a module."""
    from src.services.file_storage.entity_detector import (
        detect_python_entity_type_with_ast,
    )

    result = detect_python_entity_type_with_ast(SAMPLE_SERVICE.encode())
    assert result.entity_type == "workflow"
    assert result.has_decorators is True


def test_entity_detector_ignores_service_in_comments():
    """@service mentioned only in comments is not executable code."""
    from src.services.file_storage.entity_detector import (
        detect_python_entity_type_with_ast,
    )

    content = b"# use @service here\ndef helper():\n    pass\n"
    result = detect_python_entity_type_with_ast(content)
    assert result.entity_type == "module"


def test_ast_parser_accepts_service_forms():
    """Bare and called @service forms parse (parity with @workflow)."""
    import ast as py_ast

    from src.services.file_storage.ast_parser import ASTMetadataParser

    parser = ASTMetadataParser()

    assert parser.parse_decorator(
        py_ast.parse("@service\ndef f(): pass").body[0].decorator_list[0]
    ) == ("service", {})
    parsed = parser.parse_decorator(
        py_ast.parse('@service(name="X")\ndef f(): pass').body[0].decorator_list[0]
    )
    assert parsed is not None
    name, kwargs = parsed
    assert name == "service"
    assert kwargs == {"name": "X"}


# --- Indexer enrichment ---


def _mock_db_with_existing(existing):
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing
    mock_result.scalar_one.return_value = existing
    mock_db.execute.return_value = mock_result
    return mock_db


def _existing_workflow_row():
    row = MagicMock()
    row.id = uuid4()
    row.is_active = True
    row.endpoint_enabled = False
    row.name = "Telegram Bridge"
    row.path = "workflows/telegram.py"
    row.description = None
    row.category = "General"
    row.tags = []
    row.timeout_seconds = 1800
    row.time_saved = 0
    row.value = 0.0
    row.execution_mode = "async"
    row.type = "workflow"
    return row


@pytest.mark.asyncio
async def test_indexer_enriches_service_with_service_type():
    """@service content updates the existing row with type='service'."""
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    existing = _existing_workflow_row()
    indexer = WorkflowIndexer(_mock_db_with_existing(existing))
    await indexer.index_python_file("workflows/telegram.py", SAMPLE_SERVICE.encode())

    updates = [
        call[0][0]
        for call in indexer.db.execute.call_args_list
        if isinstance(call[0][0], Update)
    ]
    assert updates, "Expected an UPDATE for the registered service"
    params = updates[0].compile().params
    assert params.get("type") == "service"
    assert params.get("is_active") is True


@pytest.mark.asyncio
async def test_indexer_skips_unregistered_service():
    """Enrich-only: unregistered @service functions never INSERT."""
    from sqlalchemy import Insert

    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    indexer = WorkflowIndexer(mock_db)
    await indexer.index_python_file("workflows/new.py", SAMPLE_SERVICE.encode())

    for call in mock_db.execute.call_args_list:
        assert not isinstance(call[0][0], Insert)


@pytest.mark.asyncio
async def test_indexer_never_refreshes_endpoint_for_service():
    """A service row with endpoint_enabled must not register an HTTP route."""
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    existing = _existing_workflow_row()
    existing.type = "service"
    existing.endpoint_enabled = True
    indexer = WorkflowIndexer(_mock_db_with_existing(existing))
    indexer.refresh_workflow_endpoint = MagicMock()

    await indexer.index_python_file("workflows/telegram.py", SAMPLE_SERVICE.encode())

    indexer.refresh_workflow_endpoint.assert_not_called()


# --- Manifest round-trip ---


def test_manifest_workflow_service_type_round_trips():
    """ManifestWorkflow admits type='service' and preserves it for import."""
    from bifrost.manifest import ManifestWorkflow
    from bifrost.manifest_codec import Destination

    mwf = ManifestWorkflow(
        id=str(uuid4()),
        name="telegram_bridge",
        path="workflows/telegram.py",
        function_name="telegram_bridge",
        type="service",
    )
    assert mwf.to_orm_values(Destination.GIT_SYNC).direct["type"] == "service"


def test_manifest_workflow_rejects_unknown_type():
    """Misspelled executable types fail loudly at manifest parse time."""
    import pydantic

    from bifrost.manifest import ManifestWorkflow

    with pytest.raises(pydantic.ValidationError):
        ManifestWorkflow(
            id=str(uuid4()),
            name="x",
            path="workflows/x.py",
            function_name="x",
            type="daemon",
        )


# --- One-shot execution guard ---


def _execution_context():
    from bifrost._execution_context import ExecutionContext

    return ExecutionContext(
        user_id="u1",
        email="u@example.com",
        name="U",
        scope="GLOBAL",
        organization=None,
        is_platform_admin=False,
        is_function_key=False,
        execution_id=str(uuid4()),
    )


@pytest.mark.asyncio
async def test_run_workflow_rejects_service_via_dispatch_metadata():
    """Dispatch-time metadata with type='service' fails before enqueue (no IO)."""
    from src.services.execution.service import run_workflow

    with pytest.raises(ValueError, match="long-lived service"):
        await run_workflow(
            context=_execution_context(),
            workflow_id=str(uuid4()),
            dispatch_metadata={
                "name": "telegram_bridge",
                "timeout_seconds": 1800,
                "type": "service",
            },
        )


@pytest.mark.asyncio
async def test_run_workflow_rejects_service_via_metadata_cache(monkeypatch):
    """Cache-validated services fail before enqueue (Redis mocked, no DB)."""
    from src.services.execution import service as execution_service

    class FakeRedis:
        async def get_workflow_metadata_cache(self, workflow_id):
            return {
                "id": workflow_id,
                "name": "telegram_bridge",
                "file_path": "workflows/telegram.py",
                "timeout_seconds": 1800,
                "time_saved": 0,
                "value": 0.0,
                "execution_mode": "async",
                "type": "service",
            }

    # get_workflow_metadata_only resolves get_redis_client lazily from
    # src.core.redis_client, so patch the source attribute.
    import src.core.redis_client

    monkeypatch.setattr(src.core.redis_client, "get_redis_client", lambda: FakeRedis())

    with pytest.raises(ValueError, match="long-lived service"):
        await execution_service.run_workflow(
            context=_execution_context(),
            workflow_id=str(uuid4()),
        )


# --- Indexer lifecycle sync (Codex review fix) ---


SAMPLE_WORKFLOW_SAME_FN = '''
from bifrost import workflow

@workflow(name="Telegram Bridge")
async def telegram_bridge():
    """Bridge Telegram messages into Bifrost events."""
    pass
'''


def _service_row(existing_type="service"):
    row = MagicMock()
    row.id = uuid4()
    row.is_active = True
    row.endpoint_enabled = False
    row.name = "Telegram Bridge"
    row.path = "workflows/telegram.py"
    row.description = None
    row.category = "General"
    row.tags = []
    row.timeout_seconds = 1800
    row.time_saved = 0
    row.value = 0.0
    row.execution_mode = "async"
    row.type = existing_type
    return row


def _result(scalar_one_or_none=None, scalar_one=None, first=None):
    res = MagicMock()
    res.scalar_one_or_none.return_value = scalar_one_or_none
    if scalar_one is not None:
        res.scalar_one.return_value = scalar_one
    if first is not None:
        res.scalars.return_value.first.return_value = first
    return res


@pytest.mark.asyncio
async def test_indexer_ensures_definition_and_stamps_revision():
    """@service content ensures a definition and pins the source revision."""
    import hashlib

    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    row = _service_row("service")
    definition = MagicMock()
    definition.current_revision = "old"

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            _result(scalar_one_or_none=row),  # workflow lookup
            _result(scalar_one_or_none=definition),  # definition lookup
            _result(),  # UPDATE
            _result(scalar_one=row),  # re-fetch
        ]
    )

    indexer = WorkflowIndexer(mock_db)
    await indexer.index_python_file("workflows/telegram.py", SAMPLE_SERVICE.encode())

    expected = hashlib.sha256(SAMPLE_SERVICE.encode()).hexdigest()
    assert definition.current_revision == expected


@pytest.mark.asyncio
async def test_indexer_creates_missing_definition():
    """Files indexed outside registration still get a definition."""
    from src.models.orm.services import ServiceDefinition
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    row = _service_row("service")
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            _result(scalar_one_or_none=row),
            _result(scalar_one_or_none=None),  # no definition yet
            _result(),
            _result(scalar_one=row),
        ]
    )

    indexer = WorkflowIndexer(mock_db)
    await indexer.index_python_file("workflows/telegram.py", SAMPLE_SERVICE.encode())

    added = [call[0][0] for call in mock_db.add.call_args_list]
    assert any(isinstance(a, ServiceDefinition) for a in added)


@pytest.mark.asyncio
async def test_indexer_parks_definition_on_type_change():
    """A row converting away from @service is parked, history retained."""
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    row = _service_row("service")
    definition = MagicMock()
    definition.enabled = True

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(
        side_effect=[
            _result(scalar_one_or_none=row),  # workflow lookup
            _result(scalar_one_or_none=definition),  # definition lookup
            _result(first=None),  # locked live-attempt lookup: none live
            _result(),  # UPDATE
            _result(scalar_one=row),  # re-fetch
        ]
    )

    indexer = WorkflowIndexer(mock_db)
    await indexer.index_python_file("workflows/telegram.py", SAMPLE_WORKFLOW_SAME_FN.encode())

    assert definition.enabled is False
    assert definition.desired_state == "stopped"
