---
name: bifrost-debug
description: Boot an isolated, hot-reload Bifrost dev stack for the current worktree via ./debug.sh. Use when the user wants to click around the UI, view a feature in the browser, screenshot something, or otherwise needs the dev stack running. Per-worktree isolation lets multiple worktrees run dev stacks in parallel. Trigger phrases - "open the app", "let me click around", "spin up debug", "test in the browser", "show me the UI", "/bifrost-debug".
---

# Bifrost Debug

Bring up a per-worktree, hot-reload Bifrost dev stack via `./debug.sh`. Hand the user the URL. Don't restart containers for code changes (hot reload handles it).

## When to activate

- User wants to click around the UI: "open the app", "let me try this in the browser", "show me the form", "I want to test this".
- User wants screenshots of a feature.
- User explicitly invokes `/bifrost-debug`.
- Before manually verifying a UI change at the end of a feature (per `bifrost-testing` Definition of Done).

Do **not** activate for backend-only work that can be verified via tests, type generation, or `curl` against a running stack.

## Two boot modes (auto-detected)

`./debug.sh` picks the mode based on whether `NETBIRD_SETUP_KEY` is available in the env (process env, or `~/.config/bifrost/debug.env`):

- **Mode A — netbird:** key present. Stack reachable only via the Netbird mesh at `http://<bifrost-debug-WORKTREE>`. No host port bindings. Suited for showing the user's stack to anyone on their Netbird network (or themselves on a different device).
- **Mode B — port:** no key. Client exposed on a deterministic free host port (30000–39999, hashed from worktree path). Reachable at `http://localhost:<port>`. Default for most local development.

The user picks the mode by setting (or not setting) `NETBIRD_SETUP_KEY` in `~/.config/bifrost/debug.env`. Don't try to switch modes for them.

**Optional in Mode A:** `NETBIRD_EXTRA_DNS_LABELS` in `~/.config/bifrost/debug.env` adds DNS aliases for the peer (comma-separated, e.g. `bifrost,debug-current` → `bifrost.netbird.cloud`, `debug-current.netbird.cloud`). Wildcards work (`*.myserver`). Useful for stable per-user names that don't change with the worktree. Don't set this for the user — they manage it themselves.

## The basic flow

1. **Check status first.** Don't re-boot if already up.

   ```bash
   ./debug.sh status
   ```

   If the output shows `Status:   UP`, parse the `Open:` line and hand the URL straight to the user. Stop.

2. **If down, boot it (backgrounded).** `./debug.sh up` runs `docker compose up -d --build` and then waits up to 180s for the API health check. First boot can take 1-3 minutes for the image build, so use a background shell:

   ```bash
   # Bash(run_in_background=true)
   ./debug.sh up
   ```

   Tail the background output with BashOutput until you see one of:
   - `Open:     http://...` — success. Hand the URL to the user.
   - `ERROR: api did not become ready` — failure. Run `./debug.sh logs api` and report what's wrong.

3. **Tell the user the credentials.** Login is `dev@gobifrost.com` / `password`. MFA is off. No setup wizard. Mention this once with the URL.

4. **Point at logs if they ask.** `./debug.sh logs <service>` — services include `api`, `client`, `worker`, `scheduler`, `postgres`, `rabbitmq`, `redis`, `minio`.

## Connect the CLI

Bifrost can keep credentials for several instances. A folder's nearest `.env`
selects the connection for commands run there; otherwise the CLI uses the
user's saved default. Debug should use its own folder binding and must not
change that saved default.

After the stack is up, create one scratch directory for this worktree. Never
run debug CLI commands from bare `/tmp`.

```bash
mkdir -p /tmp/bifrost-cli-<worktree-name>
cd /tmp/bifrost-cli-<worktree-name>
touch .env
python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet "<URL_FROM_DEBUG_STATUS>/api/cli/download"
./.venv/bin/bifrost login --url <URL_FROM_DEBUG_STATUS> \
  --email dev@gobifrost.com --password password
```

The empty `.env` created before the first CLI call prevents an unrelated
parent `.env` from being loaded. Login then writes this debug stack's URL and
tokens into that scratch `.env`. Run all later debug-instance CLI commands
from the same scratch directory with `./.venv/bin/bifrost ...`.

Run `./debug.sh` commands from the worktree, never from the scratch directory.
If the connection is ever unclear, `bifrost auth default` is a read-only check:
the `Current connection` line is what commands in that folder will use.

When explicitly tearing down the stack, clear its folder binding before or
after `./debug.sh down`:

```bash
cd /tmp/bifrost-cli-<worktree-name>
./.venv/bin/bifrost logout --url <URL_FROM_DEBUG_STATUS> --yes
```

## Lifecycle: who tears down what

- **The stack outlives this Claude session.** Closing or clearing the session does NOT tear it down. This is intentional — the user might come back, or have another Claude session attach to the same stack.
- To tear down explicitly: `./debug.sh down`. This removes containers and volumes (postgres data is lost — fine for a debug stack; not fine for the prod stack).
- If the user says "stop the debug stack" or "wipe the debug data," run `./debug.sh down`.
- If a second Claude session or a new conversation comes up while the stack is already running, that's fine — `./debug.sh status` will reveal it. Don't call `./debug.sh up` redundantly; just hand over the URL from `status`.

## Hot reload — don't restart for code changes

Same rule as the dev stack always had:

- API, scheduler, worker: `watchmedo` / uvicorn `--reload` watches `/app/src` and `/app/shared`. Saves cause an automatic restart.
- Client: Vite HMR. Browser updates instantly.

**Never** run `docker compose restart api` (or any service) just because you edited Python. Restart only when:
- New Python dependency in `pyproject.toml` (then rebuild: `docker compose -f docker-compose.debug.yml up -d --build api`).
- New alembic migration (then `docker compose -f docker-compose.debug.yml restart init` to apply, then `restart api`).

Set the user's expectation: "your edit will be live in a few seconds, no restart."

## Multi-worktree

Two worktrees can run debug stacks in parallel. They get different Compose project names (`bifrost-debug-<hash>`), separate volumes, separate ports. To list all running debug stacks:

```bash
docker ps --filter "name=bifrost-debug-" --format "{{.Names}}"
```

Group by project name to find each worktree's URL: `cd <worktree> && ./debug.sh status`.

## Common failures

**API won't come up:**
- `./debug.sh logs init` — migrations failing? Compare `git log origin/main -- api/alembic/versions/` against the worktree.
- `./debug.sh logs api` — usually a config / import error. The traceback is usually in the first 30 lines of output after the start banner.

**Login still shows the setup wizard:**
- The seed-user provisioning runs on every API boot. If the wizard appears, the seed env vars probably aren't loaded — check `docker compose -f docker-compose.debug.yml exec api env | grep BIFROST_DEFAULT_USER`. If empty, `.env.debug` isn't being sourced; investigate `load_env_files` in `debug.sh`.
- "User already exists" on the wizard: a previous (broken) seed user lingers without an org. Run `./debug.sh down` (wipes the DB volume) and `./debug.sh up` again.

**Mode A can't be reached at the hostname:**
- DNS propagation in Netbird takes a few seconds after first peer enrollment. Wait 30s and retry.
- Confirm the peer is registered: `docker compose -f docker-compose.debug.yml logs netbird | tail -20`. Look for `Peer registration completed`.
- The Netbird Admin reverse-proxy mapping needs a one-time setup: peer = the worktree hostname, port = 80. The skill doesn't manage this; the user does it once per worktree (or sets up a wildcard).

**Mode B port not reachable:**
- `./debug.sh status` re-reads the published port from `docker port`. If `Open:` shows nothing, the client container didn't start — `./debug.sh logs client`.

## What this skill does NOT do

- Doesn't manage Netbird account / Admin config (one-time manual setup).
- Doesn't set up `~/.config/bifrost/debug.env` — point the user at it, don't write keys there for them.
- Doesn't run tests. That's the `bifrost-testing` skill's job.
- Doesn't reset DB state. `./debug.sh down` wipes the volume; there's no fast in-place reset (unlike `./test.sh stack reset`).
