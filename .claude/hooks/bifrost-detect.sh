#!/bin/bash

# Bifrost environment detection hook for Claude Code
# Runs on SessionStart to detect SDK, auth, MCP, and source access

# Only run if CLAUDE_ENV_FILE is available (SessionStart only)
if [ -z "$CLAUDE_ENV_FILE" ]; then
  exit 0
fi

# Initialize all variables
BIFROST_HAS_SOURCE=false
BIFROST_SDK_INSTALLED=false
BIFROST_LOGGED_IN=false
BIFROST_MCP_CONFIGURED=false
BIFROST_DEV_URL=""
BIFROST_SOURCE_PATH=""
BIFROST_PYTHON_CMD=""
BIFROST_PIP_CMD=""
BIFROST_PYTHON_VERSION=""

# 1. Detect Bifrost source code via file markers
check_bifrost_source() {
  local dir="$1"
  local markers=0

  [ -f "$dir/api/shared/models.py" ] && markers=$((markers + 1))
  [ -f "$dir/docker-compose.dev.yml" ] && markers=$((markers + 1))
  [ -f "$dir/api/src/main.py" ] && markers=$((markers + 1))

  if [ $markers -ge 2 ]; then
    echo "$dir"
    return 0
  fi
  return 1
}

search_dir="$(pwd)"
for i in 1 2 3 4 5; do
  if result=$(check_bifrost_source "$search_dir"); then
    BIFROST_HAS_SOURCE=true
    BIFROST_SOURCE_PATH="$result"
    break
  fi
  parent="$(dirname "$search_dir")"
  [ "$parent" = "$search_dir" ] && break
  search_dir="$parent"
done

# 2. Check if bifrost CLI is installed
if command -v bifrost >/dev/null 2>&1; then
  BIFROST_SDK_INSTALLED=true
fi

# 3. Check for credentials file and extract URL
CREDS_FILE=""
if [ -f "$HOME/.bifrost/credentials.json" ]; then
  CREDS_FILE="$HOME/.bifrost/credentials.json"
elif [ -n "$APPDATA" ] && [ -f "$APPDATA/Bifrost/credentials.json" ]; then
  CREDS_FILE="$APPDATA/Bifrost/credentials.json"
fi

if [ -n "$CREDS_FILE" ]; then
  BIFROST_LOGGED_IN=true
  if command -v jq >/dev/null 2>&1; then
    BIFROST_DEV_URL=$(jq -r '.api_url // empty' "$CREDS_FILE" 2>/dev/null)
  fi
fi

# 4. Check if bifrost MCP server is configured
if command -v claude >/dev/null 2>&1; then
  if claude mcp list 2>/dev/null | grep -q "bifrost"; then
    BIFROST_MCP_CONFIGURED=true
  fi
fi

# 5. Detect Python environment (for SDK installation)
for cmd in python3.12 python3.11 python3 python; do
  if command -v "$cmd" >/dev/null 2>&1; then
    version=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
      BIFROST_PYTHON_CMD="$cmd"
      BIFROST_PYTHON_VERSION="$version"
      break
    fi
  fi
done

if command -v pipx >/dev/null 2>&1; then
  BIFROST_PIP_CMD="pipx install --force"
elif command -v pip3 >/dev/null 2>&1; then
  BIFROST_PIP_CMD="pip3 install --force-reinstall"
elif command -v pip >/dev/null 2>&1; then
  BIFROST_PIP_CMD="pip install --force-reinstall"
elif [ -n "$BIFROST_PYTHON_CMD" ]; then
  if "$BIFROST_PYTHON_CMD" -m pip --version >/dev/null 2>&1; then
    BIFROST_PIP_CMD="$BIFROST_PYTHON_CMD -m pip install --force-reinstall"
  fi
fi

# Detect OS
BIFROST_OS=""
if [ -f /etc/os-release ]; then
  . /etc/os-release
  BIFROST_OS="$ID"
elif [ "$(uname)" = "Darwin" ]; then
  BIFROST_OS="macos"
elif [ -n "$WINDIR" ]; then
  BIFROST_OS="windows"
fi

# Write all variables to CLAUDE_ENV_FILE
shell_export() {
  local key="$1"
  local value="$2"
  printf 'export %s=%q\n' "$key" "$value"
}

{
  shell_export BIFROST_HAS_SOURCE "$BIFROST_HAS_SOURCE"
  shell_export BIFROST_SDK_INSTALLED "$BIFROST_SDK_INSTALLED"
  shell_export BIFROST_LOGGED_IN "$BIFROST_LOGGED_IN"
  shell_export BIFROST_MCP_CONFIGURED "$BIFROST_MCP_CONFIGURED"
  [ -n "$BIFROST_DEV_URL" ] && shell_export BIFROST_DEV_URL "$BIFROST_DEV_URL"
  [ -n "$BIFROST_SOURCE_PATH" ] && shell_export BIFROST_SOURCE_PATH "$BIFROST_SOURCE_PATH"
  [ -n "$BIFROST_PYTHON_CMD" ] && shell_export BIFROST_PYTHON_CMD "$BIFROST_PYTHON_CMD"
  [ -n "$BIFROST_PYTHON_VERSION" ] && shell_export BIFROST_PYTHON_VERSION "$BIFROST_PYTHON_VERSION"
  [ -n "$BIFROST_PIP_CMD" ] && shell_export BIFROST_PIP_CMD "$BIFROST_PIP_CMD"
  [ -n "$BIFROST_OS" ] && shell_export BIFROST_OS "$BIFROST_OS"
} >> "$CLAUDE_ENV_FILE"

exit 0
