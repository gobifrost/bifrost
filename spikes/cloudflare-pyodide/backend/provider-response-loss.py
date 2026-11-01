"""Fixed local TCP response-loss probe; never a provider or recovery service."""
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import httpx


async def main(*, recover_retained=False):
    from sqlalchemy import select
    from src.core.database import close_db, get_db_context, init_db
    from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
    from src.models.enums import ExecutionStatus
    from src.models.orm import Execution, ExecutionLog
    from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim

    spec = importlib.util.spec_from_file_location("canonical_response_loss", Path(__file__).with_name("canonical-local.py"))
    assert spec is not None and spec.loader is not None
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    original_post = httpx.AsyncClient.post
    original_process = WorkflowExecutionConsumer.process_message
    observed = asyncio.get_running_loop().create_future()
    report = {"runtime": "local-workerd-canonical-response-loss", "provider_response_loss_probe": True,
              "cloud_invocations": 0, "physical_termination_verified": False, "recovery_acceptance": False}
    pool_seen = None
    fixture_worker_id = None
    fixture_execution_id = None
    module_path = None
    launcher = None
    lease_ref = None
    if recover_retained:
        report["provider_retained_response_probe"] = True
        report["control_transport_simulated"] = True

    async def drop_response(reader, writer):
        nonlocal module_path
        try:
            # Only one bounded POST to this ephemeral loopback socket is legal.
            assert not observed.done()
            header = await reader.readuntil(b"\r\n\r\n")
            assert len(header) <= 16384 and header.startswith(b"POST / HTTP/1.1\r\n")
            headers = dict(line.split(b":", 1) for line in header.split(b"\r\n")[1:] if b":" in line)
            headers = {k.lower(): v.strip() for k, v in headers.items()}
            assert b"transfer-encoding" not in headers
            size = int(headers[b"content-length"])
            assert 0 < size <= 262144
            body = await reader.readexactly(size)
            request = json.loads(body)
            async with httpx.AsyncClient(timeout=90, follow_redirects=False) as upstream:
                response = await original_post(upstream, "http://bifrost-pyodide-ddf316c1:8799",
                                               content=body, headers={"Content-Type": "application/json"})
                assert response.status_code == 200 and len(response.content) <= 262144
                evidence = response.json()
                assert evidence["result"]["success"] is True
                assert evidence["result"]["result"]["value"] == 70
                assert evidence["result"]["result"]["credential_redaction_probe"] == "[REDACTED]"
                writes = [call for call in evidence["api_calls"]
                          if call["path"].endswith("/documents/upsert") and call["status"] == 200]
                assert len(writes) == 1
                document_path = writes[0]["path"].removesuffix("/upsert") + "/proof"
                context = request["context"]
                module_path = "/api/sdk/modules/" + context["file_path"]
                # Independent read before dropping the response, while the run is live.
                document = await upstream.get("http://api:8000" + document_path,
                    headers={"Authorization": "Bearer " + context["engine_token"]},
                    params={"scope": context["organization"]["id"]})
                assert document.status_code == 200 and document.json()["data"]["value"] == 70
            report.update(provider_success_before_drop=True, independent_document_value=70,
                          successful_upserts=1, response_bytes_discarded=len(response.content))
            if recover_retained:
                assert launcher is not None
                encoded = json.dumps(evidence)
                assert not any(context.get(key) and context[key] in encoded
                               for key in ("engine_token", "engine_refresh_token"))
                assert evidence["result"]["execution_id"] == request["execution_id"]
                assert evidence["result"]["metrics"]["code_hash"] == request["manifest"][context["file_path"]]
                report.update(workspace_sha256=request["manifest"],
                    observed_code_hash=evidence["result"]["metrics"]["code_hash"],
                    provider_execution_id=evidence["result"]["execution_id"],
                    provider_worker_id=evidence["result"]["worker_id"])
                try:
                    await launcher.send_terminal(
                        {"event": "result", "route_id": "route", "result": evidence["result"]},
                        inventory={"route_id": "route", "kind": "workflow", "subject_id": request["execution_id"]})
                except ConnectionError:
                    pass  # Upstream retains the event before the deliberately failed send.
                else:
                    raise AssertionError("First control send must fail")
                assert len(launcher.pending_terminal) == 1
                report["upstream_retained_before_response_drop"] = True
            observed.set_result(True)
            # The caller receives no HTTP response bytes, despite completed effects.
        except Exception as exc:
            if not observed.done():
                observed.set_exception(exc)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(drop_response, "127.0.0.1", 0, limit=16384)
    port = server.sockets[0].getsockname()[1]

    async def intercepted_post(client, url, *args, **kwargs):
        provider_request = str(url) == "http://bifrost-pyodide-ddf316c1:8799"
        if provider_request:
            url = f"http://127.0.0.1:{port}"
        try:
            return await original_post(client, url, *args, **kwargs)
        except httpx.RemoteProtocolError:
            if provider_request:
                report["collector_error_type"] = "RemoteProtocolError"
            raise

    async def capture_process(consumer, message, claimed):
        nonlocal pool_seen, fixture_worker_id, fixture_execution_id, launcher, lease_ref
        if recover_retained:
            from uuid import uuid4
            from src.worker.remote.fencing import current_remote_lease_ref
            from src.worker.remote.identity import LauncherIdentity
            from src.worker.remote.launcher import Launcher

            lease_ref = current_remote_lease_ref()
            assert lease_ref is not None

            class DisconnectedControl:
                async def send(self, _frame):
                    raise OSError("Fixed control transport loss")

            launcher = Launcher(DisconnectedControl(), identity=LauncherIdentity(
                lease_ref.lease.launcher_id, uuid4(), None, None, "local-retained-fixture", "static"),
                secret=os.environ["BIFROST_SECRET_KEY"])
            launcher.session_generation = lease_ref.lease.session_generation
        await original_process(consumer, message, claimed)
        pool_seen = consumer._pool
        fixture_worker_id = consumer.owner_worker_id
        fixture_execution_id = message["execution_id"]
        if recover_retained:
            report.update(canonical_execution_id=fixture_execution_id, canonical_worker_id=str(fixture_worker_id))
        report["routing_returned_before_collection_wait"] = True
        try:
            await pool_seen.receipt
        except httpx.RemoteProtocolError:
            pass  # Assert and inspect the exact observed failure below; never ACK it.
        else:
            raise AssertionError("Collector must report the actual socket loss")
        await observed
        assert pool_seen.evidence is None and pool_seen.launches == 1
        async with get_db_context() as db:
            from uuid import UUID
            row = await db.get(Execution, UUID(message["execution_id"]))
            assert row is not None and row.status == ExecutionStatus.RUNNING
            assert row.result is None and row.error_message is None
            assert report["collector_error_type"] == "RemoteProtocolError"
            report.update(canonical_status=row.status.value, canonical_error_message=row.error_message,
                          canonical_success_result_missing=True)
        async with httpx.AsyncClient(timeout=15) as api:
            active = await api.get("http://api:8000" + module_path,
                headers={"Authorization": "Bearer " + pool_seen.token})
            assert active.status_code == 200
            report["credential_active_pending_recovery_status"] = active.status_code
        report["launches"] = pool_seen.launches
        if recover_retained:
            from uuid import UUID, uuid4
            import hashlib
            from src.services.execution.memory_monitor import effective_memory_sample
            from src.worker.remote.host import Bridge
            from src.worker.remote.launcher import _receive_control_commands
            from src.worker.remote.ownership import acquire_remote_owner
            from src.worker.remote.protocol import CredentialRouteChanged

            stale = lease_ref.lease
            current = await acquire_remote_owner(worker_id=stale.worker_id, owner_id=stale.owner_id,
                launcher_id=stale.launcher_id, nonce_digest=hashlib.sha256(uuid4().bytes).hexdigest(),
                hello_sequence=2, credential_protocol=2)
            assert current is not None
            lease_ref.lease = current
            event_id, event = next(iter(launcher.pending_terminal.items()))
            async with httpx.AsyncClient(timeout=15) as api:
                active = await api.get("http://api:8000" + module_path,
                    headers={"Authorization": "Bearer " + pool_seen.token})
                assert active.status_code == 200
                report["credential_active_after_owner_reconnect_status"] = active.status_code
            try:
                await pool_seen.journal.reconcile_retained_workflow(stale, "route", event, consumer, pool_seen.info)
            except CredentialRouteChanged:
                report["stale_retained_owner_rejected"] = True
            else:
                raise AssertionError("Stale owner accepted retained result")
            assert await pool_seen.journal.reconcile_retained_workflow(current, "route", event, consumer, pool_seen.info)
            async with get_db_context() as db:
                row = await db.get(Execution, UUID(fixture_execution_id))
                assert row.status == ExecutionStatus.SUCCESS and row.result["value"] == 70
                report.update(local_retained_result_consumed=True, canonical_status_after_recovery=row.status.value,
                              canonical_result_value=row.result["value"])
                logs_before = list(await db.scalars(select(ExecutionLog.id).where(
                    ExecutionLog.execution_id == row.id)))
                assert logs_before

            class AckLink:
                frame = None
                first = True

                async def send_text(self, frame):
                    if self.first:
                        self.first = False
                        raise OSError("Fixed first ACK loss")
                    self.frame = frame

            link = AckLink()
            parent_current, parent_limit = effective_memory_sample()
            bridge = Bridge(link, {"current": parent_current, "limit": parent_limit,
                "hostname": "local-retained-fixture", "requirements": {}}, current)
            try:
                await bridge.acknowledge(event_id)
            except OSError:
                assert event_id in launcher.pending_terminal
                report["retained_event_survived_lost_ack"] = True
            else:
                raise AssertionError("First ACK unexpectedly delivered")
            assert await pool_seen.journal.reconcile_retained_workflow(current, "route", event, consumer, pool_seen.info)
            await bridge.acknowledge(event_id)
            launcher.session_generation = current.session_generation

            class Receiver:
                received = False

                async def recv(self):
                    if not self.received:
                        self.received = True
                        return link.frame
                    launcher.retired.set()  # End only this test control receiver.
                    return await asyncio.get_running_loop().create_future()

            await _receive_control_commands(launcher, Receiver(), asyncio.Event())
            assert launcher.pending_terminal == launcher.pending_terminal_inventory == {}
            assert launcher.template.pid is None and launcher.agent_template.pid is None
            async with get_db_context() as db:
                logs_after = list(await db.scalars(select(ExecutionLog.id).where(
                    ExecutionLog.execution_id == UUID(fixture_execution_id))))
                assert set(logs_after) == set(logs_before)
            async with httpx.AsyncClient(timeout=15) as api:
                revoked = await api.get("http://api:8000" + module_path,
                    headers={"Authorization": "Bearer " + pool_seen.token})
                assert revoked.status_code == 401
                refresh = await original_post(api, "http://api:8000/auth/refresh",
                    json={"refresh_token": pool_seen.refresh_token})
                assert refresh.status_code == 401
                report.update(credential_revoked_by_canonical_success_status=revoked.status_code,
                              refresh_revoked_by_canonical_success_status=refresh.status_code)
            assert pool_seen.launches == 1
            report.update(local_recovery_verified=True, persisted_log_count=len(logs_after),
                          upstream_ack_consumed=True, fixture_only_control_receiver_stopped=True)

    try:
        with patch.object(httpx.AsyncClient, "post", intercepted_post), \
             patch.object(WorkflowExecutionConsumer, "process_message", capture_process):
            try:
                await fixture.main()
            except httpx.RemoteProtocolError:
                # Collection is outside upstream's routing/setup error handler.
                assert pool_seen is not None and report.get("canonical_success_result_missing") is True
                report["success_fixture_acceptance_failed"] = True
            else:
                raise AssertionError("Lost provider response must not pass success acceptance")
    except Exception:
        print(json.dumps({**report, "qualification_failed": True}))
        raise
    finally:
        server.close()
        await server.wait_closed()
        if observed.done():
            observed.result()  # Preserve proxy failures, never leave them unobserved.
    assert pool_seen is not None
    await init_db()
    try:
        async with get_db_context() as db:
            from uuid import UUID
            assert fixture_worker_id is not None
            assert await db.get(ExecutionWorker, fixture_worker_id) is None
            assert not list(await db.scalars(select(ExecutionWorkerClaim).where(
                ExecutionWorkerClaim.worker_id == fixture_worker_id)))
            assert await db.get(Execution, UUID(fixture_execution_id)) is None
        report["fixture_worker_cleanup_verified"] = True
        report["fixture_execution_cleanup_verified"] = True
        async with httpx.AsyncClient(timeout=15) as api:
            revoked = await api.get("http://api:8000" + module_path,
                headers={"Authorization": "Bearer " + pool_seen.token})
            assert revoked.status_code == 401
            report["credential_revoked_by_fixture_cleanup_status"] = revoked.status_code
    finally:
        await close_db()
    print(json.dumps(report))


if __name__ == "__main__":
    assert sys.argv[1:] in (["--response-loss"], ["--retained-response"])
    try:
        asyncio.run(main(recover_retained=sys.argv[1] == "--retained-response"))
    except Exception as exc:
        print(json.dumps({"runtime": "local-workerd-canonical-response-loss", "error_type": type(exc).__name__}))
        raise SystemExit(1)
