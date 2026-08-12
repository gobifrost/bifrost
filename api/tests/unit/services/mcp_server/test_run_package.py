"""Tests for the Bifrost Agent Plugins package builder."""

import json
import zipfile
from io import BytesIO

from src.services.mcp_server.run_package import (
    PLUGIN_FILENAME,
    build_bifrost_run_plugin,
    build_setup_prompt,
    mcp_url,
)
from src.services.mcp_server.tools.gateway import GATEWAY_INSTRUCTIONS


PUBLIC_URL = "https://bifrost.example.com/"


def _read_package(zip_bytes: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_build_bifrost_run_plugin_is_deterministic():
    first = build_bifrost_run_plugin(PUBLIC_URL)
    second = build_bifrost_run_plugin(PUBLIC_URL)

    assert first == second
    assert PLUGIN_FILENAME == "bifrost-agent.zip"


def test_build_bifrost_run_plugin_contents_and_metadata_are_canonical():
    zip_bytes = build_bifrost_run_plugin(PUBLIC_URL)

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
        ]
        for info in archive.infolist():
            assert info.date_time == (2024, 1, 1, 0, 0, 0)
            assert info.external_attr == 0o644 << 16

    files = _read_package(zip_bytes)
    plugin = json.loads(files["plugin.json"])
    assert plugin == {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "bifrost-agent",
        "version": "1.0.0",
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
    files = _read_package(build_bifrost_run_plugin(PUBLIC_URL))

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
        assert manifest["version"] == "1.0.0"
        assert manifest["description"] == json.loads(files["plugin.json"])[
            "description"
        ]

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
    assert codex_marketplace["plugins"][0]["source"] == {
        "source": "local",
        "path": "./",
    }


def test_build_bifrost_run_plugin_skill_and_readme_use_gateway_instructions():
    files = _read_package(build_bifrost_run_plugin(PUBLIC_URL))

    skill = files["skills/bifrost-agent/SKILL.md"].decode()
    assert skill.startswith("---\nname: bifrost-agent\n")
    assert "# Bifrost Agent" in skill
    assert "proactively" in skill
    assert GATEWAY_INSTRUCTIONS in skill

    readme = files["README.md"].decode()
    assert "https://bifrost.example.com/mcp" in readme
    assert "streamable-http" in readme
    assert "Claude Code" in readme
    assert "GitHub Copilot" in readme
    assert "Claude Desktop" in readme
    assert "Microsoft Copilot Studio" in readme
    assert "OAuth" not in readme


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
