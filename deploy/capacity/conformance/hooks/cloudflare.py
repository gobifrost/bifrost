"""Cloudflare Container hook for the capacity conformance suite.

The suite cannot see physical launches over the capacity protocol, so it asks a
hook. On Cloudflare that means asking the Containers dashboard API for the
application's instance placements and Durable Object assignments.

Supported operations
    observe           -> {"launches": [<process-start identity>...], "running": <int>}
    restart-provider  -> opt-in re-upload of the configured Worker source
    stop-process      -> unsupported (Cloudflare exposes no targeted public API)

Restart is deliberately opt-in. It re-uploads the checked-in provider modules
and current Worker bindings without a migration, while retaining the existing
CAPACITY_TOKEN secret. The hook does not provide a production debug endpoint or
an in-container control path. Cloudflare's public Containers API documents
instance reads, but not a targeted instance stop/delete operation; reporting
that as a supported fault would make the durability proof untruthful.

Environment
    CLOUDFLARE_API_TOKEN   bearer token with Workers Containers Read; the opt-in
                           restart also needs Workers Scripts Edit
    CF_ACCOUNT_ID          account id
    CF_CONTAINER_APP       container application id (see `wrangler containers list`)
    CF_HOOK_STATE          optional path for the cumulative launch log
    CF_HOOK_ALLOW_MUTATIONS=1
                           explicit opt-in for restart-provider
    CF_SCRIPT_NAME         configured Worker script name for restart-provider
    CF_PROVIDER_SOURCE     directory containing capacity.mjs and capacity-core.mjs
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API = "https://api.cloudflare.com/client/v4"
STATE_PATH = os.environ.get("CF_HOOK_STATE", "/tmp/cf-capacity-launches.json")


class Unsupported(Exception):
    pass


def _get(path: str) -> dict:
    return _request("GET", path)


def _request(
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> dict:
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not token:
        raise Unsupported("CLOUDFLARE_API_TOKEN is not set")
    request_headers = {"Authorization": f"Bearer {token}"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        f"{API}{path}", data=data, method=method, headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise Unsupported(f"Cloudflare API {exc.code}") from None
    except (urllib.error.URLError, TimeoutError):
        raise Unsupported("Cloudflare API unavailable") from None
    except (UnicodeDecodeError, ValueError):
        raise Unsupported("Cloudflare API returned malformed JSON") from None
    if not isinstance(body, dict):
        raise Unsupported("Cloudflare API returned malformed JSON")
    if body.get("success") is not True:
        raise Unsupported("Cloudflare API returned success=false")
    return body


def _instances() -> dict:
    account = os.environ.get("CF_ACCOUNT_ID")
    app = os.environ.get("CF_CONTAINER_APP")
    if not account or not app:
        raise Unsupported("CF_ACCOUNT_ID and CF_CONTAINER_APP are required")
    body = _get(f"/accounts/{account}/containers/dash/applications/{app}/instances")
    result = body.get("result")
    if not isinstance(result, dict):
        raise Unsupported("Cloudflare API returned malformed instance telemetry")
    return result


def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    tmp = f"{STATE_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, STATE_PATH)


def _process_start_identity(instance_id: str, placement_id: str, event_id: str) -> str:
    return f"{instance_id}:{placement_id}:{event_id}"


def _target_placement(telemetry: dict, worker_id: str) -> tuple[str, str, dict] | None:
    """Return the current placement assigned to ``worker_id``, if any.

    Cloudflare keeps Durable Object assignments after their instances are gone.
    An unassigned target object is therefore a valid terminal observation, not
    evidence of a current launch. Every active assignment must be fully linked
    through the Durable Object, instance, and placement identifiers.
    """
    instances = telemetry.get("instances")
    durable_objects = telemetry.get("durable_objects")
    if not isinstance(instances, list) or not isinstance(durable_objects, list):
        raise Unsupported("Cloudflare API omitted instance telemetry")
    if not all(isinstance(instance, dict) for instance in instances) or not all(
        isinstance(durable_object, dict) for durable_object in durable_objects
    ):
        raise Unsupported("Cloudflare API returned malformed instance telemetry")

    targets = [durable_object for durable_object in durable_objects if durable_object.get("name") == worker_id]
    if len(targets) > 1:
        raise Unsupported("Cloudflare API returned ambiguous worker assignment")
    if not targets:
        return None

    durable_object = targets[0]
    actor_id = durable_object.get("id")
    deployment_id = durable_object.get("deployment_id")
    placement_id = durable_object.get("placement_id")
    if not isinstance(actor_id, str) or not actor_id:
        raise Unsupported("Cloudflare API omitted Durable Object identity")
    if deployment_id is None and placement_id is None:
        return None
    if not isinstance(deployment_id, str) or not deployment_id or not isinstance(placement_id, str) or not placement_id:
        raise Unsupported("Cloudflare API returned incomplete worker assignment")

    matched_instances = [instance for instance in instances if instance.get("id") == deployment_id]
    if len(matched_instances) != 1:
        raise Unsupported("Cloudflare API could not link worker assignment to an instance")
    placement = matched_instances[0].get("current_placement")
    if not isinstance(placement, dict):
        raise Unsupported("Cloudflare API omitted current placement telemetry")
    if (
        placement.get("id") != placement_id
        or placement.get("deployment_id") != deployment_id
        or placement.get("durable_object_actor_id") != actor_id
    ):
        raise Unsupported("Cloudflare API returned inconsistent worker placement telemetry")
    return deployment_id, placement_id, placement


def _known_launches(state: dict, worker_id: str) -> set[str]:
    launches = state.get(worker_id, [])
    if (
        not isinstance(launches, list)
        or not all(
            isinstance(launch, str)
            and len(launch.split(":")) == 3
            and all(part for part in launch.split(":"))
            for launch in launches
        )
    ):
        raise Unsupported("Cloudflare hook launch state is malformed")
    return set(launches)


def observe(payload: dict) -> dict:
    """Cumulative launch identities for one worker.

    A Durable Object name maps a worker UUID to its current deployment and
    placement. Each ``ContainerStarted`` event is a physical process start,
    including a restart in the same VM. Identities accumulate across calls, so
    an instance that has since stopped still counts and a sequential duplicate
    launch remains detectable.
    """
    worker_id = payload.get("worker_id", "")
    if not isinstance(worker_id, str) or not worker_id:
        raise Unsupported("worker_id is required")
    telemetry = _instances()
    state = _load_state()
    known = _known_launches(state, worker_id)
    running = 0
    target = _target_placement(telemetry, worker_id)
    if target is not None:
        instance_id, placement_id, placement = target
        status = placement.get("status")
        events = placement.get("events")
        if not isinstance(status, dict) or not isinstance(status.get("container_status"), str):
            raise Unsupported("Cloudflare API omitted container status telemetry")
        if not isinstance(events, list) or not all(isinstance(event, dict) for event in events):
            raise Unsupported("Cloudflare API omitted placement event telemetry")
        starts = [event for event in events if event.get("name") == "ContainerStarted"]
        if not starts:
            raise Unsupported("Cloudflare API omitted ContainerStarted launch telemetry")
        for event in starts:
            event_id = event.get("id")
            event_time = event.get("time")
            if not isinstance(event_id, str) or not event_id or not isinstance(event_time, str) or not event_time:
                raise Unsupported("Cloudflare API returned malformed ContainerStarted telemetry")
            known.add(_process_start_identity(instance_id, placement_id, event_id))
        running = int(status["container_status"] == "running")
    state[worker_id] = sorted(known)
    _save_state(state)
    return {"launches": state[worker_id], "running": running}


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise Unsupported(f"{name} is required")
    return value


def _mutation_configuration(payload: dict) -> tuple[str, str, Path, str]:
    if os.environ.get("CF_HOOK_ALLOW_MUTATIONS") != "1":
        raise Unsupported("restart-provider requires CF_HOOK_ALLOW_MUTATIONS=1")
    scope = payload.get("scope")
    if not isinstance(scope, str) or not scope:
        raise Unsupported("scope is required")
    account = _required_env("CF_ACCOUNT_ID")
    script_name = _required_env("CF_SCRIPT_NAME")
    source_value = _required_env("CF_PROVIDER_SOURCE")
    try:
        source = Path(source_value).resolve(strict=True)
    except OSError:
        raise Unsupported("CF_PROVIDER_SOURCE is not a readable directory") from None
    if not source.is_dir():
        raise Unsupported("CF_PROVIDER_SOURCE is not a directory")
    return account, script_name, source, scope


def _result(body: dict, context: str) -> dict | list:
    result = body.get("result")
    if not isinstance(result, (dict, list)):
        raise Unsupported(f"Cloudflare API returned malformed {context}")
    return result


def _configured_bindings(settings: dict, scope: str) -> list[dict]:
    bindings = settings.get("bindings")
    if not isinstance(bindings, list) or not all(isinstance(binding, dict) for binding in bindings):
        raise Unsupported("Cloudflare API returned malformed Worker settings")

    expected_durable_objects = {
        "WORKER_LEDGER": "WorkerLedger",
        "SCOPE_REGISTRY": "ScopeRegistry",
        "WORKER_CONTAINER": "WorkerContainer",
    }
    for name, class_name in expected_durable_objects.items():
        matches = [
            binding
            for binding in bindings
            if binding.get("name") == name
            and binding.get("type") == "durable_object_namespace"
            and binding.get("class_name") == class_name
        ]
        if len(matches) != 1:
            raise Unsupported("Cloudflare Worker Durable Object bindings do not match the provider")
    scope_bindings = [
        binding
        for binding in bindings
        if binding.get("name") == "SCOPE" and binding.get("type") == "plain_text"
    ]
    if len(scope_bindings) != 1 or scope_bindings[0].get("text") != scope:
        raise Unsupported("Cloudflare Worker scope does not match the requested conformance scope")
    return [dict(binding) for binding in bindings if binding.get("name") != "CONFORMANCE_RESTART_NONCE"]


def _validate_service(service: dict) -> None:
    try:
        migration_tag = service["default_environment"]["script"]["migration_tag"]
    except (KeyError, TypeError):
        raise Unsupported("Cloudflare API returned malformed Worker service") from None
    if not isinstance(migration_tag, str) or not migration_tag:
        raise Unsupported("Cloudflare API returned malformed Worker service")


def _current_version(account: str, script_name: str) -> str:
    script = urllib.parse.quote(script_name, safe="")
    versions = _result(
        _get(f"/accounts/{account}/workers/scripts/{script}/versions?per_page=1"),
        "Worker versions",
    )
    if not isinstance(versions, list) or not versions or not isinstance(versions[0], dict):
        raise Unsupported("Cloudflare API returned malformed Worker versions")
    version_id = versions[0].get("id")
    if not isinstance(version_id, str) or not version_id:
        raise Unsupported("Cloudflare API returned malformed Worker versions")
    return version_id


def _provider_modules(source: Path) -> dict[str, bytes]:
    modules: dict[str, bytes] = {}
    for name in ("capacity.mjs", "capacity-core.mjs"):
        try:
            modules[name] = (source / name).read_bytes()
        except OSError:
            raise Unsupported("CF_PROVIDER_SOURCE is missing checked-in provider modules") from None
    return modules


def _multipart_worker_upload(metadata: dict, modules: dict[str, bytes]) -> tuple[dict[str, str], bytes]:
    boundary = f"----bifrost-capacity-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def part(name: str, content: bytes, content_type: str, filename: str | None = None) -> None:
        chunks.append(f"--{boundary}\r\n".encode())
        disposition = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        chunks.append(f"{disposition}\r\nContent-Type: {content_type}\r\n\r\n".encode())
        chunks.append(content)
        chunks.append(b"\r\n")

    part("metadata", json.dumps(metadata, separators=(",", ":")).encode(), "application/json")
    for name, content in modules.items():
        part(name, content, "application/javascript+module", name)
    chunks.append(f"--{boundary}--\r\n".encode())
    return {"Content-Type": f"multipart/form-data; boundary={boundary}"}, b"".join(chunks)


def restart_provider(payload: dict) -> dict:
    """Force a new Worker version without touching secrets, migrations, or containers."""
    account, script_name, source, scope = _mutation_configuration(payload)
    script = urllib.parse.quote(script_name, safe="")
    service = _result(
        _get(f"/accounts/{account}/workers/services/{script}"),
        "Worker service",
    )
    if not isinstance(service, dict):
        raise Unsupported("Cloudflare API returned malformed Worker service")
    _validate_service(service)
    settings = _result(
        _get(f"/accounts/{account}/workers/scripts/{script}/settings"),
        "Worker settings",
    )
    if not isinstance(settings, dict):
        raise Unsupported("Cloudflare API returned malformed Worker settings")
    bindings = _configured_bindings(settings, scope)
    bindings.append({
        "name": "CONFORMANCE_RESTART_NONCE",
        "type": "plain_text",
        "text": uuid.uuid4().hex,
    })
    metadata: dict[str, object] = {
        "main_module": "capacity.mjs",
        "bindings": bindings,
        "containers": [{"class_name": "WorkerContainer"}],
        # Preserve CAPACITY_TOKEN without ever reading or sending its value.
        "keep_bindings": ["secret_text"],
    }
    for key in ("compatibility_date", "compatibility_flags"):
        if key in settings:
            value = settings[key]
            if (key == "compatibility_date" and not isinstance(value, str)) or (
                key == "compatibility_flags" and (not isinstance(value, list) or not all(isinstance(flag, str) for flag in value))
            ):
                raise Unsupported("Cloudflare API returned malformed Worker settings")
            metadata[key] = value

    previous_version_id = _current_version(account, script_name)
    headers, data = _multipart_worker_upload(metadata, _provider_modules(source))
    _request("PUT", f"/accounts/{account}/workers/scripts/{script}", headers=headers, data=data)
    version_id = _current_version(account, script_name)
    if version_id == previous_version_id:
        raise Unsupported("Cloudflare re-upload did not create a new Worker version")
    return {"ok": True, "previous_version_id": previous_version_id, "version_id": version_id}


def stop_process(payload: dict) -> dict:
    """Fail closed until Cloudflare documents a targeted physical-instance mutation."""
    if os.environ.get("CF_HOOK_ALLOW_MUTATIONS") != "1":
        raise Unsupported("stop-process requires CF_HOOK_ALLOW_MUTATIONS=1")
    worker_id = payload.get("worker_id")
    resource_uid = payload.get("resource_uid")
    if not isinstance(worker_id, str) or not worker_id or not isinstance(resource_uid, str) or not resource_uid:
        raise Unsupported("worker_id and resource_uid are required")
    raise Unsupported(
        "stop-process is unavailable: the public Cloudflare Containers API has no targeted instance stop or delete operation"
    )


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: cloudflare.py <op>"}), file=sys.stderr)
        return 2
    op = sys.argv[1]
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}

    try:
        if op == "observe":
            print(json.dumps(observe(payload)))
            return 0
        if op == "restart-provider":
            print(json.dumps(restart_provider(payload)))
            return 0
        if op == "stop-process":
            print(json.dumps(stop_process(payload)))
            return 0
        if op in ("inject-fault", "clear-fault"):
            raise Unsupported(f"{op} is not available on Cloudflare")
    except Unsupported as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 3

    print(json.dumps({"error": f"unknown op {op}"}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
