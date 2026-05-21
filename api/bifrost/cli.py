"""
Bifrost CLI

Command-line interface for the Bifrost SDK.

Commands:
  bifrost login   - Authenticate with device authorization flow
  bifrost logout  - Clear stored credentials

Note: This module is standalone and doesn't import the main bifrost package
to avoid dependencies on src.* modules that only exist in the Docker environment.
"""

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import os
import pathlib
import shutil
import sys
import textwrap
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pathspec
    from bifrost.tui.watch import WatchApp
from uuid import uuid4

import httpx

# Import credentials module directly (it's standalone)
import bifrost.credentials as credentials
from bifrost._workspace_lock import WorkspaceLock, WorkspaceLockError
from bifrost.client import BifrostClient
# Canonical platform export list. Shared with api/src/services/app_bundler.
# A drift test (tests/unit/test_platform_names_match_runtime.py) keeps this
# in step with the client's runtime `$` registry so new platform exports
# can't ship without the classifier and bundler knowing about them.
from bifrost.platform_names import PLATFORM_EXPORT_NAMES as _PLATFORM_EXPORT_NAMES

logger = logging.getLogger(__name__)

# Default ignore patterns applied even without a .gitignore file.
# .bifrost/ is always force-included via negation so push/pull/sync round-trip
# the manifest. The watch handler layers an additional .bifrost/ exclusion on
# top of these for its own observer (see _WatchChangeHandler).
_DEFAULT_IGNORE_PATTERNS = [
    ".git/",
    "__pycache__/",
    ".ruff_cache/",
    "node_modules/",
    ".venv/",
    "venv/",
    ".DS_Store",
    "*.pyc",
    # Editor atomic-write turds (e.g. foo.tsx.tmp.12345.1776000000000).
    # Without this, watchdog sees these files and pushes them to S3; the
    # editor then renames them to the real file and watchdog emits a 'moved'
    # event that deliberately does NOT delete the source. Result: every save
    # leaves a turd in S3 forever.
    "*.tmp.*",
    "*.swp",
    "*.swo",
    "*~",
    ".#*",
]

_FORCE_INCLUDE_PATTERNS = [
    "!.bifrost/",
]


# ---------------------------------------------------------------------------
# Shared CLI utilities
# ---------------------------------------------------------------------------


def _normalize_line_endings(data: bytes) -> bytes:
    """Normalize CRLF to LF for text files. Binary files pass through unchanged."""
    if b"\x00" in data[:8192]:
        return data
    return data.replace(b"\r\n", b"\n")


def _hash_for_cache(raw_bytes: bytes) -> str:
    """md5 of post-normalization bytes — must match what the server stores.

    Watch normalizes CRLF to LF before pushing, so S3 stores normalized bytes
    and S3's ETag is md5 of those. Any hash we compare against a server ETag
    must be computed on the same normalized bytes.
    """
    return hashlib.md5(_normalize_line_endings(raw_bytes)).hexdigest()


def _is_bifrost_path(path: str) -> bool:
    """Check if a path refers to a .bifrost/ manifest directory."""
    return ".bifrost" in path.replace("\\", "/").split("/")


def _build_file_filter(local_root: pathlib.Path) -> "pathspec.PathSpec":
    """Build a gitignore-style file filter for the given directory.

    Loads .gitignore if present, otherwise uses sensible defaults.
    Always force-includes .bifrost/ regardless of ignore rules.
    """
    import pathspec

    # Always start with defaults (.git/, __pycache__, etc.) — .gitignore
    # never lists .git/ because git handles it implicitly, but we need it.
    lines = list(_DEFAULT_IGNORE_PATTERNS)

    gitignore_path = local_root / ".gitignore"
    if gitignore_path.is_file():
        lines.extend(gitignore_path.read_text(encoding="utf-8").splitlines())

    # Always force-include .bifrost/
    lines.extend(_FORCE_INCLUDE_PATTERNS)

    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


# Watch session heartbeat must be < Redis TTL (WATCH_SESSION_TTL_SECONDS in files.py)
WATCH_HEARTBEAT_SECONDS = 60


def _check_cli_version() -> None:
    """Hard-block the command if the installed CLI doesn't match the deployed server.

    Mirrors the policy in ``client/src/hooks/useVersionCheck.ts``: compare the
    baked-in build version against ``data.version`` from ``GET /api/version``
    via string equality, treat ``"unknown"`` / ``"0.0.0+source"`` as dev installs
    and skip. The CLI diverges from the SPA only by ``sys.exit(1)``-ing on
    mismatch instead of showing a banner — the user must upgrade before any
    command runs.
    """
    try:
        import httpx

        from bifrost import __version__

        installed = __version__.lstrip("v")
        if installed in ("unknown", "0.0.0+source"):
            return  # dev/source install — nothing to compare against

        # Re-load dotenv so a CWD-local .env's BIFROST_API_URL is honored even
        # if bifrost.client's import-time load happened against a different cwd.
        try:
            from dotenv import find_dotenv, load_dotenv
            load_dotenv(find_dotenv(usecwd=True), override=False)
        except ImportError:
            pass  # python-dotenv is optional; without it, only os.environ is consulted

        # Use credentials._resolve_url (not get_credentials) because the version
        # check only needs the URL — get_credentials returns None unless full
        # tokens are present too, which would skip the check on a logged-out CLI.
        api_url = credentials._resolve_url(None)
        if not api_url:
            return

        # Use httpx (already a hard dep via BifrostClient), not urllib.
        # CDNs/WAFs in front of prod Bifrost instances (Cloudflare on
        # bifrost.gocovi.com being the live case) 403 the default
        # `Python-urllib/X.Y` User-Agent. httpx's default UA gets through
        # and matches what every other SDK request already sends.
        resp = httpx.get(f"{api_url}/api/version", timeout=3)
        resp.raise_for_status()
        data = resp.json()

        server_version = (data.get("version") or "").lstrip("v")
        if not server_version:
            return  # server didn't tell us its version — best-effort skip

        if server_version != installed:
            print(
                f"\033[33mYour CLI ({installed}) is out of date. "
                f"Server is on {server_version}.\nRun:\n"
                f"  pipx install --force {api_url}/api/cli/download\n\033[0m",
                file=sys.stderr,
            )
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as e:
        # Best-effort: network errors, malformed JSON, missing credentials store,
        # etc. should never block the user. The exit-on-mismatch above is the
        # only intentional bail-out.
        logger.debug(f"CLI version check skipped: {e}")


async def login_flow(api_url: str | None = None, auto_open: bool = True) -> bool:
    """
    Interactive device authorization flow.

    1. Request device code from API
    2. Display user code and verification URL
    3. Open browser automatically (if auto_open=True)
    4. Poll for authorization
    5. Save credentials when authorized

    Args:
        api_url: Bifrost API URL (uses BIFROST_DEV_URL env var if not provided)
        auto_open: Whether to automatically open browser (default: True)

    Returns:
        True if login successful, False otherwise
    """
    # Get API URL
    if not api_url:
        api_url = os.getenv("BIFROST_DEV_URL", "http://localhost:8000")

    api_url = api_url.rstrip("/")

    # Surface keyring fallback here — login is the user's chance to fix it.
    from bifrost.credentials import warn_if_keyring_fallback
    warn_if_keyring_fallback()

    try:
        async with httpx.AsyncClient(base_url=api_url, timeout=30.0) as client:
            # Step 1: Request device code
            response = await client.post("/auth/device/code")
            if response.status_code != 200:
                print(f"Error requesting device code: {response.status_code}", file=sys.stderr)
                return False

            data = response.json()
            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_url = data["verification_url"]
            interval = data.get("interval", 5)

            # Step 2: Display instructions
            full_url = f"{api_url}{verification_url}"
            print(f"\nOpening browser to {full_url}")
            print(f"Enter this code: {user_code}\n")

            # Step 3: Open browser
            if auto_open:
                try:
                    webbrowser.open(full_url)
                except Exception:
                    pass  # Ignore browser open failures

            # Step 4: Poll for authorization
            print("Waiting for authorization", end="", flush=True)
            max_attempts = 60  # 5 minutes max (60 * 5 seconds)
            attempts = 0

            while attempts < max_attempts:
                await asyncio.sleep(interval)
                print(".", end="", flush=True)
                attempts += 1

                poll_response = await client.post(
                    "/auth/device/token",
                    json={"device_code": device_code}
                )

                if poll_response.status_code != 200:
                    print(f"\nError polling for token: {poll_response.status_code}", file=sys.stderr)
                    return False

                poll_data = poll_response.json()

                # Check for error
                if "error" in poll_data:
                    error = poll_data["error"]
                    if error == "authorization_pending":
                        continue  # Keep polling
                    elif error == "expired_token":
                        print("\nDevice code expired. Please try again.", file=sys.stderr)
                        return False
                    elif error == "access_denied":
                        print("\nAuthorization denied.", file=sys.stderr)
                        return False
                    else:
                        print(f"\nUnknown error: {error}", file=sys.stderr)
                        return False

                # Success - we have tokens
                if "access_token" in poll_data:
                    print(" OK")

                    # Calculate expiry time
                    expires_at = datetime.now(timezone.utc) + timedelta(seconds=poll_data.get("expires_in", 1800))

                    # Step 5: Save credentials
                    credentials.save_credentials(
                        api_url=api_url,
                        access_token=poll_data["access_token"],
                        refresh_token=poll_data["refresh_token"],
                        expires_at=expires_at.isoformat(),
                    )

                    # Get user info
                    try:
                        user_response = await client.get(
                            "/auth/me",
                            headers={"Authorization": f"Bearer {poll_data['access_token']}"}
                        )
                        if user_response.status_code == 200:
                            user_data = user_response.json()
                            print(f"Logged in as {user_data.get('email', 'unknown')}\n")
                    except Exception:
                        print("Logged in successfully\n")

                    return True

            print("\nTimeout waiting for authorization.", file=sys.stderr)
            return False

    except Exception as e:
        print(f"\nLogin failed: {e}", file=sys.stderr)
        return False


async def password_login_flow(api_url: str, email: str, password: str) -> tuple[int, dict | None]:
    """
    Password-grant login. Tokens are returned to the caller; the caller
    decides where to persist them. The CLI's `bifrost login` command writes
    BIFROST_API_URL + the two tokens to .env in CWD so subsequent commands
    in that directory inherit them. Suitable only for isolated development
    stacks with MFA disabled.

    Returns (exit_code, payload). On success, payload is the parsed JSON
    response from /auth/login containing access_token / refresh_token.
    """
    # Always print the warning before doing anything.
    print(
        "⚠️  Password-grant login is for ephemeral, isolated development stacks only.\n"
        "   Do not run a Bifrost instance with MFA disabled in production.",
        file=sys.stderr,
    )

    api_url = api_url.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=api_url, timeout=30.0) as client:
            # /auth/login uses OAuth2PasswordRequestForm — form-encoded, not JSON
            response = await client.post(
                "/auth/login",
                data={"username": email, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code != 200:
                print(f"Error: /auth/login returned HTTP {response.status_code}", file=sys.stderr)
                return 1, None
            data = response.json()

        # MFA paths
        if data.get("mfa_required") or data.get("mfa_setup_required"):
            print(
                "Error: this instance has MFA enabled. Ephemeral password login only works for "
                "instances with BIFROST_MFA_ENABLED=false. Use `bifrost login` (no flags) for "
                "the browser flow.",
                file=sys.stderr,
            )
            return 2, None

        if "access_token" not in data or "refresh_token" not in data:
            print("Error: /auth/login response missing access_token/refresh_token", file=sys.stderr)
            return 1, None

        return 0, data
    except Exception as e:
        print(f"Error during ephemeral login: {e}", file=sys.stderr)
        return 1, None


def logout_flow(api_url: str | None = None) -> tuple[bool, str | None]:
    """
    Logout by clearing stored credentials for one URL.

    Args:
        api_url: URL to log out from. If None, resolves the same way
            get_credentials() does (env var, then first stored URL).

    Returns:
        (cleared, url) — cleared is True if a record was removed; url is
        the URL whose record was removed (so callers can prompt about .env).
    """
    creds = credentials.get_credentials(api_url)
    if creds:
        target_url = creds.get("api_url") or api_url
        credentials.clear_credentials(target_url)
        print(f"Logged out from {target_url}.")
        return True, target_url
    print("No active session found.")
    return False, None


def _read_env_file(path: pathlib.Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text().splitlines(keepends=True)
    except OSError:
        return []


def _upsert_env_vars(updates: dict[str, str]) -> None:
    """
    Write or update KEY=VALUE pairs in CWD's .env.

    For each key in ``updates``: replaces an existing ``KEY=`` (or
    ``export KEY=``) line if present; otherwise appends.
    """
    cwd = pathlib.Path.cwd()
    env_path = cwd / ".env"
    lines = _read_env_file(env_path)

    for key, value in updates.items():
        new_line = f"{key}={value}\n"
        found = False
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"export {key}="):
                lines[i] = new_line
                found = True
                break
        if not found:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] = lines[-1] + "\n"
            lines.append(new_line)

    env_path.write_text("".join(lines))


def _write_env_url(api_url: str) -> None:
    """
    Write or update `BIFROST_API_URL=<api_url>` in CWD's .env.

    Updates an existing `BIFROST_API_URL=` line if present; otherwise appends.
    Adds `.env` to .gitignore if it isn't already gitignored.
    """
    _upsert_env_vars({"BIFROST_API_URL": api_url})
    cwd = pathlib.Path.cwd()
    env_path = cwd / ".env"
    print(f"Updated {env_path} with BIFROST_API_URL={api_url}")

    # gitignore .env if we're in a git repo and it isn't already ignored
    gitignore = cwd / ".gitignore"
    if gitignore.exists():
        try:
            existing = gitignore.read_text()
            already_ignored = any(
                stripped.strip().rstrip("/") == ".env"
                for stripped in existing.splitlines()
            )
            if not already_ignored:
                with open(gitignore, "a") as f:
                    if not existing.endswith("\n"):
                        f.write("\n")
                    f.write(".env\n")
                print(f"Added .env to {gitignore}")
        except OSError:
            # Best-effort: failing to update .gitignore doesn't affect the
            # token's correctness, only its discoverability. Don't let a
            # permission error break a successful login.
            pass


def _remove_env_url_line(api_url: str) -> bool:
    """
    Remove a `BIFROST_API_URL=<api_url>` line from CWD's .env, if present.

    Returns True if a line was removed.
    """
    env_path = pathlib.Path.cwd() / ".env"
    lines = _read_env_file(env_path)
    if not lines:
        return False
    target = api_url.rstrip("/")
    kept: list[str] = []
    removed = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("BIFROST_API_URL=") or stripped.startswith("export BIFROST_API_URL="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'").rstrip("/")
            if value == target:
                removed = True
                continue
        kept.append(line)
    if not removed:
        return False
    if all(not line.strip() for line in kept):
        env_path.unlink()
    else:
        env_path.write_text("".join(kept))
    return True


def main(args: list[str] | None = None) -> int:
    """
    Main CLI entry point.

    Args:
        args: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code (0 for success, 1 for error)
    """
    if args is None:
        args = sys.argv[1:]

    # No args - show help
    if not args:
        print_help()
        return 0

    # Handle --version / -V before lowercasing (they're flags, not commands)
    if args[0] in ("--version", "-V"):
        from bifrost import __version__
        print(f"bifrost {__version__}")
        return 0

    _check_cli_version()

    try:
        command = args[0].lower()

        if command in ("help", "-h", "--help"):
            print_help()
            return 0

        if command == "login":
            return handle_login(args[1:])

        if command == "logout":
            return handle_logout(args[1:])

        if command == "auth":
            return handle_auth(args[1:])

        if command == "run":
            return handle_run(args[1:])

        if command == "git":
            return handle_git(args[1:])

        if command == "sync":
            return handle_sync(args[1:])

        if command == "push":
            return handle_push(args[1:])

        if command == "pull":
            return handle_pull(args[1:])

        if command == "import":
            from bifrost.commands.import_cmd import handle_import
            return handle_import(args[1:])

        if command == "export":
            from bifrost.commands.export import handle_export
            return handle_export(args[1:])

        if command == "watch":
            return handle_watch(args[1:])

        if command == "api":
            return handle_api(args[1:])

        if command == "migrate-imports":
            return handle_migrate_imports(args[1:])

        if command == "skill":
            from bifrost.skill import handle_skill
            return handle_skill(args[1:])

        # Entity mutation subgroups (bifrost orgs ..., bifrost roles ..., etc.).
        from bifrost.commands import ENTITY_GROUPS, dispatch_entity_subgroup

        if command in ENTITY_GROUPS:
            return dispatch_entity_subgroup(command, args[1:])

        # Unknown command
        print(f"Unknown command: {command}", file=sys.stderr)
        print_help()
        return 1
    except KeyboardInterrupt:
        print()
        return 130


def print_help() -> None:
    """Print CLI help message."""
    print("""
Bifrost CLI - Command-line interface for Bifrost SDK

Usage:
  bifrost <command> [options]

Commands:
  sync        Bidirectional sync between local files and Bifrost platform
  run         Run a workflow directly (silent JSON output) or interactively via browser
  git         Git source control operations (fetch, status, commit, push, resolve, diff, discard)
  push        Push local files to Bifrost platform (alias for sync)
  pull        Pull files from Bifrost platform to local directory (alias for sync)
  export      Export a workspace bundle (optionally portable/scrubbed)
  import      Apply a bundle to the current environment
  watch       Watch for file changes and auto-push
  api         Generic authenticated API request
  migrate-imports  Rewrite "bifrost" imports into user/lucide/router imports
  skill       Install / update / remove agent skills (.claude/skills + .agents/skills)
  login       Authenticate (browser device-code by default; password-grant with --email/--password)
  logout      Clear stored credentials for one URL
  auth        Inspect stored credentials (auth list)
  help        Show this help message

Flags:
  -V, --version   Print the installed CLI version

Entity mutation commands (see 'bifrost <entity> --help'):
  orgs         Manage organizations
  roles        Manage roles
  workflows    Manage workflow lifecycle and role assignments
  forms        Manage forms
  agents       Manage AI agents
  apps         Manage applications and dependencies
  integrations Manage integrations, config schemas, and mappings
  configs      Manage config values
  tables       Manage tables
  events       Manage event sources and subscriptions
  requirements Manage workspace Python requirements.txt (install/list/remove)

Examples:
  bifrost run workflow.py -w greet
  bifrost run workflow.py -w greet -p '{"name": "World"}'
  bifrost run workflow.py -w greet | jq .
  bifrost run workflow.py --interactive
  bifrost git fetch
  bifrost git status
  bifrost git commit -m "sync clients"
  bifrost git push
  bifrost git resolve workflows/billing.py=keep_remote
  bifrost sync
  bifrost sync apps/my-app --mirror
  bifrost push apps/my-app
  bifrost push apps/my-app --mirror
  bifrost pull
  bifrost pull apps/my-app
  bifrost watch
  bifrost watch apps/my-app
  bifrost api GET /api/workflows
  bifrost api POST /api/applications/my-app/validate
  bifrost migrate-imports apps/my-app --dry-run
  bifrost migrate-imports apps/my-app --yes
  bifrost login
  bifrost login --url https://app.gobifrost.com
  bifrost logout

For more information, visit: https://docs.gobifrost.com
""".strip())


def handle_login(args: list[str]) -> int:
    """Handle 'bifrost login' command."""
    api_url = None
    auto_open = True
    email: str | None = None
    password: str | None = None

    i = 0
    while i < len(args):
        arg = args[i]

        if arg in ("--url", "-u"):
            if i + 1 >= len(args):
                print("Error: --url requires a value", file=sys.stderr)
                return 1
            api_url = args[i + 1]
            i += 2
        elif arg in ("--no-browser", "-n"):
            auto_open = False
            i += 1
        elif arg == "--email":
            if i + 1 >= len(args):
                print("Error: --email requires a value", file=sys.stderr)
                return 1
            email = args[i + 1]
            i += 2
        elif arg == "--password":
            if i + 1 >= len(args):
                print("Error: --password requires a value", file=sys.stderr)
                return 1
            password = args[i + 1]
            i += 2
        elif arg in ("--help", "-h"):
            print("""
Usage: bifrost login [options]

Authenticate with Bifrost. Two modes:

Browser (default): Device-code flow; tokens stored in OS keychain (with JSON
                   fallback on headless Linux). Multiple URLs can coexist.
                   On success, writes BIFROST_API_URL=<url> to .env in the
                   current directory so subsequent CLI commands target this
                   instance.
Password: When --email and --password are passed, performs an ephemeral
          password-grant login and writes BIFROST_API_URL +
          BIFROST_ACCESS_TOKEN + BIFROST_REFRESH_TOKEN to .env in the
          current directory. Subsequent CLI commands from this directory
          pick the tokens up automatically. For isolated dev stacks only
          — refuses if MFA is enabled on the instance.

Options:
  --url, -u URL         API URL (default: BIFROST_API_URL or http://localhost:8000)
  --no-browser, -n      Don't automatically open browser (browser mode only)
  --email EMAIL         Email for ephemeral password-grant login
  --password PASSWORD   Password for ephemeral password-grant login
  --help, -h            Show this help message

Examples:
  bifrost login
  bifrost login --url https://app.gobifrost.com
  bifrost login --url http://localhost:38421 \\
                --email dev@gobifrost.com --password password
""".strip())
            return 0
        else:
            print(f"Unknown option: {arg}", file=sys.stderr)
            return 1

    # --email and --password go together. Either both or neither.
    if (email is None) != (password is None):
        print("Error: --email and --password must be used together", file=sys.stderr)
        return 1

    is_password_grant = email is not None and password is not None

    if is_password_grant:
        # Resolve URL: --url > BIFROST_API_URL env var > error. No default.
        if not api_url:
            api_url = os.environ.get("BIFROST_API_URL", "").rstrip("/")
        if not api_url:
            print(
                "Error: password-grant login requires --url or BIFROST_API_URL env var "
                "(no fallback default to avoid logging into the wrong stack)",
                file=sys.stderr,
            )
            return 1

        # email and password are guaranteed non-None by the validation above
        assert email is not None
        assert password is not None
        rc, data = asyncio.run(password_login_flow(api_url, email, password))
        if rc == 0 and data is not None:
            # Persist URL + tokens to CWD's .env so subsequent `bifrost`
            # commands from this directory just work — no shell-eval needed.
            # Isolation is by directory: each sandbox dir has its own .env.
            try:
                _write_env_url(api_url)
                _upsert_env_vars(
                    {
                        "BIFROST_ACCESS_TOKEN": data["access_token"],
                        "BIFROST_REFRESH_TOKEN": data["refresh_token"],
                    }
                )
            except OSError as e:
                print(
                    f"Warning: could not update .env in current directory: {e}",
                    file=sys.stderr,
                )
                # Fall back to printing so the caller can eval them.
                print(f"BIFROST_API_URL={api_url}")
                print(f"BIFROST_ACCESS_TOKEN={data['access_token']}")
                print(f"BIFROST_REFRESH_TOKEN={data['refresh_token']}")
        return rc

    # Browser device-code flow (persistent → keychain or JSON fallback).
    success = asyncio.run(login_flow(api_url=api_url, auto_open=auto_open))
    if not success:
        return 1

    # Wire up the CWD .env so subsequent commands in this folder target this URL.
    # The token lives in the keychain keyed by URL; .env just carries the URL.
    resolved_url = (api_url or os.environ.get("BIFROST_API_URL") or "").rstrip("/")
    if not resolved_url:
        # login_flow's default — match what login_flow used so the .env line agrees
        # with where the token landed.
        resolved_url = "http://localhost:8000"
    try:
        _write_env_url(resolved_url)
    except OSError as e:
        print(f"Warning: could not update .env in current directory: {e}", file=sys.stderr)
    return 0


def handle_logout(args: list[str]) -> int:
    """Handle 'bifrost logout' command."""
    api_url: str | None = None
    no_prompt = False
    yes = False

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--url", "-u"):
            if i + 1 >= len(args):
                print("Error: --url requires a value", file=sys.stderr)
                return 1
            api_url = args[i + 1]
            i += 2
        elif arg == "--yes" or arg == "-y":
            yes = True
            i += 1
        elif arg == "--no-prompt":
            no_prompt = True
            i += 1
        elif arg in ("--help", "-h"):
            print("""
Usage: bifrost logout [options]

Clear stored credentials for one Bifrost URL.

If --url is omitted, logs out from the URL resolved by the same rules as
`bifrost api ...` (BIFROST_API_URL env var, then the first stored URL).

After clearing the keychain entry, if the current directory's .env contains
a BIFROST_API_URL line for that URL, you'll be prompted to remove it.

Options:
  --url, -u URL   Specific URL to log out from
  --yes, -y       Auto-confirm the .env removal prompt
  --no-prompt     Skip the .env prompt entirely (leaves it as-is)
  --help, -h      Show this help message

Examples:
  bifrost logout
  bifrost logout --url https://app.gobifrost.com
""".strip())
            return 0
        else:
            print(f"Unknown option: {arg}", file=sys.stderr)
            return 1

    cleared, target_url = logout_flow(api_url)
    if not cleared or target_url is None:
        return 0

    # Prompt to remove the matching .env line, if present
    if no_prompt:
        return 0
    env_path = pathlib.Path.cwd() / ".env"
    if not env_path.exists():
        return 0
    has_match = any(
        line.lstrip().startswith(("BIFROST_API_URL=", "export BIFROST_API_URL="))
        and line.split("=", 1)[1].strip().strip('"').strip("'").rstrip("/") == target_url.rstrip("/")
        for line in _read_env_file(env_path)
    )
    if not has_match:
        return 0

    if yes:
        confirm = "y"
    else:
        try:
            confirm = input(f"Remove BIFROST_API_URL={target_url} from {env_path}? [y/N] ").strip().lower()
        except EOFError:
            confirm = "n"
    if confirm in ("y", "yes"):
        if _remove_env_url_line(target_url):
            print(f"Removed BIFROST_API_URL line from {env_path}")
    return 0


def handle_auth(args: list[str]) -> int:
    """Handle 'bifrost auth' subcommands."""
    if not args or args[0] in ("--help", "-h", "help"):
        print("""
Usage: bifrost auth <subcommand>

Subcommands:
  list, ls    List all Bifrost URLs with stored credentials

Examples:
  bifrost auth list
""".strip())
        return 0 if args else 1

    sub = args[0].lower()
    if sub in ("list", "ls"):
        urls = credentials.list_credentials()
        if not urls:
            print("No stored credentials.")
            return 0
        # Resolve which URL the CLI would currently use, to mark it.
        env_url = os.environ.get("BIFROST_API_URL", "").rstrip("/")
        for url in urls:
            marker = ""
            if env_url and url.rstrip("/") == env_url:
                marker = "  (current — from BIFROST_API_URL)"
            elif not env_url and url == urls[0]:
                marker = "  (current — first stored)"
            print(f"  {url}{marker}")
        return 0

    print(f"Unknown auth subcommand: {sub}", file=sys.stderr)
    return 1


def _extract_workflow_parameters(func: Any) -> list[dict[str, Any]]:
    """Extract parameter info from a workflow function signature."""
    params = []
    sig = inspect.signature(func)

    for name, param in sig.parameters.items():
        # Skip *args and **kwargs
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        param_info: dict[str, Any] = {
            "name": name,
            "type": "string",  # default
            "required": param.default is inspect.Parameter.empty,
            "label": name.replace("_", " ").title(),
            "default_value": None if param.default is inspect.Parameter.empty else param.default,
        }

        # Try to determine type from annotation
        if param.annotation is not inspect.Parameter.empty:
            annotation = param.annotation
            type_name = getattr(annotation, "__name__", str(annotation))
            if type_name in ("int", "float", "bool", "str"):
                param_info["type"] = type_name
            elif "list" in type_name.lower():
                param_info["type"] = "list"
            elif "dict" in type_name.lower():
                param_info["type"] = "dict"

        params.append(param_info)

    return params


def _run_direct(
    selected_workflow: str,
    workflows: dict[str, Any],
    params: dict[str, Any],
    verbose: bool = False,
    organization_id: str | None = None,
) -> int:
    """
    Run a workflow directly in standalone mode.

    Args:
        selected_workflow: Name of the workflow to run
        workflows: Dict of discovered workflow functions
        params: Parameters to pass to the workflow
        verbose: Whether to print status messages
        organization_id: Optional org ID override (superusers only)

    Returns:
        Exit code (0 for success, 1 for error)
    """
    import uuid

    # Try to authenticate for SDK features (knowledge, ai, etc.)
    # but don't require it — standalone mode can run without API access
    try:
        client = BifrostClient.get_instance(require_auth=True)

        # If --org was specified, fetch context for that org instead
        if organization_id:
            try:
                response = client._sync_http.get(
                    "/api/cli/context",
                    params={"org_id": organization_id},
                )
                if response.status_code == 403:
                    print("Error: --org requires superuser privileges", file=sys.stderr)
                    return 1
                if response.status_code == 404:
                    print(f"Error: Organization {organization_id} not found or inactive", file=sys.stderr)
                    return 1
                if response.status_code >= 400:
                    print(f"Error fetching org context: HTTP {response.status_code}", file=sys.stderr)
                    return 1
                ctx_data = response.json()
                # Override the client's cached context
                client._context = ctx_data
            except Exception as e:
                print(f"Error fetching org context: {e}", file=sys.stderr)
                return 1

        # Set up execution context so context.org_id, context.user_id, etc. work
        try:
            from bifrost._context import set_execution_context
            from bifrost._execution_context import ExecutionContext, Organization

            user_info = client.user
            org_info = client.organization

            org = Organization(
                id=org_info["id"],
                name=org_info.get("name", ""),
                is_active=org_info.get("is_active", True),
                is_provider=org_info.get("is_provider", False),
            ) if org_info else None
            scope = org_info["id"] if org_info else "GLOBAL"

            ctx = ExecutionContext(
                user_id=user_info.get("id", "cli-user"),
                email=user_info.get("email", ""),
                name=user_info.get("name", "CLI User"),
                scope=scope,
                organization=org,
                is_platform_admin=user_info.get("is_superuser", False),
                is_function_key=False,
                execution_id=f"standalone-{uuid.uuid4()}",
                workflow_name=selected_workflow,
            )
            set_execution_context(ctx)
        except Exception:
            pass  # Context setup is best-effort

    except (RuntimeError, Exception):
        if organization_id:
            print("Error: --org requires authentication. Run 'bifrost login' first.", file=sys.stderr)
            return 1
        # Standalone mode works without auth — fall through to local workflow execution.

    if verbose:
        print(f"Running in standalone mode: {selected_workflow}")

    workflow_fn = workflows[selected_workflow]

    try:
        result = asyncio.run(workflow_fn(**params))
        if verbose:
            print(f"Result: {json.dumps(result, indent=2, default=str)}")
        else:
            print(json.dumps(result, default=str))
        return 0
    except Exception as e:
        print(f"Error executing workflow: {e}", file=sys.stderr)
        return 1


def handle_run(args: list[str]) -> int:
    """
    Handle 'bifrost run <file>' command.

    Default behavior: direct execution (silent, pipeable output).
    Use --interactive for browser-based session.

    Args:
        args: Command arguments [file, --workflow, etc.]

    Returns:
        Exit code (0 for success, 1 for error)
    """
    import importlib.util

    if not args or args[0] in ("--help", "-h"):
        print_run_help()
        return 0

    workflow_file = args[0]
    selected_workflow: str | None = None
    no_browser = False
    interactive = False
    verbose = False
    inline_params: dict[str, Any] | None = None
    organization_id: str | None = None

    # Parse arguments
    i = 1
    while i < len(args):
        if args[i] in ("--workflow", "-w"):
            if i + 1 >= len(args):
                print("Error: --workflow requires a value", file=sys.stderr)
                return 1
            selected_workflow = args[i + 1]
            i += 2
        elif args[i] in ("--params", "-p"):
            if i + 1 >= len(args):
                print("Error: --params requires a JSON value", file=sys.stderr)
                return 1
            try:
                inline_params = json.loads(args[i + 1])
            except json.JSONDecodeError as e:
                print(f"Error: Invalid JSON for --params: {e}", file=sys.stderr)
                return 1
            i += 2
        elif args[i] in ("--interactive", "-i"):
            interactive = True
            i += 1
        elif args[i] in ("--verbose", "-v"):
            verbose = True
            i += 1
        elif args[i] in ("--organization-id", "--org"):
            if i + 1 >= len(args):
                print("Error: --organization-id requires a UUID value", file=sys.stderr)
                return 1
            organization_id = args[i + 1]
            i += 2
        elif args[i] in ("--no-browser", "-n"):
            no_browser = True
            i += 1
        elif args[i] in ("--help", "-h"):
            print_run_help()
            return 0
        else:
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            return 1

    # Check file exists
    if not os.path.isfile(workflow_file):
        print(f"Error: File not found: {workflow_file}", file=sys.stderr)
        return 1

    # Get absolute path
    abs_file_path = os.path.abspath(workflow_file)

    # Add current working directory to sys.path for workspace imports
    # This allows workflows to import from their workspace (e.g., `from features.x import y`)
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    # In non-verbose direct mode, suppress decorator warnings before loading the module
    if not interactive and not verbose:
        logging.getLogger("bifrost.decorators").setLevel(logging.ERROR)

    # Load the workflow file
    try:
        spec = importlib.util.spec_from_file_location("workflow_module", abs_file_path)
        if spec is None or spec.loader is None:
            print(f"Error: Cannot load {workflow_file}", file=sys.stderr)
            return 1
        module = importlib.util.module_from_spec(spec)
        sys.modules["workflow_module"] = module
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"Error loading workflow file: {e}", file=sys.stderr)
        return 1

    # Find @workflow decorated functions
    workflows: dict[str, Any] = {}
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and hasattr(obj, "_executable_metadata"):
            workflows[name] = obj

    if not workflows:
        print("Error: No @workflow decorated functions found", file=sys.stderr)
        return 1

    # Validate selected workflow if specified
    if selected_workflow and selected_workflow not in workflows:
        print(
            f"Error: Workflow '{selected_workflow}' not found. Available: {list(workflows.keys())}",
            file=sys.stderr,
        )
        return 1

    # Direct execution mode (default) — requires --workflow
    if not interactive:
        if not selected_workflow:
            print(
                f"Error: --workflow is required. Available workflows: {list(workflows.keys())}. "
                "Use --interactive for browser UI.",
                file=sys.stderr,
            )
            return 1

        params = inline_params if inline_params is not None else {}
        return _run_direct(selected_workflow, workflows, params, verbose=verbose, organization_id=organization_id)

    # Interactive mode (--interactive) — browser-based session
    # Ensure user is authenticated (only needed for API-based flow)
    try:
        client = BifrostClient.get_instance(require_auth=True)
    except RuntimeError as e:
        print(f"Authentication required: {e}", file=sys.stderr)
        return 1

    # Build workflow info for session registration
    workflow_infos = []
    for name, func in workflows.items():
        metadata = getattr(func, "_executable_metadata", None)
        description = ""
        if metadata is not None:
            # WorkflowMetadata is a dataclass, access attributes directly
            description = getattr(metadata, "description", "") or ""
        workflow_infos.append({
            "name": name,
            "description": description,
            "parameters": _extract_workflow_parameters(func),
        })

    # Generate session ID
    session_id = str(uuid4())

    # Run the session flow
    return asyncio.run(_run_session_flow(
        client=client,
        session_id=session_id,
        file_path=abs_file_path,
        workflow_infos=workflow_infos,
        workflows=workflows,
        selected_workflow=selected_workflow,
        no_browser=no_browser,
    ))


async def _run_session_flow(
    client: BifrostClient,
    session_id: str,
    file_path: str,
    workflow_infos: list[dict[str, Any]],
    workflows: dict[str, Any],
    selected_workflow: str | None,
    no_browser: bool,
) -> int:
    """
    Run the session-based workflow execution flow.

    1. Register session with API
    2. Open browser to DevRun page
    3. Poll for pending execution
    4. Execute workflow locally
    5. Post results back to API
    """
    api_url = client.api_url

    # Step 1: Register session
    print(f"Registering session with {len(workflow_infos)} workflow(s)...")
    try:
        response = await client.post(
            "/api/cli/sessions",
            json={
                "session_id": session_id,
                "file_path": file_path,
                "workflows": workflow_infos,
                "selected_workflow": selected_workflow,
            },
        )
        if response.status_code not in (200, 201):
            print(f"Error registering session: {response.status_code} - {response.text}", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"Error registering session: {e}", file=sys.stderr)
        return 1

    # Step 2: Open browser to CLI session page
    session_url = f"{api_url}/cli/{session_id}"
    print(f"\nOpening browser to {session_url}")
    print("Select a workflow and enter parameters in the browser, then click 'Continue'.\n")

    if not no_browser:
        try:
            webbrowser.open(session_url)
        except Exception:
            pass  # Ignore browser open failures

    # Step 3: Poll for pending execution
    print("Waiting for execution", end="", flush=True)
    poll_interval = 2  # seconds
    heartbeat_interval = 5  # seconds
    last_heartbeat = time.time()

    while True:
        await asyncio.sleep(poll_interval)
        print(".", end="", flush=True)

        # Send heartbeat periodically
        if time.time() - last_heartbeat > heartbeat_interval:
            try:
                await client.post(f"/api/cli/sessions/{session_id}/heartbeat")
                last_heartbeat = time.time()
            except Exception:
                pass  # Ignore heartbeat failures

        # Poll for pending execution
        try:
            response = await client.get(f"/api/cli/sessions/{session_id}/pending")

            if response.status_code == 204:
                # No pending execution yet
                continue
            elif response.status_code == 200:
                # Execution is pending!
                pending_data = response.json()
                execution_id = pending_data["execution_id"]
                workflow_name = pending_data["workflow_name"]
                params = pending_data["params"]
                print(f" OK\n\nExecuting workflow: {workflow_name}")
                break
            elif response.status_code == 404:
                print("\n\nSession expired or deleted.", file=sys.stderr)
                return 1
            else:
                print(f"\n\nError polling: {response.status_code}", file=sys.stderr)
                return 1
        except Exception as e:
            print(f"\n\nError polling: {e}", file=sys.stderr)
            return 1

    # Step 4: Execute workflow locally
    workflow_fn = workflows.get(workflow_name)
    if not workflow_fn:
        error_msg = f"Workflow '{workflow_name}' not found in loaded module"
        print(f"Error: {error_msg}", file=sys.stderr)
        await _post_result(client, session_id, execution_id, "Failed", None, error_msg, 0)
        return 1

    start_time = time.time()
    result = None
    error_message = None
    status = "Success"

    try:
        result = await workflow_fn(**params)
        print(f"\nResult: {json.dumps(result, indent=2, default=str)}")
    except Exception as e:
        status = "Failed"
        error_message = str(e)
        print(f"\nError: {error_message}", file=sys.stderr)

    duration_ms = int((time.time() - start_time) * 1000)

    # Step 5: Post results back to API
    await _post_result(client, session_id, execution_id, status, result, error_message, duration_ms)

    return 0 if status == "Success" else 1


async def _post_result(
    client: BifrostClient,
    session_id: str,
    execution_id: str,
    status: str,
    result: Any,
    error_message: str | None,
    duration_ms: int,
) -> None:
    """Post execution result back to API."""
    try:
        await client.post(
            f"/api/cli/sessions/{session_id}/executions/{execution_id}/result",
            json={
                "status": status,
                "result": result,
                "error_message": error_message,
                "duration_ms": duration_ms,
                "logs": [],  # Could collect logs during execution
            },
        )
        print(f"\nExecution completed ({status})")
    except Exception as e:
        print(f"\nWarning: Failed to post result: {e}", file=sys.stderr)


def handle_git(args: list[str]) -> int:
    """
    Handle 'bifrost git <subcommand>' command.

    Dispatches to git subcommands: fetch, status, commit, push, resolve, diff, discard.

    Args:
        args: Subcommand and its arguments

    Returns:
        Exit code
    """
    from .git_commands import (
        EXIT_CLEAN,
        EXIT_ERROR,
        RESOLUTION_MAP,
        run_git_commit,
        run_git_diff,
        run_git_discard,
        run_git_fetch,
        run_git_push,
        run_git_resolve,
        run_git_status,
    )

    if not args or args[0] in ("--help", "-h"):
        _print_git_help()
        return EXIT_CLEAN

    subcmd = args[0].lower()
    sub_args = args[1:]

    try:
        client = BifrostClient.get_instance(require_auth=True)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_ERROR

    if subcmd == "fetch":
        return run_git_fetch(client)

    if subcmd == "status":
        return run_git_status(client)

    if subcmd == "commit":
        # Parse -m "message"
        message = None
        i = 0
        while i < len(sub_args):
            if sub_args[i] in ("-m", "--message"):
                if i + 1 >= len(sub_args):
                    print("Error: -m requires a commit message", file=sys.stderr)
                    return EXIT_ERROR
                message = sub_args[i + 1]
                i += 2
            else:
                print(f"Unknown option: {sub_args[i]}", file=sys.stderr)
                return EXIT_ERROR
        if not message:
            print("Error: commit requires -m <message>", file=sys.stderr)
            return EXIT_ERROR
        return run_git_commit(client, message)

    if subcmd == "push":
        return run_git_push(client)

    if subcmd == "resolve":
        # Parse path=strategy pairs
        resolutions: dict[str, str] = {}
        for arg in sub_args:
            parts = arg.split("=", 1)
            if len(parts) != 2 or parts[1] not in RESOLUTION_MAP:
                print(
                    f"Error: invalid resolution '{arg}'. "
                    "Use path=keep_local or path=keep_remote",
                    file=sys.stderr,
                )
                return EXIT_ERROR
            resolutions[parts[0]] = parts[1]
        if not resolutions:
            print("Error: resolve requires at least one path=strategy argument", file=sys.stderr)
            return EXIT_ERROR
        return run_git_resolve(client, resolutions)

    if subcmd == "diff":
        if not sub_args:
            print("Error: diff requires a file path", file=sys.stderr)
            return EXIT_ERROR
        return run_git_diff(client, sub_args[0])

    if subcmd == "discard":
        if not sub_args:
            print("Error: discard requires at least one file path", file=sys.stderr)
            return EXIT_ERROR
        return run_git_discard(client, sub_args)

    print(f"Unknown git subcommand: {subcmd}", file=sys.stderr)
    _print_git_help()
    return EXIT_ERROR


def _print_git_help() -> None:
    """Print git subcommand help."""
    print("""
Usage: bifrost git <subcommand> [options]

Git source control operations for Bifrost platform.

Subcommands:
  fetch                          Regenerate manifest from DB, fetch remote, show status
  status                         Show changed files and commits ahead/behind
  commit -m "message"            Regenerate manifest, stage, preflight, commit
  push                           Pull remote + push local + import entities (deploy)
  resolve path=strategy [...]    Resolve merge conflicts (keep_local or keep_remote)
  diff <path>                    Show file diff
  discard <path> [...]           Discard working tree changes

Typical workflow:
  bifrost git fetch                     # regenerate manifest, see what's changed
  bifrost git commit -m "sync clients"  # commit DB changes to manifest
  bifrost git push                      # pull + push + import to deploy

Examples:
  bifrost git fetch
  bifrost git status
  bifrost git commit -m "add onboarding workflow"
  bifrost git push
  bifrost git resolve workflows/billing.py=keep_remote
  bifrost git diff .bifrost/workflows.yaml
  bifrost git discard workflows/old.py
""".strip())


def _warn_if_git_workspace(target_path: str) -> None:
    """Print a warning if the target path is inside a git repository."""
    p = pathlib.Path(target_path).resolve()
    # Walk up to find .git/
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            msg = (
                "Warning: target path is inside a local git checkout. "
                "`bifrost push`/`watch` pushes files directly to the platform; "
                "your local commits are not synced."
            )
            warn = f"\033[33m⚠ {msg}\033[0m" if sys.stderr.isatty() else msg
            print(warn, file=sys.stderr)
            return


@dataclass
class _PushWatchArgs:
    """Parsed arguments for push/watch commands."""
    local_path: str = "."
    mirror: bool = False
    validate: bool = False
    force: bool = False


def _parse_push_watch_args(args: list[str]) -> _PushWatchArgs | None:
    """Parse shared arguments for push/watch commands. Returns None on error."""
    result = _PushWatchArgs()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--mirror":
            result.mirror = True
        elif arg == "--validate":
            result.validate = True
        elif arg == "--force":
            result.force = True
        elif arg.startswith("--"):
            print(f"Unknown option: {arg}", file=sys.stderr)
            return None
        elif result.local_path == ".":
            result.local_path = arg
        else:
            print(f"Unexpected argument: {arg}", file=sys.stderr)
            return None
        i += 1
    return result


def handle_push(args: list[str]) -> int:
    """
    Handle 'bifrost push' command.

    Pushes local files to Bifrost _repo/ via per-file writes and manifest import.

    Usage:
      bifrost push <path> [--mirror] [--validate]

    Args:
      path: Local directory to push (defaults to ".")
      --mirror: Make target match source exactly (delete files not present locally)
      --validate: Validate after push (for apps)
    """
    if args and args[0] in ("--help", "-h"):
        print("""
Usage: bifrost push [path] [options]

Push local files to Bifrost platform.

Arguments:
  path                  Local directory to push (default: current directory)

Options:
  --mirror              Make target match source exactly (delete files not present locally)
  --validate            Validate after push (for apps)
  --force               Skip confirmation prompts
  --help, -h            Show this help message

Use 'bifrost watch' for continuous file watching.

Examples:
  bifrost push apps/my-app
  bifrost push apps/my-app --mirror
  bifrost push .
""".strip())
        return 0

    # --watch migration guard before shared parsing
    if args and args[0] == "--watch":
        print("--watch has moved to its own command: bifrost watch", file=sys.stderr)
        return 1

    parsed = _parse_push_watch_args(args)
    if parsed is None:
        return 1

    resolved = pathlib.Path(parsed.local_path).resolve()
    if not resolved.exists() or not resolved.is_dir():
        print(f"Error: {parsed.local_path} is not a valid directory", file=sys.stderr)
        return 1
    if not _ensure_workspace_marker(resolved):
        _print_not_a_workspace_error("push")
        return 1

    # Block push if a watch (or another sync/push) is already running in
    # this workspace — see handle_sync for rationale.
    try:
        lock = WorkspaceLock(resolved, "push").__enter__()
    except WorkspaceLockError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1

    try:
        # Authenticate BEFORE entering asyncio.run() so token refresh works
        # (refresh_tokens() uses asyncio.run() internally, which fails inside a running loop)
        try:
            client = BifrostClient.get_instance(require_auth=True)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        _warn_if_git_workspace(parsed.local_path)

        try:
            return asyncio.run(_sync_files(
                parsed.local_path, mirror=parsed.mirror, validate=parsed.validate, force=parsed.force,
                client=client,
            ))
        except KeyboardInterrupt:
            return 130
    finally:
        lock.__exit__()


def handle_sync(args: list[str]) -> int:
    """
    Handle 'bifrost sync' command.

    Bidirectional sync between local files and Bifrost platform.

    Usage:
      bifrost sync [path] [--mirror] [--validate] [--force]
    """
    if args and args[0] in ("--help", "-h"):
        print("""
Usage: bifrost sync [path] [options]

Bidirectional sync between local files and Bifrost platform.
Compares local and server file states and lets you choose per-file
actions (push, pull, delete, skip) in an interactive TUI.

Arguments:
  path                  Local directory to sync (default: current directory)

Options:
  --mirror              Include server-only files (for pull or delete)
  --validate            Validate after sync (for apps)
  --force               Skip confirmation prompts (use default actions)
  --help, -h            Show this help message

Examples:
  bifrost sync
  bifrost sync apps/my-app
  bifrost sync --mirror
  bifrost sync --force
""".strip())
        return 0

    parsed = _parse_push_watch_args(args)
    if parsed is None:
        return 1

    resolved = pathlib.Path(parsed.local_path).resolve()
    if not resolved.exists() or not resolved.is_dir():
        print(f"Error: {parsed.local_path} is not a valid directory", file=sys.stderr)
        return 1
    if not _ensure_workspace_marker(resolved):
        _print_not_a_workspace_error("sync")
        return 1

    # Block sync if a watch (or another sync) is already running in this
    # workspace — concurrent watch+sync would issue separate session_ids
    # that the server can't dedupe, ping-ponging each other's writes.
    try:
        lock = WorkspaceLock(resolved, "sync").__enter__()
    except WorkspaceLockError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1

    try:
        # Authenticate BEFORE entering asyncio.run()
        try:
            client = BifrostClient.get_instance(require_auth=True)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        _warn_if_git_workspace(parsed.local_path)

        try:
            return asyncio.run(_sync_files(
                parsed.local_path, mirror=parsed.mirror, validate=parsed.validate, force=parsed.force,
                client=client,
            ))
        except KeyboardInterrupt:
            return 130
    finally:
        lock.__exit__()


def handle_watch(args: list[str]) -> int:
    """
    Handle 'bifrost watch' command.

    Watches a Bifrost workspace for file changes and auto-pushes.
    Requires the directory to contain a .bifrost/ directory (workspace root).

    Usage:
      bifrost watch [path] [--mirror] [--validate] [--force]
    """
    if args and args[0] in ("--help", "-h"):
        print("""
Usage: bifrost watch [path] [options]

Watch for file changes and auto-push to Bifrost platform.
Must be run from a Bifrost workspace (directory containing .bifrost/).

Arguments:
  path                  Local directory to watch (default: current directory)

Options:
  --mirror              Make target match source exactly (delete files not present locally)
  --validate            Validate after push (for apps)
  --force               Skip repo dirty check
  --help, -h            Show this help message

Examples:
  bifrost watch
  bifrost watch apps/my-app
  bifrost watch --mirror
""".strip())
        return 0

    parsed = _parse_push_watch_args(args)
    if parsed is None:
        return 1

    # Resolve path and verify .bifrost/ exists
    resolved = pathlib.Path(parsed.local_path).resolve()
    if not resolved.exists() or not resolved.is_dir():
        print(f"Error: {parsed.local_path} is not a valid directory", file=sys.stderr)
        return 1

    if not _ensure_workspace_marker(resolved):
        _print_not_a_workspace_error("watch")
        return 1

    # Acquire the per-workspace lock. Held for the lifetime of the watch
    # process; the kernel releases it on any exit (including crashes), so no
    # cleanup or stale-state recovery is needed.
    try:
        lock = WorkspaceLock(resolved, "watch").__enter__()
    except WorkspaceLockError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1

    try:
        # Authenticate
        try:
            client = BifrostClient.get_instance(require_auth=True)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        _warn_if_git_workspace(parsed.local_path)

        repo_prefix = _detect_repo_prefix(resolved)
        try:
            return asyncio.run(_push_with_precheck(
                parsed.local_path, mirror=parsed.mirror, validate=parsed.validate, watch=True, force=parsed.force,
                client=client,
            ))
        except KeyboardInterrupt:
            print("\nStopping watch...", flush=True)
            try:
                client.post_sync("/api/files/watch", json={
                    "action": "stop", "prefix": repo_prefix,
                })
            except Exception as e:
                # Server may already be unreachable — session expires server-side via TTL
                logger.debug(f"could not notify server of watch stop: {e}")
            return 130
    finally:
        lock.__exit__()


def handle_pull(args: list[str]) -> int:
    """
    Handle 'bifrost pull' command.

    Pulls files from Bifrost platform to local directory. Downloads code files
    from S3 and regenerated manifests from DB. Only transfers changed files
    by comparing local MD5 hashes against S3 ETags.

    Usage:
      bifrost pull [path] [--mirror] [--force]

    Args:
      path: Local directory to pull into (defaults to ".")
      --mirror: Make local match platform exactly (delete local files not on platform)
      --force: Skip overwrite confirmation prompts
    """
    if args and args[0] in ("--help", "-h"):
        print("""
Usage: bifrost pull [path] [options]

Pull files from Bifrost platform to local directory.

Downloads changed code files and updated manifests. Uses MD5/ETag
comparison to only transfer files that differ from local state.

Arguments:
  path                  Local directory to pull into (default: current directory)

Options:
  --mirror              Make local match platform exactly (delete local files not on platform)
  --force               Skip overwrite confirmation prompts
  --help, -h            Show this help message

Overwrite safety:
  - Files with uncommitted git changes trigger a warning
  - Git-tracked files can be recovered from history
  - Non-git files warn about irreversible loss
  - Use --force to skip all prompts

Examples:
  bifrost pull
  bifrost pull apps/my-app
  bifrost pull --force
""".strip())
        return 0

    # Parse args
    local_path = "."
    force = False
    mirror = False
    for arg in args:
        if arg == "--force":
            force = True
        elif arg == "--mirror":
            mirror = True
        elif arg.startswith("--"):
            print(f"Unknown option: {arg}", file=sys.stderr)
            return 1
        elif local_path == ".":
            local_path = arg
        else:
            print(f"Unexpected argument: {arg}", file=sys.stderr)
            return 1

    resolved = pathlib.Path(local_path).resolve()
    if not resolved.exists() or not resolved.is_dir():
        print(f"Error: {local_path} is not a valid directory", file=sys.stderr)
        return 1
    if not _ensure_workspace_marker(resolved):
        _print_not_a_workspace_error("pull")
        return 1

    # Block pull if a watch (or another sync/push/pull) is already running
    # in this workspace — see handle_sync for rationale.
    try:
        lock = WorkspaceLock(resolved, "pull").__enter__()
    except WorkspaceLockError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1

    try:
        # Authenticate
        try:
            client = BifrostClient.get_instance(require_auth=True)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        try:
            return asyncio.run(_sync_files(
                local_path, mirror=mirror, force=force, client=client,
            ))
        except KeyboardInterrupt:
            print("\nPull cancelled.")
            return 130
    finally:
        lock.__exit__()



def _detect_repo_prefix(path: pathlib.Path) -> str:
    """Compute the repo prefix for ``path`` relative to the workspace root.

    With walk-up gone, the workspace root is the current working directory:
    the dir the user invoked the CLI from. The prefix is whatever sub-path
    of that root the caller targeted (e.g. ``bifrost push apps/my-app``
    yields ``apps/my-app``). The launch directory itself yields ``""``.

    Paths that don't sit under the cwd (unusual: an absolute path outside
    the workspace) fall through to ``""`` — the caller is expected to have
    already validated the path via ``_ensure_workspace_marker``.
    """
    try:
        cwd = pathlib.Path.cwd().resolve()
        relative = path.resolve().relative_to(cwd)
    except (OSError, ValueError):
        return ""
    prefix = str(relative)
    return "" if prefix == "." else prefix


_WORKSPACE_SENTINEL = ".workspace"


def _is_workspace_bifrost_dir(d: pathlib.Path) -> bool:
    """Distinguish a workspace ``.bifrost/`` from the CLI config ``~/.bifrost/``.

    A workspace ``.bifrost/`` either contains manifest YAMLs (``tables.yaml``,
    ``workflows.yaml``, etc., produced by sync/export) or the empty
    ``.workspace`` sentinel file (created on first ``bifrost watch`` /
    ``push`` / ``pull`` after the user confirms this is their workspace).

    The CLI config directory at ``~/.bifrost/`` only contains
    ``credentials.json`` and must NOT be treated as a workspace.
    """
    if not d.is_dir():
        return False
    try:
        if (d / _WORKSPACE_SENTINEL).exists():
            return True
        return any(d.glob("*.yaml"))
    except OSError:
        return False


def _find_bifrost_dir(local_root: pathlib.Path) -> pathlib.Path:
    """Return the workspace ``.bifrost/`` for ``local_root``.

    The launch directory IS the workspace root — no walk-up. Either:
    1. ``local_root`` itself IS a workspace ``.bifrost/`` (e.g., user ran
       ``bifrost watch .bifrost``); return it.
    2. ``local_root/.bifrost/`` exists and looks like a workspace; return it.
    3. Otherwise return ``local_root/.bifrost`` as the *expected* path. The
       caller is responsible for verifying existence (and, in interactive
       contexts, prompting the user to mark this directory as a workspace).

    Removing the walk-up eliminates a class of "wrong workspace inferred"
    bugs — most notably the credentials-only ``~/.bifrost/`` collision that
    silently rooted every relative path at ``$HOME``.
    """
    if local_root.name == ".bifrost" and _is_workspace_bifrost_dir(local_root):
        return local_root

    candidate = local_root / ".bifrost"
    if _is_workspace_bifrost_dir(candidate):
        return candidate

    return local_root / ".bifrost"  # Expected path; may not exist


def _ensure_workspace_marker(local_root: pathlib.Path, *, prompt: bool = True) -> bool:
    """Confirm ``local_root`` is the user's workspace and create the marker.

    If ``local_root/.bifrost/`` already looks like a workspace, returns True
    immediately. Otherwise, asks the user (when ``prompt`` is True and stdin
    is a tty) whether to mark this directory. On confirmation, creates
    ``.bifrost/.workspace``. Returns True on success, False if the user
    declined or stdin isn't a tty.
    """
    bifrost_dir = local_root / ".bifrost"
    if _is_workspace_bifrost_dir(bifrost_dir):
        return True

    if not prompt or not sys.stdin.isatty():
        return False

    print(f"No .bifrost/ found in {local_root}.", file=sys.stderr)
    answer = input("Is this your Bifrost workspace? Create a marker so future commands recognize it? [y/N] ").strip().lower()
    if answer not in {"y", "yes"}:
        return False

    bifrost_dir.mkdir(parents=True, exist_ok=True)
    (bifrost_dir / _WORKSPACE_SENTINEL).touch()
    print(f"Marked {local_root} as a Bifrost workspace.", file=sys.stderr)
    return True


def _print_not_a_workspace_error(command: str) -> None:
    """Print the standard 'not a workspace' error for a CLI command."""
    print("Error: not a Bifrost workspace.", file=sys.stderr)
    print(f"  Run 'bifrost {command}' from a directory that contains .bifrost/,", file=sys.stderr)
    print("  or confirm the prompt above to mark this directory.", file=sys.stderr)


async def _push_with_precheck(
    local_path: str,
    mirror: bool = False,
    validate: bool = False,
    watch: bool = False,
    force: bool = False,
    client: "BifrostClient | None" = None,
) -> int:
    """Push files with repo status pre-check."""
    if client is None:
        try:
            client = BifrostClient.get_instance(require_auth=True)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Detect repo prefix for watch mode
    path = pathlib.Path(local_path).resolve()
    repo_prefix = ""
    if path.exists() and path.is_dir():
        repo_prefix = _detect_repo_prefix(path)

    if watch:
        return await _watch_and_push(local_path, repo_prefix=repo_prefix, mirror=mirror, validate=validate, client=client)
    else:
        return await _sync_files(local_path, repo_prefix=repo_prefix, mirror=mirror, validate=validate, force=force, client=client)


class _WatchState:
    """Mutable shared state between the watcher thread and the async main loop."""

    def __init__(self, base_path: pathlib.Path):
        import threading
        self.base_path = base_path
        self.pending_changes: set[str] = set()
        self.pending_deletes: set[str] = set()
        self.lock = threading.Lock()
        # Unique session ID for filtering own changes from WebSocket events
        self.session_id: str = str(uuid4())
        # Incoming changes from other sessions (populated by WebSocket listener)
        self.incoming_files: list[tuple[list[str], str]] = []      # (paths, user_name)
        self.incoming_deletes: list[tuple[list[str], str]] = []     # (paths, user_name)
        # repo_path -> md5 of bytes currently on the server (as best as this
        # session knows). Populated by: successful pushes, incoming pull
        # writes, and the /api/files/list seed at startup. Consulted by the
        # push batcher to drop no-op pushes (the primary fix for pull/write
        # echoes re-pushing pulled content) and by the pull processor to
        # skip no-op writes.
        self.known_server_hashes: dict[str, str] = {}

    def drain(self) -> tuple[set[str], set[str]]:
        """Atomically drain pending changes and deletes."""
        with self.lock:
            changes = self.pending_changes.copy()
            deletes = self.pending_deletes.copy()
            self.pending_changes.clear()
            self.pending_deletes.clear()
        return changes, deletes

    def requeue(self, changes: set[str], deletes: set[str]) -> None:
        """Put changes back for retry."""
        with self.lock:
            self.pending_changes.update(changes)
            self.pending_deletes.update(deletes)

    def queue_incoming_files(self, paths: list[str], user_name: str) -> None:
        """Queue incoming file changes from another session."""
        with self.lock:
            self.incoming_files.append((paths, user_name))

    def queue_incoming_deletes(self, paths: list[str], user_name: str) -> None:
        """Queue incoming file deletes from another session."""
        with self.lock:
            self.incoming_deletes.append((paths, user_name))

    def drain_incoming(self) -> tuple[
        list[tuple[list[str], str]],
        list[tuple[list[str], str]],
    ]:
        """Atomically drain all incoming queues."""
        with self.lock:
            files = self.incoming_files.copy()
            deletes = self.incoming_deletes.copy()
            self.incoming_files.clear()
            self.incoming_deletes.clear()
        return files, deletes

    def get_known_hash(self, repo_path: str) -> str | None:
        with self.lock:
            return self.known_server_hashes.get(repo_path)

    def set_known_hash(self, repo_path: str, hash_hex: str) -> None:
        with self.lock:
            self.known_server_hashes[repo_path] = hash_hex

    def forget_known_hash(self, repo_path: str) -> None:
        with self.lock:
            self.known_server_hashes.pop(repo_path, None)

    def seed_known_hashes(self, pairs: dict[str, str]) -> None:
        with self.lock:
            self.known_server_hashes.update(pairs)


class _WatchChangeHandler:
    """Watchdog event handler that tracks file changes for push.

    Watch is exclusion-based: it watches the workspace root and skips
    .gitignore-derived paths plus .bifrost/. The `.bifrost/` directory is an
    export artifact written by `bifrost export --portable` and consumed by
    `bifrost import`; sync/watch/push/pull never read or mutate it.
    """

    def __init__(self, state: _WatchState):
        import pathspec
        self.state = state
        # Watch spec layers .bifrost/ on top of the shared push/pull filter so
        # observer events under the manifest directory are dropped before they
        # ever reach the handler. The shared filter still force-includes
        # .bifrost/ for full sync paths — only watch excludes it.
        base_lines = list(_DEFAULT_IGNORE_PATTERNS)
        gitignore_path = state.base_path / ".gitignore"
        if gitignore_path.is_file():
            base_lines.extend(gitignore_path.read_text(encoding="utf-8").splitlines())
        base_lines.append(".bifrost/")
        self._spec = pathspec.PathSpec.from_lines("gitwildmatch", base_lines)

    def _should_skip(self, file_path: str) -> bool:
        p = pathlib.Path(file_path)
        rel = str(p.relative_to(self.state.base_path))
        return _should_skip_path(rel, self._spec)

    def dispatch(self, event: Any) -> None:
        """Called by watchdog for all events."""
        if event.is_directory:
            return

        # For moved/renamed events, track the destination as a change.
        # Don't auto-delete the source — it may be a temp file that was
        # never on the server (editors often write to .tmp then rename).
        if event.event_type == "moved":
            dest = str(getattr(event, "dest_path", ""))
            if not dest or self._should_skip(dest):
                return
            with self.state.lock:
                self.state.pending_changes.add(dest)
                self.state.pending_deletes.discard(dest)
            return

        src = str(event.src_path)
        if self._should_skip(src):
            return
        with self.state.lock:
            if event.event_type == "deleted":
                self.state.pending_deletes.add(src)
                self.state.pending_changes.discard(src)
            elif event.event_type in ("created", "modified", "closed"):
                self.state.pending_changes.add(src)
                self.state.pending_deletes.discard(src)


def _extract_error_detail(resp: Any) -> str:
    """Extract human-readable error detail from an HTTP response body."""
    try:
        body = resp.json()
        return body.get("detail", "") or body.get("message", "") or ""
    except Exception:
        try:
            text = resp.text
            return text[:200] if text else ""
        except Exception:
            return ""


async def _process_watch_deletes(
    client: "BifrostClient",
    deletes: set[str],
    base_path: pathlib.Path,
    repo_prefix: str,
    state: _WatchState,
) -> tuple[int, list[str]]:
    """Process pending file deletions. Returns (count, relative_paths)."""
    deleted_count = 0
    deleted_rels: list[str] = []
    extra_headers: dict[str, str] = {}
    if state.session_id:
        extra_headers["X-Bifrost-Watch-Session"] = state.session_id

    for abs_path_str in deletes:
        abs_p = pathlib.Path(abs_path_str)
        if not abs_p.exists():
            rel = abs_p.relative_to(base_path)
            repo_path = f"{repo_prefix}/{rel}" if repo_prefix else str(rel)
            try:
                resp = await client.post("/api/files/delete", json={
                    "path": repo_path, "location": "workspace", "mode": "cloud",
                }, headers=extra_headers)
                if resp.status_code == 204:
                    deleted_count += 1
                    deleted_rels.append(str(rel))
                    state.forget_known_hash(repo_path)
            except Exception as del_err:
                status_code = getattr(getattr(del_err, "response", None), "status_code", None)
                if status_code == 404:
                    deleted_count += 1
                    deleted_rels.append(str(rel))
                    state.forget_known_hash(repo_path)
                else:
                    ts = datetime.now().strftime('%H:%M:%S')
                    print(f"  [{ts}] Delete error for {rel}: {del_err}", flush=True)

    return deleted_count, deleted_rels


async def _process_watch_batch(
    client: "BifrostClient",
    changes: set[str],
    deletes: set[str],
    base_path: pathlib.Path,
    repo_prefix: str,
    state: _WatchState,
    watch_app: "WatchApp | None" = None,
) -> None:
    """Process a batch of file changes and deletions."""
    deleted_count, deleted_rels = await _process_watch_deletes(
        client, deletes, base_path, repo_prefix, state,
    )

    # Build files dict from changed paths. Observer events fire for our own
    # pull writes, too — gate each file on the known-server-hash cache so we
    # don't round-trip content the server already has.
    push_files: dict[str, str] = {}
    push_hashes: dict[str, str] = {}
    for abs_path_str in changes:
        abs_p = pathlib.Path(abs_path_str)
        if abs_p.exists():
            try:
                raw_bytes = abs_p.read_bytes()
                raw = _normalize_line_endings(raw_bytes)
                rel = abs_p.relative_to(base_path)
                repo_path = f"{repo_prefix}/{rel}" if repo_prefix else str(rel)
                file_hash = hashlib.md5(raw).hexdigest()
                if state.get_known_hash(repo_path) == file_hash:
                    # No-op push: the server already has this content (common
                    # case: observer fired on our own pull write).
                    continue
                push_files[repo_path] = base64.b64encode(raw).decode("ascii")
                push_hashes[repo_path] = file_hash
            except OSError:
                continue

    ts = datetime.now().strftime('%H:%M:%S')
    if not watch_app:
        for repo_path in sorted(push_files):
            print(f"  [{ts}] File changed: {repo_path}", flush=True)
        for rel_path in sorted(deleted_rels):
            print(f"  [{ts}] File deleted: {rel_path}", flush=True)

    # Log deletes that already completed (one row per file)
    if deleted_rels:
        for rel_path in sorted(deleted_rels):
            if watch_app:
                watch_app.log_delete(rel_path)
            else:
                if not push_files:
                    # Already printed above for non-TUI
                    pass

    if push_files:
        # Create per-file spinner rows in TUI
        file_rows: dict[str, Any] = {}  # repo_path -> (batch_row, spinner_task)
        if watch_app:
            for rp in sorted(push_files):
                row = watch_app.create_batch_row("Push", rp)
                stask = asyncio.create_task(watch_app.spin_row(row))
                file_rows[rp] = (row, stask)

        # Upload files via per-file writes. Watch only syncs code/data files —
        # .bifrost/ is excluded by the observer, so manifest import never
        # happens here. .bifrost/ round-trips via `bifrost sync` / `bifrost pull`.
        watch_created = 0
        watch_errors: list[str] = []
        for rp, c in push_files.items():
            try:
                resp = await client.post("/api/files/write", json={
                    "path": rp,
                    "content": c,
                    "mode": "cloud",
                    "location": "workspace",
                    "binary": True,
                }, headers={
                    "X-Bifrost-Watch": "true",
                    "X-Bifrost-Watch-Session": state.session_id,
                })
                if resp.status_code == 204:
                    watch_created += 1
                    state.set_known_hash(rp, push_hashes[rp])
                    if rp in file_rows:
                        row, stask = file_rows[rp]
                        stask.cancel()
                        row.freeze("success", "\u2713", "Push", rp)
                else:
                    detail = _extract_error_detail(resp)
                    err_msg = f"{rp}: HTTP {resp.status_code}" + (f" \u2014 {detail}" if detail else "")
                    watch_errors.append(err_msg)
                    if rp in file_rows:
                        row, stask = file_rows[rp]
                        stask.cancel()
                        row.freeze("error", "\u2717", "Push", f"{rp}: HTTP {resp.status_code}")
            except Exception as e:
                watch_errors.append(f"{rp}: {e}")
                if rp in file_rows:
                    row, stask = file_rows[rp]
                    stask.cancel()
                    row.freeze("error", "\u2717", "Push", f"{rp}: {e}")

        # Update sync status
        if watch_app and not watch_errors:
            watch_app._last_sync = datetime.now().strftime("%H:%M:%S")
            watch_app._update_status()

        if not watch_app:
            ts = datetime.now().strftime('%H:%M:%S')
            parts = []
            if watch_created:
                parts.append(f"{watch_created} written")
            if deleted_count:
                parts.append(f"{deleted_count} deleted")
            print(f"  [{ts}] \u2713 Pushed {', '.join(parts) if parts else 'no changes'}", flush=True)

        # Log errors as separate rows (with detail sub-rows in TUI)
        if watch_errors:
            for error in watch_errors:
                if watch_app:
                    if "\u2014" in error:
                        summary, detail = error.split("\u2014", 1)
                        watch_app.log_error_detail(summary.strip(), detail.strip())
                    else:
                        watch_app.log_error(error)
                else:
                    _cols = shutil.get_terminal_size((80, 24)).columns
                    print(textwrap.fill(f"Error: {error}", width=_cols, initial_indent="    ", subsequent_indent="      "), flush=True)

        # Auto-validate app directories after push (non-blocking)
        await _auto_validate_app(client, push_files, repo_prefix, watch_app=watch_app)


async def _auto_validate_app(
    client: "BifrostClient",
    pushed_files: dict[str, str],
    repo_prefix: str,
    watch_app: "WatchApp | None" = None,
) -> None:
    """Auto-validate if pushed files belong to an app directory."""
    # Detect app paths from the pushed files
    app_slugs: set[str] = set()
    for rp in pushed_files:
        # Match apps/{slug}/... pattern
        if rp.startswith("apps/") or (repo_prefix and rp.startswith(f"{repo_prefix}apps/")):
            # Extract slug: apps/{slug}/... or {prefix}apps/{slug}/...
            stripped = rp
            if repo_prefix and stripped.startswith(repo_prefix):
                stripped = stripped[len(repo_prefix):]
            parts = stripped.split("/")
            if len(parts) >= 2 and parts[0] == "apps":
                app_slugs.add(parts[1])
        # Also detect if repo_prefix itself IS an app dir (e.g. watching apps/my-app/)
        elif any(rp.endswith(f) for f in ("_layout.tsx", "pages/index.tsx")):
            if repo_prefix:
                slug = repo_prefix.rstrip("/").rsplit("/", 1)[-1]
                app_slugs.add(slug)

    for slug in app_slugs:
        try:
            val_response = await client.get(f"/api/applications/{slug}")
            if val_response.status_code != 200:
                continue
            app_data = val_response.json()
            app_id = app_data.get("id")
            if not app_id:
                continue
            val_result = await client.post(f"/api/applications/{app_id}/validate")
            if val_result.status_code != 200:
                continue
            val_data = val_result.json()
            errors = val_data.get("errors", [])
            warnings = val_data.get("warnings", [])
            if not errors and not warnings:
                msg = f"App '{slug}' validated — no issues"
                if watch_app:
                    watch_app.log_success(msg)
                else:
                    ts = datetime.now().strftime('%H:%M:%S')
                    print(f"  [{ts}] \u2713 {msg}", flush=True)
            else:
                if errors:
                    msg = f"App '{slug}' validation: {len(errors)} error(s)"
                    if watch_app:
                        watch_app.log_error(msg)
                    else:
                        ts = datetime.now().strftime('%H:%M:%S')
                        print(f"  [{ts}] \u2717 {msg}", flush=True)
                    for err in errors:
                        err_msg = f"  {err.get('file', '?')}: {err.get('message', str(err))}"
                        if watch_app:
                            watch_app.log_error(err_msg)
                        else:
                            print(f"    {err_msg}", flush=True)
                if warnings:
                    msg = f"App '{slug}' validation: {len(warnings)} warning(s)"
                    if watch_app:
                        watch_app.log_info(msg)
                    else:
                        ts = datetime.now().strftime('%H:%M:%S')
                        print(f"  [{ts}] \u26a0 {msg}", flush=True)
                    for warn in warnings:
                        warn_msg = f"  {warn.get('file', '?')}: {warn.get('message', str(warn))}"
                        if watch_app:
                            watch_app.log_info(warn_msg)
                        else:
                            print(f"    {warn_msg}", flush=True)
        except Exception as e:
            # Non-blocking — don't fail the watch loop on validation errors
            if watch_app:
                watch_app.log_error(f"Auto-validate '{slug}' failed: {e}")
            else:
                ts = datetime.now().strftime('%H:%M:%S')
                print(f"  [{ts}] \u26a0 Auto-validate '{slug}' failed: {e}", flush=True)


async def _ws_listener(state: _WatchState, client: "BifrostClient") -> None:
    """Listen for file-activity WebSocket events from other sessions."""
    try:
        import websockets
    except ImportError:
        print("  ⚠ 'websockets' not installed — bidirectional sync disabled", flush=True)
        print("  Reinstall CLI: pipx install --force <url>", flush=True)
        return

    ws_url = client.api_url.replace("https://", "wss://").replace("http://", "ws://")
    ws_url += "/ws/connect?channels=file-activity"
    backoff = 1.0
    connected_once = False

    while True:
        try:
            async with websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {client._access_token}"},
            ) as ws:
                backoff = 1.0  # Reset on successful connect
                if not connected_once:
                    print("  WebSocket connected — listening for remote changes", flush=True)
                    connected_once = True
                async for msg in ws:
                    try:
                        event = json.loads(msg)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    # Ignore events from our own session
                    if event.get("session_id") == state.session_id:
                        continue

                    evt_type = event.get("type", "")
                    if evt_type == "file_push":
                        paths = event.get("paths", [])
                        user_name = event.get("user_name", "unknown")
                        if paths:
                            state.queue_incoming_files(paths, user_name)
                    elif evt_type == "file_delete":
                        paths = event.get("paths", [])
                        user_name = event.get("user_name", "unknown")
                        if paths:
                            state.queue_incoming_deletes(paths, user_name)
        except asyncio.CancelledError:
            return
        except Exception as e:
            # Handle token expiry (server sends close code 4001)
            close_code = getattr(getattr(e, "rcvd", None), "code", None)
            if close_code == 4001:
                if await client._refresh_and_update():
                    print("  WebSocket token refreshed — reconnecting", flush=True)
                    backoff = 1.0
                    continue
                else:
                    print("  WebSocket token expired and refresh failed — re-authenticate with `bifrost auth`", flush=True)
                    return
            print(f"  WebSocket error: {e} — reconnecting in {backoff:.0f}s", flush=True)
            await asyncio.sleep(min(backoff, 30))
            backoff *= 2


async def _process_incoming(
    client: "BifrostClient",
    files: list[tuple[list[str], str]],
    deletes: list[tuple[list[str], str]],
    base_path: pathlib.Path,
    repo_prefix: str,
    state: _WatchState,
    watch_app: "WatchApp | None" = None,
) -> None:
    """Process incoming file changes/deletes from other sessions.

    Writes here fire the watchdog observer, which would normally cause the
    push batcher to POST the pulled content back. The known-hash cache on
    `state` blocks that — we record each pulled file's hash before/after
    writing so the subsequent observer event sees a matching cache entry
    and drops the no-op push.
    """
    ts = datetime.now().strftime('%H:%M:%S')

    # Process incoming file changes
    for paths, user_name in files:
        for repo_path in paths:
            try:
                resp = await client.post("/api/files/read", json={
                    "path": repo_path,
                    "mode": "cloud",
                    "location": "workspace",
                    "binary": True,
                })
                if resp.status_code == 200:
                    data = resp.json()
                    content = base64.b64decode(data["content"])
                    content_hash = _hash_for_cache(content)
                    # Convert repo_path to local path
                    if repo_prefix and repo_path.startswith(repo_prefix + "/"):
                        rel = repo_path[len(repo_prefix) + 1:]
                    elif repo_prefix and repo_path.startswith(repo_prefix):
                        rel = repo_path[len(repo_prefix):]
                    else:
                        rel = repo_path
                    local_file = base_path / rel
                    local_file.parent.mkdir(parents=True, exist_ok=True)
                    # Skip if we already know the server has this content and
                    # the local file matches (cache hit). Falls back to a byte
                    # compare when the cache has no entry.
                    if state.get_known_hash(repo_path) == content_hash:
                        continue
                    if local_file.exists():
                        try:
                            if local_file.read_bytes() == content:
                                state.set_known_hash(repo_path, content_hash)
                                continue
                        except OSError as e:
                            # Permission / I/O issue reading existing file — fall through to overwrite
                            logger.debug(f"could not byte-compare {local_file}, will overwrite: {e}")
                    # Set the cache hash BEFORE the disk write to close the
                    # race window: watchdog runs in a separate thread and can
                    # enqueue an event the moment write_bytes flushes — which
                    # may be before this asyncio task gets to set_known_hash.
                    # If the write later fails, forget the entry so the next
                    # cycle re-pushes whatever's on disk.
                    state.set_known_hash(repo_path, content_hash)
                    try:
                        local_file.write_bytes(content)
                    except OSError:
                        state.forget_known_hash(repo_path)
                        raise
                    if watch_app:
                        watch_app.log_pull(rel, user=user_name)
                    else:
                        print(f"  [{ts}] \u2190 {user_name}: {rel}", flush=True)
            except Exception as e:
                if watch_app:
                    watch_app.log_error(f"Error pulling {repo_path}: {e}")
                else:
                    print(f"  [{ts}] \u2190 Error pulling {repo_path}: {e}", flush=True)

    # Process incoming deletes
    for paths, user_name in deletes:
        for repo_path in paths:
            if repo_prefix and repo_path.startswith(repo_prefix + "/"):
                rel = repo_path[len(repo_prefix) + 1:]
            elif repo_prefix and repo_path.startswith(repo_prefix):
                rel = repo_path[len(repo_prefix):]
            else:
                rel = repo_path
            local_file = base_path / rel
            if local_file.exists():
                try:
                    local_file.unlink()
                    state.forget_known_hash(repo_path)
                    if watch_app:
                        watch_app.log_delete(rel, user=user_name)
                    else:
                        print(f"  [{ts}] \u2190 {user_name} deleted: {rel}", flush=True)
                except OSError as e:
                    if watch_app:
                        watch_app.log_error(f"Error deleting {rel}: {e}")
                    else:
                        print(f"  [{ts}] \u2190 Error deleting {rel}: {e}", flush=True)
            else:
                # File wasn't local, but still drop any stale cache entry.
                state.forget_known_hash(repo_path)


async def _watch_loop(
    path: pathlib.Path,
    repo_prefix: str,
    client: "BifrostClient",
    state: _WatchState,
    observer: Any,
    handler: Any,
    ws_task: "asyncio.Task[None] | None",
    watch_app: "WatchApp | None" = None,
) -> None:
    """Core watch loop — runs either standalone or inside a WatchApp."""
    heartbeat_interval = WATCH_HEARTBEAT_SECONDS
    last_heartbeat = asyncio.get_event_loop().time()
    consecutive_errors = 0

    try:
        while True:
            await asyncio.sleep(0.5)

            # Restart observer if thread died
            if not observer.is_alive():
                if watch_app:
                    watch_app.log_error("File watcher died, attempting restart...")
                else:
                    print("  \u26a0 File watcher died, attempting restart...", flush=True)
                try:
                    from watchdog.observers import Observer
                    observer = Observer()
                    observer.schedule(handler, str(path), recursive=True)
                    observer.start()
                    if watch_app:
                        watch_app.log_success("File watcher restarted")
                    else:
                        print("  \u2713 File watcher restarted", flush=True)
                except Exception as e:
                    if watch_app:
                        watch_app.log_error(f"Could not restart file watcher: {e}")
                    else:
                        print(f"  \u2717 Could not restart file watcher: {e}", file=sys.stderr, flush=True)
                    break

            changes, deletes = state.drain()
            if changes or deletes:
                try:
                    await _process_watch_batch(client, changes, deletes, path, repo_prefix, state, watch_app=watch_app)
                    consecutive_errors = 0
                except KeyboardInterrupt:
                    raise
                except Exception as batch_err:
                    consecutive_errors += 1
                    if watch_app:
                        watch_app.log_error(f"Push error: {batch_err}")
                    else:
                        ts = datetime.now().strftime('%H:%M:%S')
                        print(f"  [{ts}] Push error: {batch_err}", flush=True)
                    state.requeue(changes, deletes)
                    if consecutive_errors >= 10:
                        if watch_app:
                            watch_app.log_error(f"{consecutive_errors} consecutive errors, backing off to 5s")
                        else:
                            ts = datetime.now().strftime('%H:%M:%S')
                            print(f"  [{ts}] \u26a0 {consecutive_errors} consecutive errors, backing off to 5s", flush=True)
                        await asyncio.sleep(5)

            # Process incoming file changes from other sessions. Writes land
            # on disk, the observer re-emits them to the push batcher, and
            # the known-hash cache on `state` drops the would-be echo.
            inc_files, inc_deletes = state.drain_incoming()
            if inc_files or inc_deletes:
                await _process_incoming(
                    client, inc_files, inc_deletes, path, repo_prefix,
                    state, watch_app=watch_app,
                )

            # Heartbeat
            now = asyncio.get_event_loop().time()
            if now - last_heartbeat > heartbeat_interval:
                try:
                    await client.post("/api/files/watch", json={
                        "action": "heartbeat", "prefix": repo_prefix, "session_id": state.session_id,
                    })
                except Exception as e:
                    # Heartbeat is best-effort — server-side TTL keeps things consistent
                    logger.debug(f"watch heartbeat failed: {e}")
                last_heartbeat = now

    except (KeyboardInterrupt, asyncio.CancelledError):
        # Expected on Ctrl-C / cancel — graceful exit
        pass
    finally:
        if ws_task and not ws_task.done():
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                # Expected — we just cancelled the websocket task
                pass
            except Exception as e:
                # Unexpected close error during cancel — log but continue cleanup
                logger.debug(f"websocket task cleanup raised: {e}")
        observer.stop()
        observer.join()


async def _watch_and_push(
    local_path: str,
    repo_prefix: str,
    mirror: bool,
    validate: bool,
    client: "BifrostClient",
) -> int:
    """Watch directory for changes and auto-push."""
    from watchdog.observers import Observer

    path = pathlib.Path(local_path).resolve()
    if not path.exists() or not path.is_dir():
        print(f"Error: {local_path} is not a valid directory", file=sys.stderr)
        return 1

    # Set up file watcher state (generates session_id)
    state = _WatchState(path)

    # Notify server with session_id
    try:
        await client.post("/api/files/watch", json={
            "action": "start", "prefix": repo_prefix, "session_id": state.session_id,
        })
    except Exception as e:
        # Best-effort start notification — server tolerates clients that didn't announce
        logger.debug(f"watch start notification failed: {e}")

    # Initial full sync
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(f"Initial sync of {path}...", flush=True)
    await _sync_files(str(path), repo_prefix=repo_prefix, mirror=mirror, validate=validate, client=client)

    # Seed the known-server-hash cache from the server's file listing before
    # the observer starts. Without this, the very first observer event for
    # any already-synced file (editors "touch" on open) would push a no-op.
    # Best-effort — failure just means cold start pushes until the first
    # pull/push per path populates the cache.
    try:
        seed_resp = await client.post("/api/files/list", json={
            "include_metadata": True,
            "mode": "cloud",
            "location": "workspace",
        })
        if seed_resp.status_code == 200:
            seed_data = seed_resp.json()
            state.seed_known_hashes({
                item["path"]: item["etag"]
                for item in seed_data.get("files_metadata", [])
                if item.get("path") and item.get("etag")
            })
    except Exception as e:
        # Hash cache seeding is an optimization — cold start just falls back to byte-compare
        logger.debug(f"could not seed known-hash cache from server: {e}")

    # Set up file watcher
    handler = _WatchChangeHandler(state)
    observer = Observer()
    observer.schedule(handler, str(path), recursive=True)
    observer.start()

    # Start WebSocket listener for incoming changes from other sessions
    ws_task: asyncio.Task[None] | None = None
    try:
        ws_task = asyncio.create_task(
            _ws_listener(state, client)
        )
    except Exception:
        pass  # WebSocket listener is best-effort

    if sys.stdin.isatty() and sys.stdout.isatty():
        from bifrost.tui.watch import WatchApp
        app = WatchApp(str(path), state.session_id)
        app.set_work(
            _watch_loop(path, repo_prefix, client, state, observer, handler, ws_task, watch_app=app)
        )
        await app.run_async()
    else:
        print(f"Watching {path} for changes... (Ctrl+C to stop)", flush=True)
        print(f"  Bidirectional sync enabled (session {state.session_id[:8]})", flush=True)
        await _watch_loop(path, repo_prefix, client, state, observer, handler, ws_task, watch_app=None)

    return 0


def _format_file_time(file_path: pathlib.Path) -> str:
    """Format a file's modification time for display (short format)."""
    try:
        mtime = file_path.stat().st_mtime
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        return dt.strftime("%b %d %H:%M")
    except OSError:
        return "unknown"


def _format_server_time(iso_str: str) -> str:
    """Format an ISO 8601 timestamp for display (short format)."""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%b %d %H:%M")
    except (ValueError, TypeError):
        return "unknown"


def _strip_repo_prefix(repo_path: str, repo_prefix: str) -> str:
    """Strip the repo prefix from a repo path to get the local relative path."""
    if repo_prefix and repo_path.startswith(repo_prefix + "/"):
        return repo_path[len(repo_prefix) + 1:]
    if repo_prefix and repo_path.startswith(repo_prefix):
        return repo_path[len(repo_prefix):]
    return repo_path


def _should_skip_path(rel_path: str, spec: "pathspec.PathSpec") -> bool:
    """Check if a relative path should be skipped during push/watch."""
    return spec.match_file(rel_path)


def _collect_push_files(
    path: pathlib.Path,
    repo_prefix: str,
) -> tuple[dict[str, str], int]:
    """Walk a directory and collect text files for push.

    Returns (files_dict, skipped_count).
    """
    files: dict[str, str] = {}
    skipped = 0
    spec = _build_file_filter(path)

    for file_path in sorted(path.rglob("*")):
        if file_path.is_dir():
            continue
        rel = file_path.relative_to(path)
        rel_str = str(rel)
        if spec.match_file(rel_str):
            continue
        try:
            raw = _normalize_line_endings(file_path.read_bytes())
            content = base64.b64encode(raw).decode("ascii")
            repo_path = f"{repo_prefix}/{rel_str}" if repo_prefix else rel_str
            files[repo_path] = content
        except OSError:
            skipped += 1
            continue

    return files, skipped


async def _sync_files(
    local_path: str,
    repo_prefix: str = "",
    mirror: bool = False,
    validate: bool = False,
    force: bool = False,
    client: "BifrostClient | None" = None,
) -> int:
    """Unified bidirectional sync between local directory and Bifrost platform.

    Compares local files vs server state (MD5/ETag + timestamps) and presents
    a TUI for per-item actions (push/pull/delete/skip). Entity state is
    managed separately via `bifrost export` / `bifrost import` and dedicated
    mutation commands (`bifrost orgs`, `bifrost workflows`, etc.); this
    function does not touch `.bifrost/` manifests.
    """
    path = pathlib.Path(local_path).resolve()

    if not path.exists():
        print(f"Error: path does not exist: {local_path}", file=sys.stderr)
        return 1
    if not path.is_dir():
        print(f"Error: path is not a directory: {local_path}", file=sys.stderr)
        return 1

    if client is None:
        client = BifrostClient.get_instance(require_auth=True)

    if not repo_prefix:
        repo_prefix = _detect_repo_prefix(path)

    # ── 1. Collect local files ───────────────────────────────────────────
    files, skipped = _collect_push_files(path, repo_prefix)
    regular_files = {k: v for k, v in files.items() if not _is_bifrost_path(k)}

    # ── 2. Fetch server file metadata ────────────────────────────────────
    server_metadata: dict[str, dict[str, str]] = {}
    try:
        resp = await client.post("/api/files/list", json={
            "include_metadata": True,
            "mode": "cloud",
            "location": "workspace",
        })
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("files_metadata", []):
                server_metadata[item["path"]] = {
                    "etag": item["etag"],
                    "last_modified": item["last_modified"],
                    "updated_by": item.get("updated_by", ""),
                }
    except Exception as e:
        # Without metadata we'll push everything — slower but still correct
        logger.debug(f"could not fetch server file metadata, will push without diff: {e}")

    # Filter .git/ objects from server listing
    server_metadata = {
        p: m for p, m in server_metadata.items() if not p.startswith(".git/")
    }

    # ── 3. Compare files (local vs server) ───────────────────────────────
    spec = _build_file_filter(path)
    sync_items: list[dict[str, Any]] = []

    # Track which server paths we've matched to a local file
    matched_server_paths: set[str] = set()

    for repo_path, content in regular_files.items():
        rel = _strip_repo_prefix(repo_path, repo_prefix)
        local_md5 = hashlib.md5(base64.b64decode(content)).hexdigest()
        server_info = server_metadata.get(repo_path)

        if server_info is None:
            # New locally — not on server
            sync_items.append({
                "name": rel,
                "why": "new locally",
                "modified": _format_file_time(path / rel),
                "author": "",
                "default_action": "push",
                "valid_actions": ["push", "delete", "skip"],
                "section": "files",
                "repo_path": repo_path,
                "rel": rel,
                "_content": content,
            })
        elif server_info["etag"] != local_md5:
            matched_server_paths.add(repo_path)
            # Content differs — check timestamps
            local_file = path / rel
            try:
                local_mtime = local_file.stat().st_mtime
                local_dt = datetime.fromtimestamp(local_mtime, tz=timezone.utc)
            except OSError:
                local_dt = datetime.min.replace(tzinfo=timezone.utc)

            try:
                server_dt = datetime.fromisoformat(server_info["last_modified"])
                if server_dt.tzinfo is None:
                    server_dt = server_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                server_dt = datetime.min.replace(tzinfo=timezone.utc)

            if local_dt > server_dt:
                why = "local newer"
                default_action = "push"
            else:
                why = "server newer"
                default_action = "pull"

            sync_items.append({
                "name": rel,
                "why": why,
                "modified": _format_file_time(local_file),
                "author": server_info.get("updated_by", ""),
                "default_action": default_action,
                "valid_actions": ["push", "pull", "skip"],
                "section": "files",
                "repo_path": repo_path,
                "rel": rel,
                "_content": content,
            })
        else:
            # Unchanged
            matched_server_paths.add(repo_path)

    # Server-only files — always show for pull; --mirror adds delete option
    prefix_filter = repo_prefix + "/" if repo_prefix else ""
    for server_path, server_info in server_metadata.items():
        if prefix_filter and not server_path.startswith(prefix_filter):
            continue
        if server_path in matched_server_paths:
            continue
        if server_path in files:
            continue
        rel = _strip_repo_prefix(server_path, repo_prefix)
        if _is_bifrost_path(rel):
            continue
        if _should_skip_path(rel, spec):
            continue
        sync_items.append({
            "name": rel,
            "why": "server only",
            "modified": _format_server_time(server_info.get("last_modified", "")),
            "author": server_info.get("updated_by", ""),
            "default_action": "pull",
            "valid_actions": ["pull", "delete", "skip"] if mirror else ["pull", "skip"],
            "section": "files",
            "repo_path": server_path,
            "rel": rel,
            "_content": "",
        })

    # ── 4. Check if there's anything to sync ─────────────────────────────
    if not sync_items:
        print("Already up to date.")
        return 0

    unchanged = len(regular_files) - len(sync_items)

    subtitle = f"Scanned {len(regular_files)} file(s), {unchanged} unchanged"
    if skipped:
        subtitle += f", {skipped} skipped"

    # ── 6. Interactive TUI or auto-accept ────────────────────────────────
    _is_tty = sys.stdin.isatty() and sys.stdout.isatty()

    if force or not _is_tty:
        # Auto-accept: use default actions
        from bifrost.tui.sync_app import SyncResult
        result = SyncResult()
        for item in sync_items:
            action = item.get("default_action", "skip")
            bucket = getattr(result, action, None)
            if bucket is not None:
                bucket.append(item)
            else:
                result.skip.append(item)
        if not _is_tty:
            push_count = len(result.push)
            pull_count = len(result.pull)
            delete_count = len(result.delete)
            parts = []
            if push_count:
                parts.append(f"{push_count} to push")
            if pull_count:
                parts.append(f"{pull_count} to pull")
            if delete_count:
                parts.append(f"{delete_count} to delete")
            print(f"Syncing: {', '.join(parts) if parts else 'nothing'}...")
    else:
        from bifrost.tui.sync_app import interactive_sync
        sync_result = await interactive_sync(
            sync_items,
            file_count=len(sync_items),
            subtitle=subtitle,
        )
        if sync_result is None:
            print("Sync cancelled.")
            return 0
        result = sync_result

    # ── 6. Execute actions ───────────────────────────────────────────────
    if not (result.push or result.pull or result.delete):
        print("Nothing selected.")
        return 0

    progress_items: list[tuple[str, dict[str, Any]]] = []
    for item in result.push:
        progress_items.append((f"Push {item['rel']}", {"action": "push_file", "item": item}))
    for item in result.pull:
        progress_items.append((f"Pull {item['rel']}", {"action": "pull_file", "item": item}))
    for item in result.delete:
        progress_items.append((f"Delete {item['rel']}", {"action": "delete_file", "item": item}))

    async def _do_sync_work(work_data: dict[str, Any], name: str) -> None:
        action = work_data["action"]

        if action == "push_file":
            item = work_data["item"]
            resp = await client.post("/api/files/write", json={
                "path": item["repo_path"],
                "content": item["_content"],
                "mode": "cloud", "location": "workspace", "binary": True,
            })
            if resp.status_code != 204:
                raise RuntimeError(f"HTTP {resp.status_code}")

        elif action == "pull_file":
            item = work_data["item"]
            resp = await client.post("/api/files/read", json={
                "path": item["repo_path"],
                "mode": "cloud", "location": "workspace", "binary": True,
            })
            if resp.status_code == 200:
                file_data = resp.json()
                content_bytes = base64.b64decode(file_data["content"])
                local_file = path / item["rel"]
                local_file.parent.mkdir(parents=True, exist_ok=True)
                local_file.write_bytes(content_bytes)
            else:
                raise RuntimeError(f"HTTP {resp.status_code}")

        elif action == "delete_file":
            item = work_data["item"]
            # Delete locally-only files (new locally + user chose delete)
            if item.get("why") == "new locally":
                local_file = path / item["rel"]
                if local_file.exists():
                    local_file.unlink()
            else:
                # Delete from server
                resp = await client.post("/api/files/delete", json={
                    "path": item["repo_path"],
                    "mode": "cloud", "location": "workspace",
                })
                if resp.status_code not in (204, 404):
                    raise RuntimeError(f"HTTP {resp.status_code}")

    async def _post_sync(file_errors: list[str]) -> str:
        """Compute summary string — runs after all progress items complete."""
        error_names = {e.split(":")[0] for e in file_errors}
        n_pushed = sum(1 for n, d in progress_items if d["action"] == "push_file" and n not in error_names)
        n_pulled = sum(1 for n, d in progress_items if d["action"] == "pull_file" and n not in error_names)
        n_deleted = sum(1 for n, d in progress_items if d["action"] == "delete_file" and n not in error_names)

        parts = []
        if n_pushed:
            parts.append(f"{n_pushed} pushed")
        if n_pulled:
            parts.append(f"{n_pulled} pulled")
        if n_deleted:
            parts.append(f"{n_deleted} deleted")
        if unchanged > 0:
            parts.append(f"{unchanged} unchanged")
        return ", ".join(parts) if parts else "No changes"

    errors: list[str] = []
    if progress_items and _is_tty:
        from bifrost.tui.progress import ProgressApp
        app = ProgressApp("Syncing", progress_items, _do_sync_work, post_fn=_post_sync)
        errors = await app.run_async() or []
    elif progress_items:
        for name, data in progress_items:
            try:
                await _do_sync_work(data, name)
            except Exception as e:
                errors.append(f"{name}: {e}")
                print(f"  Error: {name}: {e}", file=sys.stderr)
        summary = await _post_sync(errors)
        print(f"  \u2713 {summary}")

    _cols = shutil.get_terminal_size((80, 24)).columns
    if errors:
        print(f"\n  Errors ({len(errors)}):")
        for error in errors:
            print(textwrap.fill(f"- {error}", width=_cols, initial_indent="    ", subsequent_indent="      "))

    # Validate if requested
    if validate and repo_prefix:
        slug = repo_prefix.rstrip("/").rsplit("/", 1)[-1]
        print(f"\nValidating app '{slug}'...")
        try:
            val_response = await client.get(f"/api/applications/{slug}")
            if val_response.status_code == 200:
                app_data = val_response.json()
                app_id = app_data.get("id")
                if app_id:
                    val_result = await client.post(f"/api/applications/{app_id}/validate")
                    if val_result.status_code == 200:
                        val_data = val_result.json()
                        if val_data.get("errors"):
                            print(f"  Errors ({len(val_data['errors'])}):")
                            for err in val_data["errors"]:
                                print(f"    - [{err.get('severity', 'error')}] {err.get('message', err)}")
                        elif val_data.get("warnings"):
                            print(f"  Warnings ({len(val_data['warnings'])}):")
                            for warn in val_data["warnings"]:
                                print(f"    - {warn.get('message', warn)}")
                        else:
                            print("  No issues found.")
                    else:
                        print(f"  Validation failed: {val_result.status_code}", file=sys.stderr)
            else:
                print(f"  Could not find app '{slug}' for validation", file=sys.stderr)
        except Exception as e:
            print(f"  Validation error: {e}", file=sys.stderr)

    return 0 if not errors else 1


def handle_api(args: list[str]) -> int:
    """bifrost api <METHOD> <endpoint> [json-body]"""
    if not args or args[0] in ("--help", "-h"):
        print("""
Usage: bifrost api <METHOD> <endpoint> [json-body]

Make an authenticated API request to Bifrost.

Arguments:
  METHOD                HTTP method (GET, POST, PUT, PATCH, DELETE)
  endpoint              API endpoint path (e.g., /api/workflows)
  json-body             Optional JSON body (inline or @filename)

Examples:
  bifrost api GET /api/workflows
  bifrost api GET /api/github/repo-status
  bifrost api POST /api/applications/my-app/validate
  bifrost api POST /api/files/push @payload.json
""".strip())
        return 0 if args and args[0] in ("--help", "-h") else 1

    if len(args) < 2:
        print("Usage: bifrost api <METHOD> <endpoint> [json-body]", file=sys.stderr)
        return 1

    method = args[0].upper()
    endpoint = args[1]
    body = None

    if len(args) > 2:
        import pathlib
        raw = args[2]
        # Support @filename for reading body from file
        if raw.startswith("@"):
            try:
                body = json.loads(pathlib.Path(raw[1:]).read_text())
            except Exception as e:
                print(f"Error reading file: {e}", file=sys.stderr)
                return 1
        else:
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"Invalid JSON: {e}", file=sys.stderr)
                return 1

    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        print(f"Unsupported method: {method}", file=sys.stderr)
        return 1

    # Authenticate BEFORE entering asyncio.run() so token refresh works
    try:
        client = BifrostClient.get_instance(require_auth=True)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return asyncio.run(_api_request(method, endpoint, body, client=client))


async def _api_request(method: str, endpoint: str, body: Any | None, client: "BifrostClient | None" = None) -> int:
    if client is None:
        try:
            client = BifrostClient.get_instance(require_auth=True)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    http_fn = getattr(client, method.lower())
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body

    try:
        response = await http_fn(endpoint, **kwargs)
        # Pretty-print response
        try:
            data = response.json()
            print(json.dumps(data, indent=2, default=str))
        except Exception:
            print(response.text)
        return 0 if response.status_code < 400 else 1
    except httpx.ConnectError:
        print("Error: could not connect to Bifrost API.", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# migrate-imports
# ---------------------------------------------------------------------------
# _PLATFORM_EXPORT_NAMES is imported at the top of this file from
# bifrost.platform_names (the canonical shared source).


def handle_migrate_imports(args: list[str]) -> int:
    """bifrost migrate-imports [PATH] [--dry-run] [--yes] [--skip-diff]

    Diff-first workflow: the unified diff is printed before any write, including
    when --yes is passed. The classifier uses regex (not an AST) so it cannot
    distinguish a platform name used as an expression from a local binding that
    shadows it -- always review the diff. Use --skip-diff with --yes to
    suppress the diff output in scripted runs where you've already reviewed.
    """
    if args and args[0] in ("--help", "-h"):
        print("""
Usage: bifrost migrate-imports [path] [options]

Rewrite "bifrost" imports into user-component / lucide-react / react-router-dom imports.

Classifier precedence (first match wins):
  1. Local components/<Name>.{tsx,ts}  -> default import from "./components/Name"
  2. React Router primitives            -> "react-router-dom"
  3. Lucide icons                       -> "lucide-react"
  4. Everything else                    -> stays in "bifrost"

Also infers missing user-component imports from JSX usage.

ALWAYS review the diff before applying. The classifier uses regex, not a
full scope analysis: if it added an import for a name you declared locally
(e.g. a destructured parameter that happens to match a platform export),
reject the change and fix by hand.

Arguments:
  path                  App dir or workspace containing apps/* (default: current directory)

Options:
  --dry-run             Print unified diff, do not write or prompt
  --yes, -y             Apply without the confirmation prompt (diff is still printed)
  --skip-diff           Suppress diff output. Only valid with --yes (scripted runs)
  --help, -h            Show this help message
""".strip())
        return 0

    from bifrost.migrate_imports import (
        discover_apps,
        load_lucide_icon_names,
        migrate_app,
        render_diff,
    )

    dry_run = False
    yes = False
    skip_diff = False
    path_arg: str | None = None

    for a in args:
        if a == "--dry-run":
            dry_run = True
        elif a in ("--yes", "-y"):
            yes = True
        elif a == "--skip-diff":
            skip_diff = True
        elif a.startswith("-"):
            print(f"Unknown option: {a}", file=sys.stderr)
            return 1
        elif path_arg is None:
            path_arg = a
        else:
            print(f"Unexpected argument: {a}", file=sys.stderr)
            return 1

    if skip_diff and not yes:
        print("Error: --skip-diff requires --yes (it only makes sense for scripted runs).", file=sys.stderr)
        return 1
    if skip_diff and dry_run:
        print("Error: --skip-diff is incompatible with --dry-run (dry-run exists to show the diff).", file=sys.stderr)
        return 1

    root = pathlib.Path(path_arg).resolve() if path_arg else pathlib.Path.cwd()
    if not root.exists():
        print(f"Error: path does not exist: {root}", file=sys.stderr)
        return 1

    lucide_names = load_lucide_icon_names()
    apps = discover_apps(root)

    all_results = []
    app_for_result: dict[pathlib.Path, pathlib.Path] = {}
    for app_dir in apps:
        for r in migrate_app(app_dir, _PLATFORM_EXPORT_NAMES, lucide_names):
            all_results.append(r)
            app_for_result[r.path] = app_dir

    changed = [r for r in all_results if r.changed]
    apps_touched = {app_for_result[r.path] for r in changed}

    if not changed:
        print("No changes needed.")
        return 0

    # --- Output ---
    if dry_run:
        for r in changed:
            print(render_diff(r), end="")
        print(f"\n{len(changed)} file(s) would change across {len(apps_touched)} app dir(s).")
        return 0

    # Diff first so the user always has scrollback to review, unless explicitly
    # suppressed in a scripted `--yes --skip-diff` run.
    if not skip_diff:
        for r in changed:
            print(render_diff(r), end="")

    # Summary after the diff
    for r in changed:
        print(str(r.path))
        for line in r.summary_lines():
            print(line)
    print(f"\n{len(changed)} file(s) will change across {len(apps_touched)} app dir(s).")

    if not yes:
        print("Review the diff above -- the classifier doesn't do full scope analysis.")
        try:
            reply = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply != "y":
            print("Aborted.")
            return 1

    for r in changed:
        r.path.write_text(r.updated, encoding="utf-8")

    print(f"Updated {len(changed)} file(s).")
    return 0


def print_run_help() -> None:
    """Print run command help."""
    print("""
Usage: bifrost run <file> -w <workflow> [options]

Run a workflow directly. Output is raw JSON (pipeable). Use --interactive for browser UI.

Arguments:
  file                  Python file containing @workflow decorated functions

Options:
  --workflow, -w NAME          Workflow to run (required in direct mode)
  --params, -p JSON            JSON parameters to pass to the workflow (default: {})
  --organization-id, --org ID  Run as a specific organization (superusers only)
  --verbose, -v                Show status messages (e.g., "Running...", "Result:")
  --interactive, -i            Open browser-based session instead of direct execution
  --no-browser, -n             Don't auto-open browser (only with --interactive)
  --help, -h                   Show this help message

Examples:
  bifrost run workflow.py -w greet                                         # Direct execution, raw JSON output
  bifrost run workflow.py -w greet -p '{"name": "World"}'                  # With parameters
  bifrost run workflow.py -w greet -v                                      # Verbose output
  bifrost run workflow.py -w greet | jq .                                  # Pipe to jq
  bifrost run workflow.py --interactive                                    # Browser-based session
""".strip())


if __name__ == "__main__":
    sys.exit(main())
