"""Fixed local TCP response-loss probe; never a provider or recovery service."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import httpx


async def main():
    from sqlalchemy import select
    from src.core.database import close_db, get_db_context, init_db
    from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
    from src.models.enums import ExecutionStatus
    from src.models.orm import Execution
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
        nonlocal pool_seen, fixture_worker_id, fixture_execution_id
        await original_process(consumer, message, claimed)
        pool_seen = consumer._pool
        fixture_worker_id = consumer.owner_worker_id
        fixture_execution_id = message["execution_id"]
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
    assert sys.argv[1:] == ["--response-loss"]
    try:
        asyncio.run(main())
    except Exception as exc:
        print(json.dumps({"runtime": "local-workerd-canonical-response-loss", "error_type": type(exc).__name__}))
        raise SystemExit(1)
