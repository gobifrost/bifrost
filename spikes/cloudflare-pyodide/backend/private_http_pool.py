"""Private HTTP physical adapter; upstream retains all durable execution authority."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import PurePosixPath
import re
import time
from types import SimpleNamespace
from uuid import UUID

import httpx

from attempt_journal import AttemptJournal, AttemptResult
from attempt_deadline import report_expired_workflow, supervise_attempt_deadline
from http_invocation import HttpInvocation
from runtime_inventory import validate_inventory, bind_package_requirements
from workflow_request_mux import WorkflowRequestMux

from src.services.execution.process_pool import CredentialRoute
from src.worker.remote.ownership import owns_remote_session
from src.worker.remote.protocol import CLOUDFLARE_PYTHON_RUNTIME_PROFILE, CredentialRouteChanged
from src.worker.remote.remote_pool import ThinRemoteProcessPool

# Companion profile vocabulary, not an upstream scheduler/admission API.
# These declarations are trusted-host input; arbitrary Python can lie about them.
SUPPORTED_REQUIREMENTS = frozenset(('json', 'async-workflow', 'pinned-workspace',
                                   'bifrost-http-sdk'))


class _Attempt(SimpleNamespace):
    def __repr__(self):
        return 'PrivateHttpAttempt()'


def _observe_delivery(task):
    # Retain the error on the owned task/receipt without an unobserved-task
    # warning. No retry, synthetic terminal result or credential-bearing log.
    if not task.cancelled():
        task.exception()


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
        self._runtime_inventory = None
        self._lock = asyncio.Lock()
        self.mux = WorkflowRequestMux(lambda: lease_ref.lease.session_generation)
        self.journal = AttemptJournal(capacity=capacity)
        mux = self.mux
        pool = self

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
                if action == 'package_tool':
                    if payload != {'operation': 'inventory'} or pool._runtime_inventory is None:
                        raise RuntimeError('Only bound immutable runtime inventory is supported')
                    return {'packages': json.loads(json.dumps(pool._runtime_inventory['packages']))}
                if action == 'workflow':
                    owner = lease_ref.lease
                    if (expected_generation != owner.session_generation
                        or not await owns_remote_session(owner) or owner != lease_ref.lease):
                        slot = pool._attempts.get(payload['execution_id'])
                        if (slot is not None and slot.invocation is None and slot.launches == 0
                            and slot.info is None and mux.collection(payload['execution_id']) is None):
                            slot.no_send_refusal = True
                        raise CredentialRouteChanged('Physical workflow owner changed before deadline binding')
                    pool._start_deadline(payload)
                try:
                    return await mux.request(action, payload, expected_generation=expected_generation)
                except httpx.TransportError as exc:
                    # A request may have reached the child before its ACK was
                    # lost. Native ConnectionError preserves that route; the
                    # original collection task still retains its exact error.
                    raise ConnectionError('Physical HTTP outcome uncertain; reconciliation required') from exc

        self.native = ThinRemoteProcessPool(Transport(), worker_id=str(self.worker_id),
            runtime_profile=CLOUDFLARE_PYTHON_RUNTIME_PROFILE, on_result=on_result)
        self._consumer = SimpleNamespace(owner_worker_id=self.worker_id, _handle_result=self._deliver)
        self._deadline_consumer = SimpleNamespace(owner_worker_id=self.worker_id, _handle_result=on_result)

    def bind_runtime_inventory(self, probe):
        """Trusted provider probe only; does not qualify or start this executor."""
        if self._retired or self._attempts or self._runtime_inventory is not None:
            raise RuntimeError('Runtime inventory binding unavailable')
        self._runtime_inventory = validate_inventory(probe)

    def _start_deadline(self, payload):
        # Native ExecutionInfo exists before transport awaits the launch ACK.
        slot = self._attempts[payload['execution_id']]
        if slot.info is not None:
            raise ConnectionError('Native attempt already bound')
        slot.route = payload['route_id']
        slot.info = self.native.processes[slot.route].current_execution

        async def supervise():
            expired = await supervise_attempt_deadline(self._ref.lease, self._deadline_consumer,
                slot.info, slot.settled, lease_at_cutoff=lambda: self._ref.lease)
            return {'expired': expired}

        slot._deadline_task = asyncio.create_task(supervise())
        slot._deadline_task.add_done_callback(_observe_delivery)
        slot.deadline = AttemptResult(slot._deadline_task)

    def credential_route(self):
        return CredentialRoute(2, self._ref.lease.session_generation)

    def bind_manifest(self, execution_id, manifest, *, requirements, packages):
        """Bind trusted-host provenance/requirements, not user authorization.

        Requirements describe reviewed code under these pins. They neither
        discover dynamic behavior nor enforce a sandbox inside Python.
        """
        if self._retired or execution_id in self._plans or len(self._plans) >= self._capacity:
            raise RuntimeError('Manifest binding unavailable; preserve existing attempts')
        if not isinstance(execution_id, str) or str(UUID(execution_id)) != execution_id:
            raise ValueError('Canonical execution identity required')
        if not isinstance(manifest, dict) or not 1 <= len(manifest) <= 128:
            raise ValueError('Bounded Python module manifest required')
        if (not isinstance(requirements, (list, tuple)) or len(requirements) > 32
            or any(not isinstance(item, str) or item not in SUPPORTED_REQUIREMENTS for item in requirements)
            or len(set(requirements)) != len(requirements)):
            raise ValueError('Explicit supported workload requirements required before binding')
        bound_packages = bind_package_requirements(self._runtime_inventory, packages)
        for path, digest in manifest.items():
            if (not isinstance(path, str) or len(path) > 512 or '\\' in path
                or str(PurePosixPath(path)) != path or PurePosixPath(path).is_absolute()
                or '..' in PurePosixPath(path).parts or not path.endswith('.py')
                or not isinstance(digest, str) or re.fullmatch('[a-f0-9]{64}', digest) is None):
                raise ValueError('Canonical relative Python paths and SHA256 digests required')
        self._plans[execution_id] = {'manifest': dict(manifest), 'requirements': tuple(sorted(requirements)),
                                    'packages': bound_packages}

    def attempt(self, execution_id):
        """Trusted-parent receipt access; never expose as a public result API."""
        return self._attempts[execution_id]

    async def route_execution(self, *, execution_id, context, credential_route, active_execution):
        routed = await self._reserve_execution(execution_id=execution_id, context=context,
            credential_route=credential_route, active_execution=active_execution)
        await asyncio.shield(routed)

    async def _reserve_execution(self, *, execution_id, context, credential_route, active_execution):
        async with self._lock:
            if self._retired:
                raise RuntimeError('Private HTTP adapter retired')
            lease = self._ref.lease
            if (credential_route != self.credential_route() or lease.worker_id != self.worker_id
                or not await owns_remote_session(lease) or lease != self._ref.lease):
                raise CredentialRouteChanged('Private HTTP dispatch owner changed')
            plan = self._plans.get(execution_id)
            manifest = None if plan is None else plan['manifest']
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
            fingerprint = json.dumps([frozen, frozen_active, plan], sort_keys=True, separators=(',', ':'),
                                     allow_nan=False, default=str)
            previous = self._attempts.get(execution_id)
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise ConnectionError('Existing workflow dispatch changed; reconcile original attempt')
                return previous.routed
            envelope = {'mode': 'canonical', 'worker_id': str(self.worker_id), 'execution_id': execution_id,
                        'context': frozen, 'manifest': manifest}
            HttpInvocation.validated_request(envelope)
            # Budget the longest UTC isoformat string the native pool can add.
            # This copy is for sizing only; the actual cutoff remains native.
            HttpInvocation.validated_request({**envelope, 'context': {
                **frozen, 'workflow_deadline': '9999-12-31T23:59:59.999999+00:00'}})
            slot = _Attempt(fingerprint=fingerprint, invocation=None, evidence=None, info=None,
                route=None, receipt=None, accepted_monotonic=None, terminal_monotonic=None, launches=0,
                no_send_refusal=False, routed=asyncio.get_running_loop().create_future(),
                settled=asyncio.get_running_loop().create_future(), _deadline_task=None)
            slot.retained_fingerprint = None
            slot.deadline_reconciled_owner = None
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

            async def deliver_terminal():
                await slot.receipt
                if not await self.reconcile(execution_id):
                    raise ConnectionError('Terminal not acknowledged; reconciliation required')
                return {'acknowledged': True}

            slot._delivery_task = asyncio.create_task(deliver_terminal())
            slot._delivery_task.add_done_callback(_observe_delivery)
            slot.delivery = AttemptResult(slot._delivery_task)
            return slot.routed

    async def _deliver(self, result):
        slot = self._attempts[result['execution_id']]
        if datetime.now(timezone.utc) >= slot.info.started_at + timedelta(seconds=slot.info.timeout_seconds):
            await self.reconcile_deadline(result['execution_id'])
        await self.native.handle_event({'event': 'result', 'route_id': slot.route, 'result': result})

    async def reconcile_deadline(self, execution_id):
        """Replay only logical timeout through the current canonical owner."""
        slot = self._attempts[execution_id]
        owner = self._ref.lease
        await report_expired_workflow(owner, self._deadline_consumer, slot.info)
        slot.deadline_reconciled_owner = owner

    async def _finish_acknowledged(self, slot):
        if not slot.settled.done():
            slot.settled.set_result({'acknowledged': True})
        try:
            await slot.deadline
        except Exception:
            # Keep the original task/receipt failed. Only a separate successful
            # current-owner timeout consumption can resolve that uncertainty;
            # this does not retry a callback or infer physical termination.
            if slot.deadline_reconciled_owner != self._ref.lease:
                raise

    async def reconcile_retained(self, execution_id, evidence, *, lease=None):
        """Trusted parent only: validated retained HTTP terminal, never rerun code.

        This companion operation is not an upstream launcher event or public
        authentication surface. Caller must authenticate the evidence source.
        """
        slot = self._attempts[execution_id]
        owner = self._ref.lease if lease is None else lease
        if not await owns_remote_session(owner):
            raise CredentialRouteChanged('Stale retained HTTP result owner')
        route = self.journal.routes[execution_id]
        route.require_owner(owner)
        if not route.task.done():
            return False
        if not route.task.cancelled() and route.task.exception() is None:
            raise ValueError('Normal collection requires normal reconciliation')
        encoded = json.dumps(evidence, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode()) > 262144:
            raise ValueError('Bounded retained HTTP evidence required')
        frozen = json.loads(encoded)
        handle = frozen.get('runtime_handle') if isinstance(frozen, dict) else None
        result = frozen.get('result') if isinstance(frozen, dict) else None
        known = None if slot.invocation is None else slot.invocation.runtime_handle
        if (not isinstance(handle, str) or re.fullmatch(r'[A-Za-z0-9._:-]{1,255}', handle) is None
            or (known is not None and known != handle) or not isinstance(result, dict)
            or result.get('execution_id') != execution_id or not isinstance(result.get('success'), bool)):
            raise ValueError('Matching retained HTTP terminal required')
        if slot.retained_fingerprint is not None and slot.retained_fingerprint != encoded:
            raise ValueError('Retained HTTP terminal changed; reconcile original evidence')
        slot.retained_fingerprint = encoded

        async def consume(_route, _subject, terminal):
            await self._deliver(terminal)
            return True

        acknowledged = await self.journal.reconcile_retained_terminal(owner, execution_id, result, consume)
        if acknowledged:
            self.mux.acknowledge_retained(execution_id, result)
            slot.evidence = frozen
            slot.terminal_monotonic = time.monotonic()
            await self._finish_acknowledged(slot)
        return acknowledged

    async def reconcile(self, execution_id, *, lease=None):
        slot = self._attempts[execution_id]
        acknowledged = await self.journal.reconcile_workflow(self._ref.lease if lease is None else lease,
            execution_id, self._consumer, slot.info)
        if acknowledged:
            await self._finish_acknowledged(slot)
        return acknowledged

    async def acknowledge_refusal(self, execution_id, accept, *, lease=None):
        """Private host only: acknowledge canonical consumption, never rerun it.

        Only the explicit pre-collection handoff fence supplies no-send proof.
        Transport loss, cancellation and missing ACKs cannot supply this proof.
        Original failed route/collection/delivery receipts remain unchanged.
        """
        slot = self._attempts[execution_id]
        if (not slot.no_send_refusal or slot.invocation is not None or slot.launches != 0
            or slot._deadline_task is not None or self.mux.collection(execution_id) is not None
            or any(handle.current_execution is not None
                   and handle.current_execution.execution_id == execution_id
                   for handle in self.native.processes.values())):
            raise ValueError('Proven pre-collection no-send refusal required')
        acknowledged = await self.journal.acknowledge_refusal(
            self._ref.lease if lease is None else lease, execution_id, accept)
        if acknowledged:
            self.mux.acknowledge_not_started(execution_id)
            if not slot.settled.done():
                slot.settled.set_result({'acknowledged': True, 'not_started': True})
        return acknowledged

    async def route_service(self, **request):
        # Reuse the native profile's refusal before any service side effect.
        return await self.native.route_service(**request)

    async def retire(self):
        # ACKs have to traverse the actual upstream callback before the companion
        # journal permits retirement. Neither operation establishes CPU stop.
        async with self._lock:
            if any(not slot._delivery_task.done() for slot in self._attempts.values()):
                raise RuntimeError('Owned terminal delivery remains pending')
            if any(slot._deadline_task is not None and not slot._deadline_task.done()
                   for slot in self._attempts.values()):
                raise RuntimeError('Owned deadline supervision remains pending')
            await self.journal.retire()
            self.mux.retire()
            if self.native._redis is not None:
                await self.native._redis.aclose()
                self.native._redis = None
            self._attempts.clear()
            self._plans.clear()
            self._retired = True
