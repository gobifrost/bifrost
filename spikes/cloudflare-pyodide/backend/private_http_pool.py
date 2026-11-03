"""Private HTTP physical adapter; upstream retains all durable execution authority."""
import asyncio
import json
from pathlib import PurePosixPath
import re
import time
from types import SimpleNamespace
from uuid import UUID

from attempt_journal import AttemptJournal
from http_invocation import HttpInvocation
from workflow_request_mux import WorkflowRequestMux

from src.services.execution.process_pool import CredentialRoute
from src.worker.remote.ownership import owns_remote_session
from src.worker.remote.protocol import CLOUDFLARE_PYTHON_RUNTIME_PROFILE, CredentialRouteChanged
from src.worker.remote.remote_pool import ThinRemoteProcessPool


class _Attempt(SimpleNamespace):
    def __repr__(self):
        return 'PrivateHttpAttempt()'


class PrivateHttpWorkflowPool:
    """Physical routing for explicitly bound workflows under a canonical owner.

    The trusted host registers/claims through upstream and supplies the existing
    run-scoped context, immutable provenance and result consumer. This adapter
    does not authorize a user, issue a token, schedule a job or retry effects.
    Public startup/admission and abnormal physical cancellation are unqualified.
    """
    def __init__(self, *, endpoint, worker_id, lease_ref, memory_sample,
                 memory_request_bytes, on_result, capacity, headers=None):
        self.worker_id = UUID(str(worker_id))
        if lease_ref.lease.worker_id != self.worker_id:
            raise ValueError('Canonical worker and owner identity differ')
        self._endpoint = HttpInvocation.validated_endpoint(endpoint)
        self._headers = HttpInvocation.validated_headers(headers)
        self._ref = lease_ref
        self._capacity = capacity
        self._plans = {}
        self._attempts = {}
        self._retired = False
        self._lock = asyncio.Lock()
        self.mux = WorkflowRequestMux(lambda: lease_ref.lease.session_generation)
        self.journal = AttemptJournal(capacity=capacity)
        mux = self.mux

        class Transport:
            connected = True
            hostname = 'private-python-http-adapter'

            @property
            def lease(self):
                return lease_ref.lease

            def memory(self):
                # Parent cgroup sample, never a fabricated isolate measurement.
                return memory_sample()

            def memory_request_bytes(self, key):
                return memory_request_bytes(key)

            async def request(self, action, payload, *, expected_generation=None):
                return await mux.request(action, payload, expected_generation=expected_generation)

        self.native = ThinRemoteProcessPool(Transport(), worker_id=str(self.worker_id),
            runtime_profile=CLOUDFLARE_PYTHON_RUNTIME_PROFILE, on_result=on_result)
        self._consumer = SimpleNamespace(owner_worker_id=self.worker_id, _handle_result=self._deliver)

    def credential_route(self):
        return CredentialRoute(2, self._ref.lease.session_generation)

    def bind_manifest(self, execution_id, manifest):
        """Trusted host provenance input, not a scheduling or authorization API."""
        if self._retired or execution_id in self._plans or len(self._plans) >= self._capacity:
            raise RuntimeError('Manifest binding unavailable; preserve existing attempts')
        if not isinstance(execution_id, str) or str(UUID(execution_id)) != execution_id:
            raise ValueError('Canonical execution identity required')
        if not isinstance(manifest, dict) or not 1 <= len(manifest) <= 128:
            raise ValueError('Bounded Python module manifest required')
        for path, digest in manifest.items():
            if (not isinstance(path, str) or len(path) > 512 or '\\' in path
                or str(PurePosixPath(path)) != path or PurePosixPath(path).is_absolute()
                or '..' in PurePosixPath(path).parts or not path.endswith('.py')
                or not isinstance(digest, str) or re.fullmatch('[a-f0-9]{64}', digest) is None):
                raise ValueError('Canonical relative Python paths and SHA256 digests required')
        self._plans[execution_id] = dict(manifest)

    def attempt(self, execution_id):
        """Trusted-parent receipt access; never expose as a public result API."""
        return self._attempts[execution_id]

    async def route_execution(self, *, execution_id, context, credential_route, active_execution):
        async with self._lock:
            if self._retired:
                raise RuntimeError('Private HTTP adapter retired')
            lease = self._ref.lease
            if (credential_route != self.credential_route() or lease.worker_id != self.worker_id
                or not await owns_remote_session(lease) or lease != self._ref.lease):
                raise CredentialRouteChanged('Private HTTP dispatch owner changed')
            manifest = self._plans.get(execution_id)
            frozen = json.loads(json.dumps(context, default=str, allow_nan=False))
            if (manifest is None or frozen.get('execution_id') != execution_id
                or frozen.get('file_path') not in manifest
                or frozen.get('content_hash') != manifest[frozen['file_path']]
                or not isinstance(frozen.get('function_name'), str) or not frozen['function_name']
                or type(frozen.get('timeout_seconds')) is not int or not 1 <= frozen['timeout_seconds'] <= 30
                or any(frozen.get(key) is not None for key in ('code', 'service', 'solution_id', 'artifact_workspace_id'))
                or any(frozen.get(key) for key in ('is_platform_admin', 'solution_global_repo_access'))):
                raise ValueError('Bound, pinned, supported workflow context required before dispatch')
            frozen_active = json.loads(json.dumps(active_execution, default=str, allow_nan=False))
            fingerprint = json.dumps([frozen, frozen_active], sort_keys=True, separators=(',', ':'),
                                     allow_nan=False, default=str)
            previous = self._attempts.get(execution_id)
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise ConnectionError('Existing workflow dispatch changed; reconcile original attempt')
                await asyncio.shield(previous.routed)
                return
            slot = _Attempt(fingerprint=fingerprint, invocation=None, evidence=None, info=None,
                route=None, receipt=None, accepted_monotonic=None, terminal_monotonic=None, launches=0,
                routed=asyncio.get_running_loop().create_future())
            self._attempts[execution_id] = slot

            async def collect(payload, accepted):
                def on_accepted(handle):
                    slot.accepted_monotonic = time.monotonic()
                    accepted(handle)

                slot.invocation = HttpInvocation(self._endpoint,
                    {'mode': 'canonical', 'worker_id': str(self.worker_id), 'execution_id': execution_id,
                     'context': payload['context'], 'manifest': manifest}, on_accepted, headers=self._headers)
                slot.launches += 1
                slot.evidence = await slot.invocation.collect()
                slot.terminal_monotonic = time.monotonic()
                return slot.evidence['result']

            async def launch():
                try:
                    self.mux.bind(execution_id, collect)
                    await self.native.route_execution(execution_id, frozen, frozen_active, credential_route=credential_route)
                    retained = [(route, handle) for route, handle in self.native.processes.items()
                        if handle.current_execution is not None and handle.current_execution.execution_id == execution_id]
                    if len(retained) != 1:
                        raise ConnectionError('Native workflow route uncertain; reconciliation required')
                    slot.route, handle = retained[0]
                    slot.info = handle.current_execution
                    slot.routed.set_result(None)
                    collection = self.mux.collection(execution_id)
                    if collection is None:
                        raise ConnectionError('No physical collection; reconciliation required')
                    return await asyncio.shield(collection)
                except Exception as exc:
                    if not slot.routed.done():
                        slot.routed.set_exception(exc)
                    raise

            try:
                # Retain the physical receipt before native dispatch can send
                # credentials or lose its ACK. The key is the canonical run ID;
                # the native pool independently supplies its opaque route ID.
                slot.receipt = await self.journal.dispatch(lease, execution_id, execution_id, launch)
            except Exception:
                self._attempts.pop(execution_id, None)
                raise
            await asyncio.shield(slot.routed)

    async def _deliver(self, result):
        slot = self._attempts[result['execution_id']]
        await self.native.handle_event({'event': 'result', 'route_id': slot.route, 'result': result})

    async def reconcile(self, execution_id, *, lease=None):
        slot = self._attempts[execution_id]
        return await self.journal.reconcile_workflow(self._ref.lease if lease is None else lease,
            execution_id, self._consumer, slot.info)

    async def route_service(self, **request):
        # Reuse the native profile's refusal before any service side effect.
        return await self.native.route_service(**request)

    async def retire(self):
        # ACKs have to traverse the actual upstream callback before the companion
        # journal permits retirement. Neither operation establishes CPU stop.
        async with self._lock:
            await self.journal.retire()
            self.mux.retire()
            self._attempts.clear()
            self._plans.clear()
            self._retired = True
