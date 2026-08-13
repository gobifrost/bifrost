"""Deterministic multi-harness plugin builder for the Bifrost MCP gateway."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from src.services.mcp_server.tools.gateway import GATEWAY_INSTRUCTIONS

PLUGIN_FILENAME = "bifrost-agent.zip"
PLUGIN_ID = "bifrost-agent"
PLUGIN_NAME = "Bifrost Agent"
MARKETPLACE_ID = "bifrost-agent"
MARKETPLACE_NAME = "Bifrost Agent"
ZIP_TIMESTAMP = (2024, 1, 1, 0, 0, 0)
HOMEPAGE_URL = "https://gobifrost.com"
REPOSITORY_URL = "https://github.com/gobifrost/bifrost"
ASSET_DIRECTORY = Path("/app/assets")

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\."
    r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
GIT_HASH_RE = re.compile(r"^(?P<sha>[0-9a-f]{7,40})(?P<dirty>-dirty)?$")

PLUGIN_DESCRIPTION = (
    "Proactively use Bifrost whenever a request could benefit from a "
    "specialized agent, connected system, or executable workflow. Search for a "
    "relevant agent first; use it when the request authorizes the work, or "
    "offer to use it when execution would expand the request."
)


def mcp_url(public_url: str) -> str:
    """Return the public streamable-http MCP URL."""
    return f"{public_url.rstrip('/')}/mcp"


def build_setup_prompt() -> str:
    """Return the prompt used to create a reusable skill in manual clients."""
    return (
        "Help me create a reusable skill or agent with this exact prompt:\n\n"
        f"{GATEWAY_INSTRUCTIONS}"
    )


def normalize_plugin_version(instance_version: str) -> str:
    """Convert the running Bifrost version to strict plugin semver."""
    candidate = instance_version.strip().removeprefix("v")
    if SEMVER_RE.fullmatch(candidate):
        return candidate

    git_hash = GIT_HASH_RE.fullmatch(candidate)
    if git_hash is not None:
        dirty = ".dirty" if git_hash.group("dirty") else ""
        return f"0.0.0+g{git_hash.group('sha')}{dirty}"

    return "0.0.0"


def build_bifrost_run_plugin(public_url: str, instance_version: str) -> bytes:
    """Build a deterministic polyglot agent plugin package."""
    plugin_version = normalize_plugin_version(instance_version)
    files = {
        ".agents/plugins/marketplace.json": _json_bytes(
            _codex_marketplace_json()
        ),
        ".claude-plugin/marketplace.json": _json_bytes(
            _claude_marketplace_json(plugin_version)
        ),
        ".claude-plugin/plugin.json": _json_bytes(
            _claude_plugin_json(plugin_version)
        ),
        ".codex-plugin/plugin.json": _json_bytes(
            _codex_plugin_json(plugin_version)
        ),
        ".cursor-plugin/plugin.json": _json_bytes(
            _cursor_plugin_json(plugin_version)
        ),
        ".github/plugin/plugin.json": _json_bytes(
            _github_plugin_json(plugin_version)
        ),
        ".mcp.json": _json_bytes(_native_mcp_json(public_url)),
        "assets/icon.png": _asset_bytes("icon.png"),
        "assets/logo.png": _asset_bytes("logo.png"),
        "plugin.json": _json_bytes(_plugin_json(plugin_version)),
        "mcp.json": _json_bytes(_mcp_json(public_url)),
        "gemini-extension.json": _json_bytes(
            _gemini_extension_json(public_url, plugin_version)
        ),
        "server.json": _json_bytes(
            _server_json(public_url, plugin_version)
        ),
        "skills/bifrost-agent/agents/openai.yaml": (
            _openai_skill_yaml().encode()
        ),
        "skills/bifrost-agent/SKILL.md": _skill_md().encode(),
        "README.md": _readme(public_url, plugin_version).encode(),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[path])
    return buffer.getvalue()


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _asset_bytes(filename: str) -> bytes:
    """Read a Bifrost brand asset baked into the API image."""
    return (ASSET_DIRECTORY / filename).read_bytes()


def _plugin_json(plugin_version: str) -> dict:
    return {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": PLUGIN_ID,
        "version": plugin_version,
        "description": PLUGIN_DESCRIPTION,
        "author": {
            "name": "Bifrost",
            "url": HOMEPAGE_URL,
        },
        "homepage": HOMEPAGE_URL,
        "repository": REPOSITORY_URL,
        "license": "AGPL-3.0",
        "keywords": _keywords(),
    }


def _mcp_json(public_url: str) -> dict:
    return {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "bifrost": {
                "type": "streamable-http",
                "url": mcp_url(public_url),
            }
        },
    }


def _native_mcp_json(public_url: str) -> dict:
    return {
        "mcpServers": {
            "bifrost": {
                "type": "http",
                "url": mcp_url(public_url),
            }
        }
    }


def _claude_plugin_json(plugin_version: str) -> dict:
    return {
        "name": PLUGIN_ID,
        "displayName": PLUGIN_NAME,
        "description": PLUGIN_DESCRIPTION,
        "version": plugin_version,
        "author": {"name": "Bifrost"},
    }


def _claude_marketplace_json(plugin_version: str) -> dict:
    return {
        "name": MARKETPLACE_ID,
        "owner": {"name": "Bifrost"},
        "description": "Plugins for connecting AI assistants to Bifrost.",
        "plugins": [
            {
                "name": PLUGIN_ID,
                "source": "./",
                "description": PLUGIN_DESCRIPTION,
                "version": plugin_version,
                "author": {"name": "Bifrost"},
                "homepage": HOMEPAGE_URL,
                "repository": REPOSITORY_URL,
                "license": "AGPL-3.0",
                "keywords": _keywords(),
                "category": "Productivity",
            }
        ],
    }


def _codex_plugin_json(plugin_version: str) -> dict:
    return {
        "name": PLUGIN_ID,
        "version": plugin_version,
        "description": PLUGIN_DESCRIPTION,
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "author": {
            "name": "Bifrost",
            "url": HOMEPAGE_URL,
        },
        "homepage": HOMEPAGE_URL,
        "repository": REPOSITORY_URL,
        "license": "AGPL-3.0",
        "keywords": _keywords(),
        "interface": {
            "displayName": PLUGIN_NAME,
            "shortDescription": "Use the agents and tools in your Bifrost instance.",
            "longDescription": PLUGIN_DESCRIPTION,
            "developerName": "Bifrost",
            "category": "Productivity",
            "capabilities": ["Interactive", "Read", "Write"],
            "websiteURL": HOMEPAGE_URL,
            "brandColor": "#222222",
            "composerIcon": "./assets/icon.png",
            "logo": "./assets/logo.png",
            "defaultPrompt": "Use Bifrost",
        },
    }


def _codex_marketplace_json() -> dict:
    return {
        "name": MARKETPLACE_ID,
        "interface": {"displayName": MARKETPLACE_NAME},
        "plugins": [
            {
                "name": PLUGIN_ID,
                "source": {
                    "source": "local",
                    "path": "./",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Productivity",
            }
        ],
    }


def _github_plugin_json(plugin_version: str) -> dict:
    return {
        "name": PLUGIN_ID,
        "description": PLUGIN_DESCRIPTION,
        "version": plugin_version,
        "author": {
            "name": "Bifrost",
            "url": HOMEPAGE_URL,
        },
        "homepage": HOMEPAGE_URL,
        "repository": REPOSITORY_URL,
        "license": "AGPL-3.0",
        "keywords": _keywords(),
    }


def _cursor_plugin_json(plugin_version: str) -> dict:
    return {
        "name": PLUGIN_ID,
        "displayName": PLUGIN_NAME,
        "description": PLUGIN_DESCRIPTION,
        "version": plugin_version,
        "author": {
            "name": "Bifrost",
            "url": HOMEPAGE_URL,
        },
        "homepage": HOMEPAGE_URL,
        "repository": REPOSITORY_URL,
        "license": "AGPL-3.0",
        "keywords": _keywords(),
        "logo": "./assets/logo.png",
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
    }


def _gemini_extension_json(public_url: str, plugin_version: str) -> dict:
    return {
        "name": PLUGIN_NAME,
        "version": plugin_version,
        "description": PLUGIN_DESCRIPTION,
        "mcpServers": {
            "bifrost": {
                "httpUrl": mcp_url(public_url),
                "oauth": {"enabled": True},
            }
        },
    }


def _server_json(public_url: str, plugin_version: str) -> dict:
    return {
        "$schema": (
            "https://static.modelcontextprotocol.io/schemas/"
            "2025-09-29/server.schema.json"
        ),
        "name": "com.gobifrost.mcp/bifrost-agent",
        "title": PLUGIN_NAME,
        "description": PLUGIN_DESCRIPTION,
        "repository": {
            "url": REPOSITORY_URL,
            "source": "github",
        },
        "version": plugin_version,
        "remotes": [
            {
                "type": "streamable-http",
                "url": mcp_url(public_url),
            }
        ],
    }


def _keywords() -> list[str]:
    return ["agents", "automation", "bifrost", "mcp", "workflows"]


def _skill_md() -> str:
    return (
        "---\n"
        f"name: {PLUGIN_ID}\n"
        f"description: {PLUGIN_DESCRIPTION}\n"
        "---\n\n"
        "# Bifrost Agent\n\n"
        f"{GATEWAY_INSTRUCTIONS}\n"
    )


def _openai_skill_yaml() -> str:
    return (
        "interface:\n"
        '  display_name: "Assist"\n'
        '  short_description: "Find and use the right Bifrost agent or workflow"\n'
    )


def _readme(public_url: str, plugin_version: str) -> str:
    return (
        "# Bifrost Agent\n\n"
        "Connect an AI assistant to the agents and tools in this Bifrost "
        "instance. The package keeps one shared skill and includes thin "
        "adapters for the major plugin formats.\n\n"
        "## Install\n\n"
        "The exact MCP URL and Bifrost version for this instance are already "
        "embedded in the package.\n\n"
        "### Claude Code\n\n"
        "Load the downloaded archive directly for the current session:\n\n"
        "```sh\n"
        "claude --plugin-dir /path/to/bifrost-agent.zip\n"
        "```\n\n"
        "For a persistent install, extract it, add the included marketplace, "
        "then install the plugin:\n\n"
        "```sh\n"
        "claude plugin marketplace add /path/to/bifrost-agent\n"
        f"claude plugin install {PLUGIN_ID}@{MARKETPLACE_ID}\n"
        "```\n\n"
        "### Codex and ChatGPT desktop\n\n"
        "Extract the archive and register its included marketplace:\n\n"
        "```sh\n"
        "codex plugin marketplace add /path/to/bifrost-agent\n"
        f"codex plugin add {PLUGIN_ID}@{MARKETPLACE_ID}\n"
        "```\n\n"
        "Restart ChatGPT desktop if it is already open. The package registers "
        f"the `{MARKETPLACE_NAME}` marketplace (ID `{MARKETPLACE_ID}`), "
        "separately from the standard `bifrost` "
        "marketplace. You can also install Bifrost Agent from that marketplace "
        "in the Plugins Directory.\n\n"
        "### GitHub Copilot CLI\n\n"
        "Extract the archive, then install its folder:\n\n"
        "```sh\n"
        "copilot plugin install /path/to/bifrost-agent\n"
        "```\n\n"
        "The package also includes manifests for Agent Plugins, Cursor, and "
        "Gemini CLI clients.\n\n"
        "The root `plugin.json` and `mcp.json` support Agent Plugins clients. "
        "The dot folders and `gemini-extension.json` provide native adapters "
        "for clients that use their own package layout.\n\n"
        "## Manual Setup\n\n"
        "For Claude Desktop, Microsoft Copilot Studio, and other MCP clients "
        "that cannot import the package, connect the streamable HTTP MCP URL "
        "and use the included `skills/bifrost-agent/SKILL.md` instructions.\n\n"
        f"- MCP URL: `{mcp_url(public_url)}`\n"
        f"- Bifrost version: `{plugin_version}`\n"
        "- Transport: `streamable-http`\n"
        "- Authenticate with your Bifrost account when prompted.\n"
    )
