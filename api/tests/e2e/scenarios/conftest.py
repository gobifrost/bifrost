"""World and runs for the execution-identity scenario module.

One module-scoped world (orgs, personas, tables, integration, configs, probe
workflows) and one module-scoped batch of runs: every start in
``rule.STARTS`` is launched together, then polled together, so the module's
wall time is roughly the slowest run rather than the sum of all runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest

from tests.e2e.conftest import poll_until, write_and_register
from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser
from tests.e2e.scenarios import rule
from tests.e2e.scenarios.workflow_sources import probe_source

PROVIDER_ORG_ID = "00000000-0000-0000-0000-000000000002"
ENGINE_USER_ID = "00000000-0000-0000-0000-000000000001"
TERMINAL = {"Success", "Failed", "CompletedWithErrors", "Cancelled", "Timeout"}
ALL_ACTIONS = ["read", "create", "update", "delete"]
# Matches only the frozen clock the in-process tick uses, never the live
# scheduler's real clock while the module runs.
SCHEDULE_CRON = "17 3 1 1 *"
SCHEDULE_FIRES_AT = datetime(2026, 1, 1, 3, 17, 30, tzinfo=timezone.utc)


def _ok(resp: httpx.Response, *codes: int) -> dict:
    assert resp.status_code in (codes or (200, 201)), (
        f"{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}"
    )
    return resp.json() if resp.content else {}


def _create_person(client, admin, *, tag, key, org_id, is_external=False) -> E2EUser:
    user = E2EUser(
        email=f"scn-{key}-{tag}@contoso.example",
        password=f"Scn-{tag}-Pass1!",
        name=f"Scenario {key}",
        organization_id=UUID(org_id),
    )
    body: dict[str, Any] = {
        "email": user.email,
        "name": user.name,
        "organization_id": org_id,
        "is_superuser": False,
    }
    if is_external:
        body["is_external"] = True
    user.user_id = UUID(_ok(client.post("/api/users", headers=admin.headers, json=body))["id"])
    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = UUID(org_id)
    return user


@pytest.fixture(scope="module")
def scenario_world(
    e2e_client, platform_admin, org1, org2, org1_user, org2_user, provider_org_user
):
    client, admin = e2e_client, platform_admin
    tag = uuid4().hex[:6]
    started = time.monotonic()
    created_paths: list[str] = []
    created_tables: list[str] = []
    created_roles: list[str] = []
    created_sources: list[str] = []

    org_ids = {"contoso": org1["id"], "fabrikam": org2["id"], "provider": PROVIDER_ORG_ID}
    targets = {**org_ids, "global": "global"}

    # People. Session personas plus three created here.
    hr_role = _ok(client.post(
        "/api/roles", headers=admin.headers,
        json={"name": f"Scenario HR {tag}", "description": "permission-free role a workflow checks"},
    ))
    created_roles.append(hr_role["id"])
    base_role = _ok(client.post(
        "/api/roles", headers=admin.headers,
        json={"name": f"Scenario base {tag}", "description": "custom base role"},
    ))
    created_roles.append(base_role["id"])
    hr = _create_person(client, admin, tag=tag, key="hr", org_id=org1["id"])
    _ok(client.post(f"/api/roles/{hr_role['id']}/users", headers=admin.headers,
                    json={"user_ids": [str(hr.user_id)]}), 200, 204)
    external = _create_person(client, admin, tag=tag, key="external", org_id=org1["id"], is_external=True)
    custom_base = _create_person(client, admin, tag=tag, key="custombase", org_id=org1["id"])
    _ok(client.put(
        f"/api/users/{custom_base.user_id}/role-assignments", headers=admin.headers,
        json={"base_role_id": base_role["id"], "additional": []},
    ), 200, 204)
    people = {
        "admin": admin,
        "staff": provider_org_user,
        "customer": org1_user,
        "hr": hr,
        "external": external,
        "custom_base": custom_base,
        "fabrikam_customer": org2_user,
    }

    # One observation table per partition. Everyone may read, so direct reads
    # measure reach rather than table policy.
    table = f"scn_obs_{tag}"
    table_ids: dict[str, str] = {}
    policies = {"policies": [
        {"name": "admin_bypass", "actions": ALL_ACTIONS, "when": {"user": "is_platform_admin"}},
        {"name": "everyone_reads", "actions": ["read"], "when": {"eq": [1, 1]}},
    ]}
    for label in rule.TARGETS:
        created = _ok(client.post("/api/tables", headers=admin.headers, json={
            "name": table,
            "description": "execution-identity scenario observations",
            "organization_id": org_ids.get(label),
            "policies": policies,
        }))
        table_ids[label] = created["id"]
        created_tables.append(created["id"])
        # A marker per partition shows which partition a direct read answered from.
        _ok(client.post(f"/api/tables/{created['id']}/documents", headers=admin.headers,
                        json={"id": f"marker-{label}", "data": {"marker": label}}))

    # Fake integration with one mapping per org; configs with an org override.
    integration = f"ScnPSA_{tag}"
    integ = _ok(client.post("/api/integrations", headers=admin.headers, json={"name": integration}))
    for label, org_id in org_ids.items():
        _ok(client.post(f"/api/integrations/{integ['id']}/mappings", headers=admin.headers, json={
            "organization_id": org_id, "entity_id": f"{label}-tenant", "entity_name": f"{label}-tenant",
        }))
    config_key = f"scn_cfg_{tag}"
    secret_key = f"scn_secret_{tag}"
    config_ids = [
        _ok(client.post("/api/config", headers=admin.headers, json={
            "key": config_key, "value": "global-default", "type": "string", "organization_id": None,
        }))["id"],
        _ok(client.post("/api/config", headers=admin.headers, json={
            "key": config_key, "value": "contoso-value", "type": "string", "organization_id": org1["id"],
        }))["id"],
        _ok(client.post("/api/config", headers=admin.headers, json={
            "key": secret_key, "value": f"secret-{tag}", "type": "secret", "organization_id": None,
        }))["id"],
    ]

    # Probe workflows, one per home. Children always run the global probe.
    workflows: dict[str, dict] = {}
    child_name = f"scn_probe_global_{tag}"
    homes = {
        "provider": PROVIDER_ORG_ID,
        "contoso": org1["id"],
        "global": None,
        "global_new": None,
        "global_tool": None,
    }
    for home, org_id in homes.items():
        name = f"scn_probe_{home}_{tag}"
        path = f"scenarios_{tag}/probe_{home}.py"
        source = probe_source(
            function_name=name, targets=targets, table=table,
            config_key=config_key, integration=integration, child_workflow=child_name,
            is_tool=home == "global_tool",
        )
        registered = write_and_register(client, admin.headers, path, source, name, organization_id=org_id)
        created_paths.append(path)
        _ok(client.patch(f"/api/workflows/{registered['id']}", headers=admin.headers, json={
            "organization_id": org_id,
            "access_level": "everyone" if org_id is None else "authenticated",
        }))
        workflows[home] = registered

    # Entry points other than REST. Event sources: one per home; a source fires
    # every subscription on it, so the Contoso source runs two probes per POST.
    sources: dict[str, dict] = {}
    source_specs = {
        "provider": (PROVIDER_ORG_ID, ["provider"], None),
        "global": (None, ["global"], f"scn-signing-{tag}"),
        "contoso": (org1["id"], ["contoso", "global_new"], None),
    }
    for label, (org_id, probes, secret) in source_specs.items():
        webhook: dict[str, Any] = {"adapter_name": "generic", "config": {}}
        if secret:
            webhook["config"]["secret"] = secret
        src = _ok(client.post("/api/events/sources", headers=admin.headers, json={
            "name": f"Scenario webhook {label} {tag}", "source_type": "webhook",
            "organization_id": org_id, "webhook": webhook,
        }))
        created_sources.append(src["id"])
        for probe in probes:
            _ok(client.post(f"/api/events/sources/{src['id']}/subscriptions", headers=admin.headers,
                            json={"workflow_id": workflows[probe]["id"], "event_type": None}))
        sources[f"webhook@{label}"] = {"id": src["id"], "secret": secret}
    schedule = _ok(client.post("/api/events/sources", headers=admin.headers, json={
        "name": f"Scenario schedule {tag}", "source_type": "schedule", "organization_id": None,
        "schedule": {"cron_expression": SCHEDULE_CRON, "timezone": "UTC", "enabled": True},
    }))
    created_sources.append(schedule["id"])
    _ok(client.post(f"/api/events/sources/{schedule['id']}/subscriptions", headers=admin.headers,
                    json={"workflow_id": workflows["global"]["id"], "event_type": None}))
    sources["schedule"] = {"id": schedule["id"]}
    topic = f"scn.identity_{tag}"
    topic_source = _ok(client.post("/api/events/sources", headers=admin.headers, json={
        "name": f"Scenario topic {tag}", "source_type": "topic", "event_type": topic,
        "organization_id": None,
    }))
    created_sources.append(topic_source["id"])
    _ok(client.post(f"/api/events/sources/{topic_source['id']}/subscriptions", headers=admin.headers,
                    json={"workflow_id": workflows["global_new"]["id"], "event_type": topic}))
    sources["topic"] = {"id": topic_source["id"], "topic": topic}

    endpoint_keys: dict[str, str] = {}
    key_ids: list[str] = []
    for probe in ("global", "contoso"):
        _ok(client.patch(f"/api/workflows/{workflows[probe]['id']}", headers=admin.headers, json={
            "endpoint_enabled": True, "allowed_methods": ["POST"], "execution_mode": "async",
        }))
        key = _ok(client.post("/api/workflow-keys", headers=admin.headers, json={
            "workflow_id": workflows[probe]["id"], "description": f"scenario {tag}",
        }))
        endpoint_keys[probe] = key["raw_key"]
        key_ids.append(key["id"])

    form = _ok(client.post("/api/forms", headers=admin.headers, json={
        "name": f"Scenario form {tag}", "workflow_id": workflows["global"]["id"],
        "form_schema": {"fields": [{"name": "note", "type": "text", "label": "Note", "required": False}]},
        "access_level": "authenticated", "organization_id": None,
    }))
    agent = _ok(client.post("/api/agents", headers=admin.headers, json={
        "name": f"Scenario agent {tag}", "description": "calls the scenario probe tool",
        "system_prompt": "Call the probe tool.", "channels": ["chat"],
        "tool_ids": [workflows["global_tool"]["id"]],
        "access_level": "everyone", "organization_id": None,
    }))

    world = {
        "tag": tag,
        "client": client,
        "admin": admin,
        "people": people,
        "org_ids": org_ids,
        "targets": targets,
        "table": table,
        "table_ids": table_ids,
        "integration": integration,
        "integration_id": integ["id"],
        "config_key": config_key,
        "secret_key": secret_key,
        "workflows": workflows,
        "sources": sources,
        "endpoint_keys": endpoint_keys,
        "form_id": form["id"],
        "agent_id": agent["id"],
        "started": started,
    }
    yield world

    for source_id in created_sources:
        client.delete(f"/api/events/sources/{source_id}", headers=admin.headers)
    for key_id in key_ids:
        client.delete(f"/api/workflow-keys/{key_id}", headers=admin.headers)
    client.delete(f"/api/forms/{form['id']}", headers=admin.headers)
    client.delete(f"/api/agents/{agent['id']}", headers=admin.headers)
    for path in created_paths:
        client.delete(f"/api/files/editor?path={path}", headers=admin.headers)
    for table_id in created_tables:
        client.delete(f"/api/tables/{table_id}", headers=admin.headers)
    for config_id in config_ids:
        client.delete(f"/api/config/{config_id}", headers=admin.headers)
    client.delete(f"/api/integrations/{integ['id']}", headers=admin.headers)
    for person in (hr, external, custom_base):
        client.delete(f"/api/users/{person.user_id}", headers=admin.headers)
    for role_id in created_roles:
        client.delete(f"/api/roles/{role_id}", headers=admin.headers)


def _child_specs(world, specs: tuple[rule.Child, ...]) -> list[dict]:
    out = []
    for child in specs:
        run_as = world["people"][child.run_as].user_id if child.run_as else None
        out.append({
            "org": child.org,
            "run_as": str(run_as) if run_as else None,
            "children": _child_specs(world, child.children),
        })
    return out


def _start_rest(world, start: rule.Start) -> dict:
    person = world["people"][start.person]
    body: dict[str, Any] = {
        "workflow_id": world["workflows"][start.probe]["id"],
        "input_data": {"children": _child_specs(world, start.children)},
        "sync": False,
    }
    if start.request_org:
        body["org_id"] = world["targets"][start.request_org]
    resp = world["client"].post("/api/workflows/execute", headers=person.headers, json=body)
    if resp.status_code != 200:
        return {"accepted": False, "http": resp.status_code}
    return {"accepted": True, "http": 200, "execution_id": resp.json()["execution_id"]}


def _fetch_execution(world, execution_id: str) -> dict | None:
    resp = world["client"].get(f"/api/executions/{execution_id}", headers=world["admin"].headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data if data.get("status") in TERMINAL else None


def _await_executions(world, execution_ids: list[str], max_wait: float = 60.0) -> dict[str, dict]:
    done: dict[str, dict] = {}

    def _all_done():
        for execution_id in execution_ids:
            if execution_id not in done:
                found = _fetch_execution(world, execution_id)
                if found is not None:
                    done[execution_id] = found
        return len(done) == len(execution_ids)

    assert poll_until(_all_done, max_wait=max_wait, interval=0.5), (
        f"executions not terminal: {sorted(set(execution_ids) - set(done))}"
    )
    return done


def _collect_tree(world, execution_id: str, specs: tuple[rule.Child, ...], key: str, out: dict) -> None:
    """Record an execution and, recursively, the children it spawned."""
    execution = _await_executions(world, [execution_id])[execution_id]
    out[key] = {"execution": execution}
    result = execution.get("result") if isinstance(execution.get("result"), dict) else {}
    spawned = result.get("children") or []
    for child, spawn in zip(specs, spawned, strict=False):
        child_key = f"{key}>{child.key}"
        if spawn.get("ok"):
            _collect_tree(world, spawn["value"], child.children, child_key, out)
        else:
            out[child_key] = {"refused": spawn.get("err")}


def _rows_by_partition(world) -> dict[str, dict[str, str | None]]:
    """Every document in each observation table: doc id -> created_by."""
    rows: dict[str, dict[str, str | None]] = {}
    for label, table_id in world["table_ids"].items():
        data = _ok(world["client"].post(
            f"/api/tables/{table_id}/documents/query",
            headers=world["admin"].headers, json={"limit": 1000},
        ))
        rows[label] = {doc["id"]: doc.get("created_by") for doc in data["documents"]}
    return rows


def _direct_reads(world, start: rule.Start) -> dict[str, dict]:
    person = world["people"][start.person]
    reads = {}
    for label, scope in world["targets"].items():
        resp = world["client"].post(
            f"/api/tables/{world['table']}/documents/query?scope={scope}",
            headers=person.headers, json={"limit": 1000},
        )
        if resp.status_code != 200:
            reads[label] = {"ok": False, "err": f"HTTP{resp.status_code}"}
            continue
        markers = sorted(
            doc["id"].removeprefix("marker-")
            for doc in resp.json()["documents"] if doc["id"].startswith("marker-")
        )
        reads[label] = {"ok": True, "err": None, "partitions": markers}
    return reads


def _start_other(world, start: rule.Start, posted: dict[str, str]) -> dict:
    """Launch a non-REST start. Returns an execution id or what to resolve it from."""
    client, tag = world["client"], world["tag"]
    workflow_id = world["workflows"][start.probe]["id"]
    person = world["people"][start.person] if start.person else None
    if start.entry == "delayed":
        assert person
        resp = client.post("/api/workflows/execute", headers=person.headers, json={
            "workflow_id": workflow_id, "input_data": {}, "delay_seconds": 1,
        })
        return {"execution_id": _ok(resp)["execution_id"]}
    if start.entry == "form":
        assert person
        resp = client.post(f"/api/forms/{world['form_id']}/submissions", headers=person.headers,
                           json={"form_data": {}})
        return {"execution_id": _ok(resp)["execution_id"]}
    if start.entry == "endpoint":
        resp = client.post(f"/api/endpoints/{workflow_id}", json={},
                           headers={"X-Bifrost-Key": world["endpoint_keys"][start.probe]})
        return {"execution_id": _ok(resp)["execution_id"]}
    if start.entry == "webhook":
        label = f"webhook@{start.source or start.probe}"
        source = world["sources"][label]
        if label not in posted:
            posted[label] = f"{tag}-{label}"
            body = json.dumps({"marker": posted[label]}).encode()
            headers = {"Content-Type": "application/json"}
            if source["secret"]:
                digest = hmac.new(source["secret"].encode(), body, hashlib.sha256).hexdigest()
                headers["X-Signature-256"] = f"sha256={digest}"
            resp = client.post(f"/api/hooks/{source['id']}", content=body, headers=headers)
            assert resp.status_code == 202, f"webhook {label} -> {resp.status_code}: {resp.text}"
        return {"source_id": source["id"], "marker": posted[label]}
    if start.entry == "topic":
        resp = client.post("/api/events/emit", headers=world["admin"].headers, json={
            "topic": world["sources"]["topic"]["topic"], "data": {"marker": tag}, "scope": None,
        })
        return {"event_id": _ok(resp)["event_id"]}
    if start.entry == "schedule":
        return {"source_id": world["sources"]["schedule"]["id"], "event_type": "schedule.fired"}
    if start.entry == "chat":
        return {"chat": True}
    raise AssertionError(f"unknown entry {start.entry}")


def _reset_loop_singletons() -> None:
    """App-side DB, Redis and RabbitMQ clients pin to the loop that made them."""
    import src.core.redis_client as redis_module
    from src.core.database import reset_db_state
    from src.jobs.rabbitmq import rabbitmq

    reset_db_state()
    redis_module._redis_client = None
    rabbitmq.reset_pools()


class _FrozenClock(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return SCHEDULE_FIRES_AT if tz else SCHEDULE_FIRES_AT.replace(tzinfo=None)


async def _in_process(world, session_factory, chat_person) -> str | None:
    """Fire the schedule, promote delayed runs, and run the chat tool call."""
    from src.core.database import close_db
    from src.jobs.schedulers.cron_scheduler import process_schedule_sources
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    _reset_loop_singletons()
    try:
        with patch("src.jobs.schedulers.cron_scheduler.datetime", _FrozenClock):
            await process_schedule_sources()
        await asyncio.sleep(1.2)  # the delayed run is due one second after it was queued
        _, failures = await promote_due_executions()
        assert failures == 0, f"delayed promotion failures: {failures}"
        if chat_person is None:
            return None
        async with session_factory() as session:
            return await _chat_tool_call(world, session, chat_person)
    finally:
        await close_db()
        _reset_loop_singletons()


async def _chat_tool_call(world, session, person) -> str:
    """One chat turn whose in-process test model calls the probe tool once."""
    from unittest.mock import AsyncMock, MagicMock

    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
    from pydantic_ai.models.test import TestModel

    from src.core.principal import UserPrincipal
    from src.models.contracts.agents import ChatRequest
    from src.models.contracts.artifacts import ModelCapabilities
    from src.routers.chat import send_message
    from src.services.llm.base import LLMConfig
    from src.services.llm.pydantic_client import PydanticAIClient

    tool_name = f"wf_{world['workflows']['global_tool']['name']}"

    class ProbeToolModel(TestModel):
        def _request(self, messages, model_settings, model_request_parameters):
            del model_settings, model_request_parameters
            done = any(
                isinstance(m, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in m.parts)
                for m in messages
            )
            part = TextPart("done") if done else ToolCallPart(tool_name, {}, tool_call_id="probe")
            return ModelResponse(parts=[part], model_name=self.model_name)

    client = world["client"]
    conversation = _ok(client.post("/api/chat/conversations", headers=person.headers, json={
        "agent_id": world["agent_id"], "channel": "chat", "title": "scenario",
    }), 201)
    config = LLMConfig(provider="openai", model="test-chat", api_key="test-key")
    profile = MagicMock(id=uuid4(), name="scenario")
    with (
        patch("src.services.agent_executor.get_llm_client", new_callable=AsyncMock,
              return_value=PydanticAIClient(config)),
        patch("src.services.agent_executor.AIModelService.resolve_chat_profile",
              new=AsyncMock(return_value=(profile, config,
                                          ModelCapabilities(tool_calling=True, source="manual")))),
        patch("src.services.agent_runtime.model_factory.create_agent_model",
              return_value=ProbeToolModel(model_name="scenario-probe")),
    ):
        await send_message(
            conversation_id=UUID(conversation["id"]),
            request=ChatRequest(message="Run the probe."),
            db=session,
            user=UserPrincipal(
                user_id=person.user_id, email=person.email, name=person.name,
                organization_id=person.organization_id, is_superuser=False,
            ),
        )
    messages = _ok(client.get(f"/api/chat/conversations/{conversation['id']}/messages",
                              headers=person.headers))
    calls = [m for m in messages if m["role"] == "tool_call"]
    assert len(calls) == 1 and calls[0]["execution_id"], calls
    return calls[0]["execution_id"]


def _resolve_event_execution(world, launch: dict, workflow_id: str, max_wait: float = 30.0) -> str:
    """The execution an event delivered to one workflow."""
    client, headers = world["client"], world["admin"].headers
    found: dict[str, str] = {}

    def _delivered() -> bool:
        event_ids = [launch["event_id"]] if "event_id" in launch else []
        if not event_ids:
            events = _ok(client.get(f"/api/events/sources/{launch['source_id']}/events", headers=headers))
            event_ids = [
                e["id"] for e in events["items"]
                if (launch.get("marker") and (e.get("data") or {}).get("marker") == launch["marker"])
                or (launch.get("event_type") and e.get("event_type") == launch["event_type"])
            ]
        for event_id in event_ids:
            deliveries = _ok(client.get(f"/api/events/{event_id}/deliveries", headers=headers))
            for delivery in deliveries["items"]:
                if delivery.get("workflow_id") == workflow_id and delivery.get("execution_id"):
                    found["id"] = delivery["execution_id"]
                    return True
        return False

    assert poll_until(_delivered, max_wait=max_wait, interval=0.5), f"no delivery for {launch}"
    return found["id"]


@pytest.fixture(scope="module")
def matrix_runs(scenario_world, async_session_factory) -> dict[str, Any]:
    world = scenario_world
    launched: dict[str, dict] = {}
    direct: dict[str, dict] = {}
    posted: dict[str, str] = {}
    chat_start = None
    for start in rule.STARTS:
        if start.entry in ("rest", "org_override"):
            launched[start.key] = _start_rest(world, start)
        elif start.entry == "direct":
            direct[start.key] = _direct_reads(world, start)
        else:
            launched[start.key] = {"accepted": True, **_start_other(world, start, posted)}
            if start.entry == "chat":
                chat_start = start

    chat_person = world["people"][chat_start.person] if chat_start and chat_start.person else None
    chat_execution = asyncio.run(_in_process(world, async_session_factory, chat_person))
    if chat_start is not None:
        launched[chat_start.key]["execution_id"] = chat_execution

    runs: dict[str, dict] = {}
    for start in rule.STARTS:
        launch = launched.get(start.key)
        if launch is None:
            continue
        if not launch["accepted"]:
            runs[start.key] = {"refused_http": launch["http"]}
            continue
        execution_id = launch.get("execution_id") or _resolve_event_execution(
            world, launch, world["workflows"][start.probe]["id"]
        )
        _collect_tree(world, execution_id, start.children, start.key, runs)
    labels = {str(person.user_id): f"person:{key}" for key, person in world["people"].items()}
    labels[ENGINE_USER_ID] = "engine"
    return {
        "runs": runs,
        "labels": labels,
        "direct": direct,
        "rows": _rows_by_partition(world),
        "elapsed": time.monotonic() - world["started"],
    }
