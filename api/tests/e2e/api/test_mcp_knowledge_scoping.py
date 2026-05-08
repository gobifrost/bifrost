"""
E2E: search_knowledge exposure and scoping over the MCP HTTP transport.

Two MCP mounts:

- POST /mcp                — un-scoped: tools/list and tool calls union
                              the user's accessible agents.
- POST /mcp/{agent_id}     — agent-scoped: tools/list and tool calls
                              are limited to that one agent.

Regression coverage:

1. search_knowledge is auto-injected into tools/list when an agent has
   knowledge_sources, even if system_tools doesn't list it (mirror of
   agent_helpers.py:140 behavior in native chat).
2. /mcp returns the union of namespaces the user can access.
3. /mcp/{agent_id} sees ONLY that agent's namespaces.
4. /mcp/{agent_id} rejects explicit cross-namespace queries — a user with
   broader token access cannot bypass the per-agent boundary by passing
   namespace="other_namespace" in the call args.

Skipped without ``EMBEDDINGS_AI_TEST_KEY`` since search_knowledge calls
the embedding provider.
"""

import logging
import os
import uuid

import pytest

logger = logging.getLogger(__name__)

EMBEDDINGS_AVAILABLE = bool(os.environ.get("EMBEDDINGS_AI_TEST_KEY"))
MCP_ACCEPT_HEADER = "application/json, text/event-stream"


def _mcp_post(e2e_client, url: str, headers: dict[str, str], body: dict) -> dict:
    """JSON-RPC over the MCP HTTP mount. Returns the parsed JSON body."""
    resp = e2e_client.post(
        url,
        json=body,
        headers={**headers, "Accept": MCP_ACCEPT_HEADER},
    )
    assert resp.status_code == 200, f"{url}: {resp.status_code} {resp.text}"
    return resp.json()


def _mcp_initialize(e2e_client, url: str, headers: dict[str, str]) -> None:
    """initialize is required before tools/list and tools/call."""
    payload = _mcp_post(
        e2e_client,
        url,
        headers,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        },
    )
    assert "result" in payload, payload


def _mcp_list_tools(e2e_client, url: str, headers: dict[str, str]) -> list[dict]:
    payload = _mcp_post(
        e2e_client,
        url,
        headers,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert "result" in payload, payload
    return payload["result"]["tools"]


def _mcp_call_tool(
    e2e_client,
    url: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict,
) -> dict:
    return _mcp_post(
        e2e_client,
        url,
        headers,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )


@pytest.fixture(scope="module")
def _embedding_config(e2e_client, platform_admin):
    """Configure OpenAI embeddings (required for search_knowledge)."""
    if not EMBEDDINGS_AVAILABLE:
        pytest.skip("EMBEDDINGS_AI_TEST_KEY not set")

    config = {
        "provider": "openai",
        "model": "text-embedding-3-small",
        "api_key": os.environ["EMBEDDINGS_AI_TEST_KEY"],
    }
    resp = e2e_client.post(
        "/api/admin/llm/embedding-config",
        json=config,
        headers=platform_admin.headers,
    )
    assert resp.status_code == 200, f"Embedding config failed: {resp.text}"

    yield

    try:
        e2e_client.delete(
            "/api/admin/llm/embedding-config",
            headers=platform_admin.headers,
        )
    except Exception as e:
        logger.warning(f"Failed to clean up embedding config: {e}")


@pytest.fixture(scope="module")
def _knowledge_scoping_setup(e2e_client, platform_admin, _embedding_config):
    """Two namespaces with disjoint markers and two AUTHENTICATED agents,
    each bound to one namespace and with empty system_tools (so the
    auto-inject path is exercised)."""
    suffix = uuid.uuid4().hex[:8]
    ns_alpha = f"e2e_mcp_ns_alpha_{suffix}"
    ns_beta = f"e2e_mcp_ns_beta_{suffix}"
    marker_alpha = f"ALPHA-MARKER-{suffix}"
    marker_beta = f"BETA-MARKER-{suffix}"

    agent_alpha_id: str | None = None
    agent_beta_id: str | None = None

    try:
        for ns, content, key in [
            (ns_alpha, f"Alpha document content. {marker_alpha}", "alpha-doc"),
            (ns_beta, f"Beta document content. {marker_beta}", "beta-doc"),
        ]:
            resp = e2e_client.post(
                "/api/cli/knowledge/store",
                headers=platform_admin.headers,
                json={
                    "content": content,
                    "namespace": ns,
                    "key": key,
                    "metadata": {},
                    "scope": None,  # global scope
                },
            )
            assert resp.status_code == 200, (
                f"Failed to seed namespace {ns}: {resp.text}"
            )

        resp = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": f"MCP KS Alpha {suffix}",
                "system_prompt": "Alpha agent with knowledge.",
                "channels": ["chat"],
                "system_tools": [],
                "knowledge_sources": [ns_alpha],
            },
        )
        assert resp.status_code == 201, resp.text
        agent_alpha_id = resp.json()["id"]

        resp = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": f"MCP KS Beta {suffix}",
                "system_prompt": "Beta agent with knowledge.",
                "channels": ["chat"],
                "system_tools": [],
                "knowledge_sources": [ns_beta],
            },
        )
        assert resp.status_code == 201, resp.text
        agent_beta_id = resp.json()["id"]

        yield {
            "ns_alpha": ns_alpha,
            "ns_beta": ns_beta,
            "marker_alpha": marker_alpha,
            "marker_beta": marker_beta,
            "agent_alpha_id": agent_alpha_id,
            "agent_beta_id": agent_beta_id,
        }
    finally:
        for agent_id in (agent_alpha_id, agent_beta_id):
            if agent_id:
                try:
                    e2e_client.delete(
                        f"/api/agents/{agent_id}",
                        headers=platform_admin.headers,
                    )
                except Exception as e:
                    logger.warning(f"Failed to clean up agent {agent_id}: {e}")
        for ns, key in [(ns_alpha, "alpha-doc"), (ns_beta, "beta-doc")]:
            try:
                e2e_client.post(
                    "/api/cli/knowledge/delete",
                    headers=platform_admin.headers,
                    json={"key": key, "namespace": ns, "scope": None},
                )
            except Exception as e:
                logger.warning(f"Failed to clean up namespace {ns}: {e}")


@pytest.mark.e2e
@pytest.mark.skipif(not EMBEDDINGS_AVAILABLE, reason="EMBEDDINGS_AI_TEST_KEY not set")
class TestMCPKnowledgeScoping:
    """Knowledge scoping across un-scoped and agent-scoped MCP mounts."""

    def test_unscoped_lists_search_knowledge_via_auto_inject(
        self, e2e_client, platform_admin, _knowledge_scoping_setup
    ):
        """/mcp tools/list includes search_knowledge because at least one
        accessible agent has knowledge_sources, even though no agent has
        search_knowledge in system_tools."""
        _mcp_initialize(e2e_client, "/mcp", platform_admin.headers)
        tools = _mcp_list_tools(e2e_client, "/mcp", platform_admin.headers)

        names = [t["name"] for t in tools]
        assert "search_knowledge" in names, (
            f"search_knowledge missing from /mcp tools/list (auto-inject "
            f"path). Got: {names}"
        )

    def test_agent_scoped_lists_search_knowledge(
        self, e2e_client, platform_admin, _knowledge_scoping_setup
    ):
        """/mcp/{agent_id} tools/list includes search_knowledge for an
        agent with knowledge_sources, even if system_tools is empty."""
        url = f"/mcp/{_knowledge_scoping_setup['agent_alpha_id']}"
        _mcp_initialize(e2e_client, url, platform_admin.headers)
        tools = _mcp_list_tools(e2e_client, url, platform_admin.headers)

        names = [t["name"] for t in tools]
        assert "search_knowledge" in names, (
            f"search_knowledge missing from agent-scoped /mcp/<agent>: {names}"
        )

    def test_unscoped_search_returns_union(
        self, e2e_client, platform_admin, _knowledge_scoping_setup
    ):
        """/mcp search_knowledge can find documents across the union of
        namespaces from all accessible agents."""
        _mcp_initialize(e2e_client, "/mcp", platform_admin.headers)

        result = _mcp_call_tool(
            e2e_client, "/mcp", platform_admin.headers, "search_knowledge",
            {"query": _knowledge_scoping_setup["marker_alpha"]},
        )
        text_blob = str(result.get("result", ""))
        assert _knowledge_scoping_setup["ns_alpha"] in text_blob, (
            f"Expected ns_alpha in unscoped search; got {result}"
        )

        result = _mcp_call_tool(
            e2e_client, "/mcp", platform_admin.headers, "search_knowledge",
            {"query": _knowledge_scoping_setup["marker_beta"]},
        )
        text_blob = str(result.get("result", ""))
        assert _knowledge_scoping_setup["ns_beta"] in text_blob, (
            f"Expected ns_beta in unscoped search; got {result}"
        )

    def test_agent_scoped_isolation(
        self, e2e_client, platform_admin, _knowledge_scoping_setup
    ):
        """/mcp/{agent_alpha} can only see ns_alpha. The same user could
        see ns_beta via the unscoped mount, but the agent-scoped session
        is bound to that one agent's namespaces."""
        url = f"/mcp/{_knowledge_scoping_setup['agent_alpha_id']}"
        _mcp_initialize(e2e_client, url, platform_admin.headers)

        result = _mcp_call_tool(
            e2e_client, url, platform_admin.headers, "search_knowledge",
            {"query": _knowledge_scoping_setup["marker_alpha"]},
        )
        text_blob = str(result.get("result", ""))
        assert _knowledge_scoping_setup["ns_alpha"] in text_blob, (
            f"Expected ns_alpha hit on agent-scoped query; got {result}"
        )

        result = _mcp_call_tool(
            e2e_client, url, platform_admin.headers, "search_knowledge",
            {"query": _knowledge_scoping_setup["marker_beta"]},
        )
        text_blob = str(result.get("result", ""))
        assert _knowledge_scoping_setup["ns_beta"] not in text_blob, (
            f"agent-scoped session leaked ns_beta into a query "
            f"that should only see ns_alpha. Result: {result}"
        )

    def test_agent_scoped_rejects_explicit_cross_namespace(
        self, e2e_client, platform_admin, _knowledge_scoping_setup
    ):
        """Explicit namespace='ns_beta' on an agent_alpha-scoped mount
        must be rejected, not silently honored via the user's broader
        access. This is the security boundary the agent-scoped mount
        exists to enforce."""
        url = f"/mcp/{_knowledge_scoping_setup['agent_alpha_id']}"
        _mcp_initialize(e2e_client, url, platform_admin.headers)

        result = _mcp_call_tool(
            e2e_client, url, platform_admin.headers, "search_knowledge",
            {
                "query": _knowledge_scoping_setup["marker_beta"],
                "namespace": _knowledge_scoping_setup["ns_beta"],
            },
        )
        text_blob = str(result.get("result", ""))
        assert "Access denied" in text_blob or "not accessible" in text_blob, (
            f"Cross-namespace request on agent-scoped mount was not "
            f"rejected. Got: {result}"
        )

    def test_unscoped_rejects_unknown_namespace(
        self, e2e_client, platform_admin, _knowledge_scoping_setup
    ):
        """Sanity: even on the un-scoped mount, namespaces outside the
        user's accessible set must be rejected."""
        _mcp_initialize(e2e_client, "/mcp", platform_admin.headers)
        result = _mcp_call_tool(
            e2e_client, "/mcp", platform_admin.headers, "search_knowledge",
            {
                "query": "anything",
                "namespace": "this_namespace_does_not_exist",
            },
        )
        text_blob = str(result.get("result", ""))
        assert "Access denied" in text_blob or "not accessible" in text_blob, (
            f"Unknown namespace was not rejected on /mcp: {result}"
        )
