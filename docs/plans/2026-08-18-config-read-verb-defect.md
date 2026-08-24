# Defect: `POST /api/sdk/config/get` is a pure read behind a POST

**Found:** 2026-08-18, during Phase 2 Configs slice triage
**Status:** logged, deliberately NOT fixed in Phase 2
**Decided by:** Jack, 2026-08-18

## What

`api/src/routers/cli.py:412` defines `POST /config/get` (mounted under
`/api/sdk`). It is a pure read: it takes `{key, scope}`, resolves the config
cascade, decrypts secrets, coerces the declared type, and returns a value. It
performs no writes and has no side effects.

The SDK's `bifrost.config.get(key)` (`api/bifrost/config.py`) is its only
documented caller path, but the handler lives in `cli.py` and its docstring
says "via CLI API" — so the naming is muddled on two axes: verb and ownership.

## Why it is wrong

- Uncacheable: no HTTP caching layer will treat a POST as a read.
- Invisible to verb-based auditing, proxy rules, and read/write traffic splits.
- Cannot be exercised by any tooling that assumes reads are GETs.

## The one half-defense, and why it does not hold

`scope` is tri-state: unset (caller's default org), an explicit org UUID, and
an explicit `None` meaning global. A query string cannot cleanly distinguish
"unset" from "explicitly null" — `?scope=` is ambiguous — whereas a JSON body
can. That is a real problem, but it has conventional solutions (a separate
`?global=true` flag, or a documented sentinel value), and it does not justify
modeling a read as a POST.

## Why it was not fixed in Phase 2

This route is on the **workflow runtime read path**. Changing its verb, or
adding a GET twin and migrating callers, reaches well beyond MCP/CLI parity
work and risks live workflow execution. Phase 2 instead adds
`GET /api/config/{config_id}` for entity-inspection reads (CLI `configs get`
and MCP `get_config`), leaving the by-key runtime resolver untouched.

Note these serve genuinely different needs and both may survive:
- by-ID entity inspection (new `GET /api/config/{config_id}`)
- by-key value resolution with cascade + secret decryption (the SDK path)

The defect is the **verb and the module placement**, not the existence of two
readers.

## Suggested fix, when scheduled

Add `GET /api/sdk/config/{key}` with scope expressed via explicit query
params, migrate `bifrost.config.get` and any CLI caller, then retire
`POST /config/get`. Move the handler out of `cli.py` into a config-owned
module. Requires the contract tripwire's attention
(`./test.sh tests/unit/test_contract_version.py`).
