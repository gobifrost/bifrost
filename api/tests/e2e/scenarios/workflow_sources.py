"""Workflow sources registered by the execution-identity scenario module.

Every name is suffixed with the module's run tag so repeated runs never
collide. Org IDs and resource names are baked into the source at registration
time, so unattended runs (webhook, schedule, topic, endpoint) need no inputs.
"""

from __future__ import annotations

import json


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


async def _upsert(knob, label, scope):
    await tables.upsert(TABLE, id=_doc(knob, label), data={{"knob": knob, "target": label}}, scope=scope)


async def _query(scope):
    found = await tables.query(TABLE, scope=scope, document_id_prefix="marker-", limit=10)
    return sorted(doc.id.removeprefix("marker-") for doc in found.documents)


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
            "tables_upsert": await _attempt(lambda: _upsert("tables_upsert", label, scope)),
            "tables_query": await _attempt(lambda: _query(scope)),
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


def journey_sources(
    *,
    tag: str,
    targets: dict[str, str],
    table: str,
    config_key: str,
    secret_key: str,
    secret_value: str,
    integration: str,
    hr_role_id: str,
    stored_action_id: str,
) -> dict[str, tuple[str, str]]:
    """Functional journey workflows: key -> (function name, source).

    Shapes taken from real workspaces: an onboarding workflow gated on a
    role that hands off to a child, a provider dispatcher replaying a stored
    request as its submitter, a provider fleet job and a global job that
    switches to the provider org.
    """
    header = f"""from bifrost import config, context, integrations, roles, tables, workflow, workflows

TARGETS = {json.dumps(targets)}
TABLE = {table!r}
CONFIG_KEY = {config_key!r}
SECRET_KEY = {secret_key!r}
INTEGRATION = {integration!r}
"""
    names = {
        key: f"scn_{key}_{tag}"
        for key in (
            "reader",
            "onboard",
            "replay_child",
            "dispatcher",
            "fleet",
            "global_switch",
        )
    }
    sources = {
        "reader": f"""{header}

@workflow(name={names["reader"]!r}, description="Scenario settings reader")
async def {names["reader"]}():
    mapping = await integrations.get(INTEGRATION)
    return {{
        "user_id": str(context.user_id),
        "org_id": context.org_id,
        "config": await config.get(CONFIG_KEY),
        "secret_matches": await config.get(SECRET_KEY) == {secret_value!r},
        "entity_id": getattr(mapping, "entity_id", None) if mapping else None,
    }}
""",
        "onboard": f"""{header}
HR_ROLE_ID = {hr_role_id!r}


@workflow(name={names["onboard"]!r}, description="Scenario onboarding")
async def {names["onboard"]}(target: str, person: str):
    is_hr = str(context.user_id) in await roles.list_users(HR_ROLE_ID)
    is_staff = bool(context.organization and context.organization.is_provider)
    if not (is_hr or is_staff):
        return {{"outcome": "not_permitted"}}
    org_id = TARGETS[target]
    try:
        context.set_scope(org_id)
        await tables.insert(TABLE, {{"person": person}}, id=f"onboard-{{context.execution_id}}")
    except PermissionError:
        return {{"outcome": "refused"}}
    finally:
        context.set_scope(None)
    child = await workflows.execute({names["reader"]!r}, {{}}, org_id=org_id, run_as=str(context.user_id))
    return {{"outcome": "onboarded", "child": child}}
""",
        "replay_child": f"""{header}

@workflow(name={names["replay_child"]!r}, description="Scenario replayed request")
async def {names["replay_child"]}(org: str, request_id: str):
    context.set_scope(TARGETS[org])
    await tables.insert(TABLE, {{"replayed": request_id}}, id=f"replay-{{request_id}}")
    return {{"user_id": str(context.user_id)}}
""",
        "dispatcher": f"""{header}

@workflow(name={names["dispatcher"]!r}, description="Scenario request dispatcher")
async def {names["dispatcher"]}():
    request = await tables.get(TABLE, {stored_action_id!r})
    data = request.data
    child = await workflows.execute(
        {names["replay_child"]!r},
        {{"org": data["org"], "request_id": {stored_action_id!r}}},
        run_as=data["submitter"],
    )
    return {{"child": child}}
""",
        "fleet": f"""{header}

@workflow(name={names["fleet"]!r}, description="Scenario fleet job")
async def {names["fleet"]}():
    for label in ("contoso", "fabrikam"):
        context.set_scope(TARGETS[label])
        try:
            await tables.insert(TABLE, {{"fleet": label}}, id=f"fleet-{{context.execution_id}}-{{label}}")
        finally:
            context.set_scope(None)
    return {{"done": True}}
""",
        "global_switch": f"""{header}

@workflow(name={names["global_switch"]!r}, description="Scenario global job")
async def {names["global_switch"]}():
    context.set_scope(TARGETS["provider"])
    try:
        await tables.insert(TABLE, {{"switched": True}}, id=f"switch-{{context.execution_id}}")
    finally:
        context.set_scope(None)
    return {{"done": True}}
""",
    }
    return {key: (names[key], source) for key, source in sources.items()}
