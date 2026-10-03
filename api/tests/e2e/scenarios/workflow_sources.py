"""Workflow sources registered by the execution-identity scenario module.

Every name is suffixed with the module's run tag so repeated runs never
collide. Org IDs and resource names are baked into the source at registration
time, so unattended runs (webhook, schedule, topic, endpoint) need no inputs.
"""

from __future__ import annotations

import json

KNOBS = (
    "set_scope",
    "tables_scope",
    "config_scope",
    "integration_scope",
    "raw_api",
    "context_override",
)
TARGET_LABELS = ("contoso", "fabrikam", "provider", "global")


def probe_source(
    *,
    function_name: str,
    targets: dict[str, str],
    table: str,
    config_key: str,
    integration: str,
    child_workflow: str,
    is_tool: bool = False,
) -> str:
    """A workflow that tries every scope knob against every target.

    It never raises for a refused attempt: each attempt is recorded as
    ``{"ok": bool, "err": str | None}`` and every attempted write uses a
    document id derived from the execution id, so the test can check which
    writes really landed and in which partition.
    """
    return f'''"""Execution-identity scenario probe."""
from bifrost import api, config, context, integrations, tables, workflow, workflows
from bifrost._context import _execution_context

TARGETS = {json.dumps(targets)}
TABLE = {table!r}
CONFIG_KEY = {config_key!r}
INTEGRATION = {integration!r}
CHILD_WORKFLOW = {child_workflow!r}


async def _attempt(call):
    try:
        value = await call()
    except Exception as exc:  # a refusal is an observation, not a failure
        return {{"ok": False, "err": type(exc).__name__}}
    return {{"ok": True, "err": None, "value": value}}


def _doc(knob, label):
    return f"{{context.execution_id}}-{{knob}}-{{label}}"


async def _insert(knob, label, scope=None):
    await tables.insert(TABLE, {{"knob": knob, "target": label}}, id=_doc(knob, label), scope=scope)


async def _with_set_scope(knob, label, scope):
    context.set_scope(scope)
    try:
        await _insert(knob, label)
    finally:
        context.set_scope(None)


async def _with_context_override(knob, label, scope):
    # Sets the scope on the live context directly, skipping set_scope's check,
    # so the observation shows what the server itself enforces.
    live = _execution_context.get()
    live._scope_override = scope
    try:
        await _insert(knob, label)
    finally:
        live._scope_override = None


async def _raw(knob, label, scope):
    response = await api.post(
        f"/api/tables/{{TABLE}}/documents?scope={{scope}}",
        json={{"data": {{"knob": knob, "target": label}}, "id": _doc(knob, label)}},
    )
    if response.status_code >= 300:
        raise PermissionError(f"HTTP{{response.status_code}}")


async def _config(scope):
    return await config.get(CONFIG_KEY, scope=scope)


async def _integration(scope):
    found = await integrations.get(INTEGRATION, scope=scope)
    return getattr(found, "entity_id", None) if found else None


@workflow(name={function_name!r}, description="Scenario probe", is_tool={is_tool!r})
async def {function_name}(children: list | None = None):
    attempts = {{}}
    for label, scope in TARGETS.items():
        attempts[label] = {{
            "set_scope": await _attempt(lambda: _with_set_scope("set_scope", label, scope)),
            "tables_scope": await _attempt(lambda: _insert("tables_scope", label, scope)),
            "config_scope": await _attempt(lambda: _config(scope)),
            "integration_scope": await _attempt(lambda: _integration(scope)),
            "raw_api": await _attempt(lambda: _raw("raw_api", label, scope)),
            "context_override": await _attempt(
                lambda: _with_context_override("context_override", label, scope)
            ),
        }}
    spawned = []
    for spec in children or []:
        org_label = spec.get("org")
        result = await _attempt(
            lambda: workflows.execute(
                CHILD_WORKFLOW,
                {{"children": spec.get("children") or []}},
                org_id=TARGETS[org_label] if org_label else None,
                run_as=spec.get("run_as"),
            )
        )
        spawned.append({{"spec": spec, **result}})
    return {{
        "identity": {{
            "user_id": str(context.user_id) if context.user_id else None,
            "email": context.email,
            "org_id": context.org_id,
            "is_platform_admin": bool(context.is_platform_admin),
            "execution_id": context.execution_id,
        }},
        "attempts": attempts,
        "children": spawned,
    }}
'''
