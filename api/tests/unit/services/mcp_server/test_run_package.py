"""Tests for the Bifrost Agent Plugins package builder."""

import json
import zipfile
from io import BytesIO

import pytest

from src.services.mcp_server.run_package import (
    MARKETPLACE_ID,
    PLUGIN_FILENAME,
    build_bifrost_run_plugin,
    build_setup_prompt,
    mcp_url,
    normalize_plugin_version,
)
from src.services.mcp_server.tools.gateway import GATEWAY_INSTRUCTIONS


PUBLIC_URL = "https://bifrost.example.com/"
INSTANCE_VERSION = "1.2.2-dev.12"


def _read_package(zip_bytes: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_build_bifrost_run_plugin_is_deterministic():
    first = build_bifrost_run_plugin(PUBLIC_URL, INSTANCE_VERSION)
    second = build_bifrost_run_plugin(PUBLIC_URL, INSTANCE_VERSION)

    assert first == second
    assert PLUGIN_FILENAME == "bifrost-agent.zip"


def test_build_bifrost_run_plugin_contents_and_metadata_are_canonical():
    zip_bytes = build_bifrost_run_plugin(PUBLIC_URL, INSTANCE_VERSION)

    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        assert archive.namelist() == [
            ".agents/plugins/marketplace.json",
            ".claude-plugin/marketplace.json",
            ".claude-plugin/plugin.json",
            ".codex-plugin/plugin.json",
            ".cursor-plugin/plugin.json",
            ".github/plugin/plugin.json",
            ".mcp.json",
            "README.md",
            "assets/icon.png",
            "assets/logo.png",
            "gemini-extension.json",
            "mcp.json",
            "plugin.json",
            "server.json",
            "skills/bifrost-agent/SKILL.md",
            "skills/bifrost-agent/agents/openai.yaml",
        ]
        for info in archive.infolist():
            assert info.date_time == (2024, 1, 1, 0, 0, 0)
            assert info.external_attr == 0o644 << 16

    files = _read_package(zip_bytes)
    plugin = json.loads(files["plugin.json"])
    assert plugin == {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "bifrost-agent",
        "version": INSTANCE_VERSION,
        "description": (
            "Proactively use Bifrost whenever a request could benefit from a "
            "specialized agent, connected system, or executable workflow. Search "
            "for a relevant agent first; use it when the request authorizes the "
            "work, or offer to use it when execution would expand the request."
        ),
        "author": {
            "name": "Bifrost",
            "url": "https://gobifrost.com",
        },
        "homepage": "https://gobifrost.com",
        "repository": "https://github.com/gobifrost/bifrost",
        "license": "AGPL-3.0",
        "keywords": ["agents", "automation", "bifrost", "mcp", "workflows"],
    }

    mcp = json.loads(files["mcp.json"])
    assert mcp == {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "bifrost": {
                "type": "streamable-http",
                "url": "https://bifrost.example.com/mcp",
            }
        },
    }

    native_mcp = json.loads(files[".mcp.json"])
    assert native_mcp == {
        "mcpServers": {
            "bifrost": {
                "type": "http",
                "url": "https://bifrost.example.com/mcp",
            }
        }
    }


def test_build_bifrost_run_plugin_includes_native_harness_manifests():
    files = _read_package(
        build_bifrost_run_plugin(PUBLIC_URL, INSTANCE_VERSION)
    )

    claude = json.loads(files[".claude-plugin/plugin.json"])
    codex = json.loads(files[".codex-plugin/plugin.json"])
    github = json.loads(files[".github/plugin/plugin.json"])
    cursor = json.loads(files[".cursor-plugin/plugin.json"])
    gemini = json.loads(files["gemini-extension.json"])
    registry = json.loads(files["server.json"])
    claude_marketplace = json.loads(
        files[".claude-plugin/marketplace.json"]
    )
    codex_marketplace = json.loads(files[".agents/plugins/marketplace.json"])

    for manifest in (claude, codex, github, cursor):
        assert manifest["name"] == "bifrost-agent"
        assert manifest["version"] == INSTANCE_VERSION
        assert manifest["description"] == json.loads(files["plugin.json"])[
            "description"
        ]
    assert gemini["version"] == INSTANCE_VERSION
    assert registry["version"] == INSTANCE_VERSION

    assert codex["skills"] == "./skills/"
    assert codex["mcpServers"] == "./.mcp.json"
    assert codex["interface"]["displayName"] == "Bifrost Agent"
    assert codex["interface"]["composerIcon"] == "./assets/icon.png"
    assert codex["interface"]["logo"] == "./assets/logo.png"
    assert claude["displayName"] == "Bifrost Agent"
    assert codex["interface"]["defaultPrompt"] == "Use Bifrost"
    assert cursor["skills"] == "./skills/"
    assert cursor["mcpServers"] == "./.mcp.json"
    assert cursor["logo"] == "./assets/logo.png"
    assert files["assets/icon.png"].startswith(b"\x89PNG\r\n\x1a\n")
    assert files["assets/logo.png"].startswith(b"\x89PNG\r\n\x1a\n")
    assert gemini["mcpServers"]["bifrost"]["httpUrl"] == (
        "https://bifrost.example.com/mcp"
    )
    assert registry["remotes"] == [
        {
            "type": "streamable-http",
            "url": "https://bifrost.example.com/mcp",
        }
    ]
    assert claude_marketplace["plugins"][0]["source"] == "./"
    assert claude_marketplace["name"] == MARKETPLACE_ID
    assert claude_marketplace["plugins"][0]["version"] == INSTANCE_VERSION
    assert MARKETPLACE_ID == "bifrost-agent"
    assert codex_marketplace["name"] == MARKETPLACE_ID
    assert codex_marketplace["interface"]["displayName"] == "Bifrost Agent"
    assert codex_marketplace["plugins"][0]["source"] == {
        "source": "local",
        "path": "./",
    }


def test_build_bifrost_run_plugin_skill_and_readme_use_gateway_instructions():
    files = _read_package(
        build_bifrost_run_plugin(PUBLIC_URL, INSTANCE_VERSION)
    )

    skill = files["skills/bifrost-agent/SKILL.md"].decode()
    assert skill.startswith("---\nname: bifrost-agent\n")
    assert "# Bifrost Agent" in skill
    assert "bifrost_get_required_instructions" in skill
    assert GATEWAY_INSTRUCTIONS in skill
    assert files["skills/bifrost-agent/agents/openai.yaml"].decode() == (
        "interface:\n"
        '  display_name: "Assist"\n'
        '  short_description: "Find and use the right Bifrost agent or workflow"\n'
    )

    readme = files["README.md"].decode()
    assert "https://bifrost.example.com/mcp" in readme
    assert "streamable-http" in readme
    assert "Claude Code" in readme
    assert "GitHub Copilot" in readme
    assert "Claude Desktop" in readme
    assert "Microsoft Copilot Studio" in readme
    assert "codex plugin add bifrost-agent@bifrost-agent" in readme
    assert "claude plugin install bifrost-agent@bifrost-agent" in readme
    assert "separately from the standard `bifrost` marketplace" in readme
    assert f"Bifrost version: `{INSTANCE_VERSION}`" in readme
    assert "OAuth" not in readme


@pytest.mark.parametrize(
    ("instance_version", "expected"),
    [
        ("1.2.1", "1.2.1"),
        ("v1.2.1", "1.2.1"),
        ("1.2.2-dev.12", "1.2.2-dev.12"),
        (
            "v1.2.0-12-g88a02534c-dirty",
            "1.2.0-12-g88a02534c-dirty",
        ),
        ("88a02534c", "0.0.0+g88a02534c"),
        ("88a02534c-dirty", "0.0.0+g88a02534c.dirty"),
        ("unknown", "0.0.0"),
    ],
)
def test_normalize_plugin_version(instance_version: str, expected: str):
    assert normalize_plugin_version(instance_version) == expected


def test_build_setup_prompt_preserves_the_exact_gateway_instructions():
    setup_prompt = build_setup_prompt()

    assert setup_prompt == (
        "Help me create a reusable skill or agent with this exact prompt:\n\n"
        f"{GATEWAY_INSTRUCTIONS}"
    )


def test_mcp_url_strips_trailing_slash():
    assert mcp_url("https://bifrost.example.com///") == (
        "https://bifrost.example.com/mcp"
    )
