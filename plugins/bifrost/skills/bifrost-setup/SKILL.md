---
name: bifrost:setup
description: Set up Bifrost SDK - install CLI, authenticate, configure MCP server. Use when user needs to get started with Bifrost or has incomplete setup.
---

# Bifrost Setup

## Introduction

Before running any commands, introduce the setup process to the user:

> **Bifrost SDK Setup**
>
> I'll help you set up the Bifrost SDK. This involves three steps:
> 1. **Install the CLI** - A command-line tool for developing and testing workflows
> 2. **Authenticate** - Log in to your Bifrost instance
> 3. **Configure MCP** - Connect Claude Code to Bifrost's tools
>
> Let me check your current setup status...

## Check Current State

Run this command to check environment (set by SessionStart hook):

```bash
echo "SDK: $BIFROST_SDK_INSTALLED | Login: $BIFROST_LOGGED_IN | MCP: $BIFROST_MCP_CONFIGURED"
echo "Python: $BIFROST_PYTHON_CMD ($BIFROST_PYTHON_VERSION) | Pip: $BIFROST_PIP_CMD | OS: $BIFROST_OS"
```

## Resume Logic

Based on the environment state:

1. **All true** -> Setup complete! Inform user they're ready to use `/bifrost:build`
2. **SDK installed + logged in, MCP not configured** -> SDK-first development is ready! MCP is optional — the CLI (`bifrost api`, `bifrost watch`) handles most operations. MCP is only needed for creating forms/apps/agents and knowledge search. Ask if they want to configure it.
3. **SDK not installed** -> Go to SDK Installation
4. **SDK installed but not logged in** -> Go to Login
5. **Logged in but MCP not configured** -> Go to MCP Configuration (optional)

## SDK Installation

### Prerequisites Check

**If BIFROST_PYTHON_CMD is empty:**
Python 3.11+ is required. Install based on OS:
- **ubuntu/debian**: `sudo apt install python3.11`
- **macos**: `brew install python@3.11`
- **windows**: `winget install Python.Python.3.11`

**If BIFROST_PIP_CMD is empty:**
Need pipx (recommended for CLI tools on modern systems):
- **ubuntu/debian**: `sudo apt install pipx && pipx ensurepath`
- **macos**: `brew install pipx && pipx ensurepath`
- **windows**: `pip install pipx`

### Get Bifrost URL

**If `$BIFROST_DEV_URL` is set:** Use that URL (already detected from credentials).

**Otherwise:** Ask the user: "What is your Bifrost instance URL? (e.g., https://yourcompany.gobifrost.com)"

Do NOT suggest placeholder URLs - every Bifrost instance has a unique URL provided by the user's organization.

### Install SDK

**Use the detected pip command** (from `$BIFROST_PIP_CMD`):

```bash
$BIFROST_PIP_CMD {url}/api/cli/download
```

Verify with:
```bash
bifrost help
```

## Login

```bash
bifrost login --url {url}
```

This opens a browser for authentication and saves credentials to `~/.bifrost/credentials.json`.

## MCP Configuration

Check existing configuration:
```bash
claude mcp list
```

**If `bifrost` exists with wrong URL:** Ask user if they want to update it.

**Add/update MCP server:**
```bash
claude mcp remove bifrost 2>/dev/null; claude mcp add --transport http bifrost {url}/mcp
```

## Restart Required (MCP only)

If MCP was configured, tell the user:

> Setup complete! Please restart Claude Code for the MCP server to take effect.
>
> After restarting, you can use `/bifrost:build` to create workflows, forms, and apps.

If MCP was skipped (SDK-first only), tell the user:

> Setup complete! You're ready to use `/bifrost:build` for SDK-first development.
>
> Use `bifrost watch` to auto-sync file changes and `bifrost api` for platform operations. MCP can be added later if you need `create_form`, `create_app`, or knowledge search.

## Troubleshooting

### pipx install fails with network error
- Verify URL is accessible: `curl {url}/api/cli/download -o /dev/null -w "%{http_code}"`

### bifrost login hangs
- Check if URL is accessible in browser
- Try with `--no-browser` flag and copy the URL manually

### MCP not working after restart
- Verify with `claude mcp list`
- Check Claude Code logs for MCP connection errors
