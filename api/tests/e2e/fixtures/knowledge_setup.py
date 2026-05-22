"""
Knowledge Store E2E test fixtures.

Provides fixtures for testing knowledge store (RAG) endpoints.
Requires EMBEDDINGS_API_TEST_KEY environment variable for embedding generation.

Environment variables:
- EMBEDDINGS_API_TEST_KEY: OpenAI API key for embeddings (text-embedding-3-small)
"""

import logging
import os
from collections.abc import Generator
from typing import Any

import pytest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def embeddings_test_key() -> str | None:
    """Get embeddings test API key from environment."""
    return os.environ.get("EMBEDDINGS_AI_TEST_KEY")


@pytest.fixture(scope="session")
def embeddings_configured(embeddings_test_key) -> bool:
    """Check if embeddings are configured for testing."""
    return embeddings_test_key is not None and len(embeddings_test_key) > 0


@pytest.fixture(scope="function")
def knowledge_cleanup(
    e2e_client,
    platform_admin,
    org1,
) -> Generator[None, None, None]:
    """
    Clean up any knowledge entries after test.

    This fixture ensures tests start with clean state and
    cleans up any documents created during the test.
    """
    # Clean up any existing knowledge for org1 before test
    namespaces_to_clean = [
        "e2e-test",
        "e2e-isolation",
        "e2e-org1",
        "e2e-org2",
        "e2e-global",
        "e2e-chunking",
        "e2e-dedup",
    ]

    for ns in namespaces_to_clean:
        try:
            e2e_client.delete(
                f"/api/cli/knowledge/namespace/{ns}",
                headers=platform_admin.headers,
            )
        except Exception:
            pass  # Ignore if no namespace exists

    yield

    # Clean up after test
    for ns in namespaces_to_clean:
        try:
            e2e_client.delete(
                f"/api/cli/knowledge/namespace/{ns}",
                headers=platform_admin.headers,
            )
            logger.info(f"Cleaned up knowledge namespace: {ns}")
        except Exception:
            pass  # Ignore cleanup failures


@pytest.fixture(scope="function")
def embedding_config_setup(
    e2e_client,
    platform_admin,
    embeddings_test_key,
) -> Generator[dict[str, Any], None, None]:
    """
    Configure embedding provider for a test.

    Skips if EMBEDDINGS_API_TEST_KEY is not set.
    Sets up OpenAI as the embedding provider.

    Yields:
        dict with embedding config details
    """
    if not embeddings_test_key:
        pytest.skip("EMBEDDINGS_API_TEST_KEY not configured")

    config = {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "api_key": embeddings_test_key,
    }

    # Configure embedding provider
    response = e2e_client.post(
        "/api/admin/llm/embedding-config",
        json=config,
        headers=platform_admin.headers,
    )
    assert response.status_code == 200, f"Failed to configure embeddings: {response.text}"

    logger.info("Configured OpenAI embedding provider for test")
    yield config

    # Cleanup
    try:
        e2e_client.delete(
            "/api/admin/llm/embedding-config",
            headers=platform_admin.headers,
        )
        logger.info("Cleaned up embedding config")
    except Exception as e:
        logger.warning(f"Failed to cleanup embedding config: {e}")
