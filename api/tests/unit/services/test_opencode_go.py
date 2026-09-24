"""OpenCode Go endpoint identity, wire-surface routing, and header contract."""

from src.services.opencode_go import (
    OPENCODE_GO_DEFAULT_ENDPOINT,
    is_opencode_go_endpoint,
    opencode_go_anthropic_endpoint,
    opencode_go_extra_headers,
    opencode_go_wire_api,
)


def test_default_endpoint_is_the_go_gateway() -> None:
    assert OPENCODE_GO_DEFAULT_ENDPOINT == "https://opencode.ai/zen/go/v1"


def test_is_opencode_go_endpoint_matches_only_go_gateway_paths() -> None:
    assert is_opencode_go_endpoint(OPENCODE_GO_DEFAULT_ENDPOINT)
    assert is_opencode_go_endpoint("https://opencode.ai/zen/go/v1/")
    assert is_opencode_go_endpoint("https://opencode.ai/zen/go")
    assert not is_opencode_go_endpoint("https://opencode.ai/zen/v1")
    assert not is_opencode_go_endpoint("https://opencode.ai")
    assert not is_opencode_go_endpoint("https://example.test/zen/go/v1")
    assert not is_opencode_go_endpoint(None)


def test_wire_api_matches_the_published_catalog() -> None:
    assert opencode_go_wire_api("grok-4.7") == "responses"
    assert opencode_go_wire_api("gpt-6-luna") == "responses"
    assert opencode_go_wire_api("gpt-5.6-luna") == "responses"
    assert opencode_go_wire_api("muse-spark-1.3-contributor") == "responses"
    assert opencode_go_wire_api("minimax-m3") == "messages"
    assert opencode_go_wire_api("qwen3.8-flash") == "messages"
    assert opencode_go_wire_api("deepseek-v4.1-flash") == "chat_completions"
    assert opencode_go_wire_api("glm-5.3") == "chat_completions"
    assert opencode_go_wire_api("brand-new-model") == "chat_completions"


def test_anthropic_endpoint_drops_the_v1_suffix_the_sdk_re_adds() -> None:
    assert (
        opencode_go_anthropic_endpoint(OPENCODE_GO_DEFAULT_ENDPOINT)
        == "https://opencode.ai/zen/go"
    )
    assert (
        opencode_go_anthropic_endpoint("https://opencode.ai/zen/go/v1/")
        == "https://opencode.ai/zen/go"
    )
    assert (
        opencode_go_anthropic_endpoint("https://opencode.ai/zen/go")
        == "https://opencode.ai/zen/go"
    )
    assert opencode_go_anthropic_endpoint(None) is None


def test_extra_headers_identify_bifrost_and_carry_the_session() -> None:
    headers = opencode_go_extra_headers("conversation-1")

    assert headers["User-Agent"].startswith("Bifrost/")
    assert headers["x-opencode-session"] == "conversation-1"


def test_session_header_is_omitted_without_a_session() -> None:
    headers = opencode_go_extra_headers()

    assert headers["User-Agent"].startswith("Bifrost/")
    assert "x-opencode-session" not in headers
