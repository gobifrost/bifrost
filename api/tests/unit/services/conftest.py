"""
Pytest fixtures for service unit tests.

Provides mocks for:
- OAuth storage and connection testing
- Workspace operations
- Common test data
"""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
def sample_logs():
    """Sample execution logs"""
    return [
        {
            "timestamp": "2024-01-01T10:00:00Z",
            "level": "INFO",
            "message": "Execution started",
            "data": {}
        },
        {
            "timestamp": "2024-01-01T10:00:01Z",
            "level": "INFO",
            "message": "Processing item 1",
            "data": {"item": 1}
        },
        {
            "timestamp": "2024-01-01T10:00:02Z",
            "level": "INFO",
            "message": "Execution completed",
            "data": {"result": "success"}
        }
    ]


@pytest.fixture
def sample_execution_result():
    """Sample execution result data"""
    return {
        "status": "completed",
        "duration_seconds": 15.5,
        "items_processed": 100,
        "errors": 0,
        "warnings": 2
    }


@pytest.fixture
def temp_test_dir(tmp_path):
    """Provides a temporary directory for file operations tests"""
    return tmp_path


# ====================  OAuth Storage Service Fixtures ====================


@pytest.fixture
def mock_table_service():
    """Mock AsyncTableStorageService for OAuth storage"""
    with patch("src.services.oauth_storage.AsyncTableStorageService") as mock:
        instance = AsyncMock()
        instance.insert_entity = AsyncMock()  # Async
        instance.get_entity = AsyncMock()     # Async
        instance.upsert_entity = AsyncMock()  # Async
        instance.delete_entity = AsyncMock()  # Async
        instance.query_entities = AsyncMock() # Async
        mock.return_value = instance
        yield instance


@pytest.fixture
def sample_oauth_connection():
    """Sample OAuth connection data for testing"""
    from datetime import datetime, timezone
    return {
        "connection_id": "conn-123",
        "connection_name": "test_connection",
        "name": "Test Connection",
        "oauth_flow_type": "authorization_code",
        "client_id": "client-id-123",
        "client_secret": "client-secret-456",
        "org_id": "org-123",
        "authorization_url": "https://oauth.example.com/authorize",
        "token_url": "https://oauth.example.com/token",
        "scopes": "openid profile email",
        "redirect_uri": "/oauth/callback/test_connection",
        "status": "not_connected",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": "user@example.com"
    }


@pytest.fixture
def sample_oauth_request():
    """Sample create OAuth connection request"""
    return {
        "connection_name": "test_connection",
        "description": "Test OAuth Connection",
        "oauth_flow_type": "authorization_code",
        "client_id": "client-123",
        "client_secret": "secret-456",
        "authorization_url": "https://oauth.example.com/authorize",
        "token_url": "https://oauth.example.com/token",
        "scopes": "openid profile email"
    }


@pytest.fixture
def sample_oauth_update_request():
    """Sample update OAuth connection request"""
    return {
        "client_id": "new-client-id",
        "client_secret": "new-secret",
        "authorization_url": "https://oauth.example.com/authorize",
        "token_url": "https://oauth.example.com/token",
        "scopes": "openid profile email offline_access"
    }


@pytest.fixture
def mock_config_table_response():
    """Sample Config table entity response"""
    from datetime import datetime, timezone
    import json
    return {
        "PartitionKey": "org-123",
        "RowKey": "config:oauth_test_connection_metadata",
        "Value": json.dumps({
            "oauth_flow_type": "authorization_code",
            "client_id": "client-123",
            "authorization_url": "https://oauth.example.com/authorize",
            "token_url": "https://oauth.example.com/token",
            "scopes": "openid profile email",
            "redirect_uri": "/oauth/callback/test_connection",
            "status": "not_connected"
        }),
        "Type": "json",
        "Description": "OAuth metadata for test_connection",
        "UpdatedAt": datetime.now(timezone.utc).isoformat(),
        "UpdatedBy": "user@example.com"
    }


# ====================  Workspace Service Fixtures ====================


@pytest.fixture
def workspace_test_data():
    """Sample workspace test data"""
    return {
        "workspace_path": "/workspace",
        "test_files": [
            "workflow1.py",
            "workflow2.py",
            "subdir/workflow3.py",
            "config.yaml"
        ],
        "file_contents": {
            "workflow1.py": b"def workflow_1():\n    print('Workflow 1')",
            "workflow2.py": b"def workflow_2():\n    print('Workflow 2')",
            "subdir/workflow3.py": b"def workflow_3():\n    print('Workflow 3')"
        }
    }


@pytest.fixture
def mock_shutil():
    """Mock shutil for directory operations"""
    with patch("shutil.rmtree") as mock_rmtree:
        yield {"rmtree": mock_rmtree}


# ====================  OAuth Token Fixtures ====================


@pytest.fixture
def sample_oauth_tokens():
    """Sample OAuth tokens for testing"""
    from datetime import datetime, timedelta, timezone
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    return {
        "access_token": "access_token_xyz123",
        "refresh_token": "refresh_token_xyz456",
        "token_type": "Bearer",
        "expires_at": expires_at,
        "expires_in": 3600
    }


@pytest.fixture
def sample_oauth_response_metadata():
    """Sample OAuth response metadata"""
    from datetime import datetime, timedelta, timezone
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    return {
        "access_token": "access_token_xyz123",
        "refresh_token": "refresh_token_xyz456",
        "token_type": "Bearer",
        "expires_at": expires_at.isoformat(),
        "scope": "openid profile email"
    }




# ====================  Agent Executor Fixtures ====================


@pytest.fixture(autouse=True)
def unknown_catalog_context_window():
    """Executor unit tests run on mock sessions that cannot serve the model
    catalog; treat every chain's window as unknown to the catalog."""
    with (
        patch(
            "src.services.agent_executor.chain_context_window",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.services.execution.autonomous_agent_executor.chain_context_window",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield
