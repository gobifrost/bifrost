import {runtimeCode} from './runtime-code.mjs';
import overlay from './sdk-overlay.mjs';
const python = `
from workers import WorkerEntrypoint, Response
from runtime_guard import require_runtime
import importlib
from sdk_cleanup import close_sdk, _pending
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        result = {"runtime": require_runtime(), "probes": {}}
        for name in ("ssl", "certifi", "bifrost.client"):
            try:
                module = importlib.import_module(name)
                result["probes"][name] = {"imported": True}
            except Exception as error:
                result["probes"][name] = {"imported": False, "error_type": type(error).__name__,
                    "missing_module": getattr(error, "name", None)}
        if result["probes"]["bifrost.client"]["imported"]:
            try:
                from bifrost.client import BifrostClient
                client = BifrostClient("https://unused.invalid", "synthetic-unusable-probe-value")
                result["constructor"] = {"created": True}
                import asyncio
                http = client._get_async_client()
                loop = asyncio.get_running_loop()
                owned = client._loop_clients[loop]
                await owned.started.wait()
                before = owned.cleanup.done()
                await client.close()
                result["client_close"] = {"cleanup_pending_before": not before,
                    "cleanup_done": owned.cleanup.done(), "async_closed": http.is_closed,
                    "sync_closed": client._sync_http.is_closed,
                    "loop_registry_empty": not client._loop_clients}
                await client.close()
                result["client_close"]["second_close_returned"] = True
                interrupted = BifrostClient("https://unused.invalid", "synthetic-unusable-probe-value")
                delayed = interrupted._get_async_client()
                owner = interrupted._loop_clients[loop]
                entered, release = asyncio.Event(), asyncio.Event()
                actual_close = delayed.aclose
                async def held_close():
                    entered.set()
                    await release.wait()
                    await actual_close()
                delayed.aclose = held_close
                waiter = asyncio.create_task(close_sdk(interrupted))
                await entered.wait()
                waiter.cancel()
                try:
                    await waiter
                except asyncio.CancelledError:
                    cancelled = True
                else:
                    cancelled = False
                sync_closed_at_cancel = interrupted._sync_http.is_closed
                pending_at_cancel = len(_pending)
                release.set()
                await asyncio.gather(*tuple(_pending))
                result["interrupted_close"] = {"waiter_cancelled": cancelled,
                    "async_closed_after_release": delayed.is_closed,
                    "owner_cleanup_done": owner.cleanup.done(),
                    "sync_closed_at_cancel": sync_closed_at_cancel,
                    "sync_closed_without_followup": interrupted._sync_http.is_closed,
                    "owned_pending_at_cancel": pending_at_cancel,
                    "owned_pending_after_completion": len(_pending)}
                await interrupted.close()
                result["interrupted_close"]["sync_closed_after_followup"] = interrupted._sync_http.is_closed
            except Exception as error:
                result["constructor"] = {"created": False, "error_type": type(error).__name__}
        if result["probes"]["certifi"]["imported"]:
            import certifi, hashlib, ssl
            content = certifi.contents().encode()
            context = ssl.create_default_context(cafile=certifi.where())
            result["certifi_api"] = {"version": certifi.__version__,
                "contents_sha256": hashlib.sha256(content).hexdigest(),
                "contents_bytes": len(content), "certificate_store": context.cert_store_stats()}
        return Response.json(result)
`;
export default {
  async fetch(request, env) {
    if (env.ISOLATED_TEST_STACK !== 'true' || request.method !== 'POST') return new Response('Local probe only', {status: 403});
    const code = runtimeCode(null, python);
    for (const [name, value] of Object.entries(overlay.files)) {
      code.modules[name] = value.base64 === undefined ? value : {
        data: Uint8Array.from(atob(value.base64), character => character.charCodeAt(0)).buffer,
      };
    }
    const response = await env.LOADER.load(code).getEntrypoint().fetch(new Request('https://unused.invalid/'));
    // Fixed synthetic import/constructor probe; no workflow or network capability.
    return response;
  },
};
