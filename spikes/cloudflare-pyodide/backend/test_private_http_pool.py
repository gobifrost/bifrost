"""Native adapter/framing tests; real engine/SDK qualification is separate."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from http_invocation import HttpInvocation
from private_http_pool import PrivateHttpWorkflowPool
from src.worker.remote.protocol import CredentialRouteChanged

CLIENT = httpx.AsyncClient


class Frames(httpx.AsyncByteStream):
    def __init__(self, run, ack, finish, *, lose=False):
        self.run, self.ack, self.finish, self.lose = run, ack, finish, lose

    async def __aiter__(self):
        await self.ack.wait()
        handle = 'invocation:' + self.run
        accepted = {'protocol': 1, 'type': 'accepted', 'execution_id': self.run, 'runtime_handle': handle}
        yield (json.dumps(accepted) + '\n').encode()
        await self.finish.wait()
        if self.lose:
            raise httpx.ReadError('Synthetic stream loss')
        yield (json.dumps({**accepted, 'type': 'result', 'evidence': {'runtime_handle': handle,
            'result': {'execution_id': self.run, 'success': True, 'result': {'value': 7}}}}) + '\n').encode()


class PrivatePoolTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker, self.run = uuid4(), str(uuid4())
        self.ref = SimpleNamespace(lease=SimpleNamespace(worker_id=self.worker, launcher_id=uuid4(),
                                                       session_generation=1, credential_protocol=2))
        self.owner = self.enterContext(patch('private_http_pool.owns_remote_session', new_callable=AsyncMock, return_value=True))
        self.journal_owner = self.enterContext(patch('attempt_journal.owns_remote_session', new_callable=AsyncMock, return_value=True))
        self.enterContext(patch('src.worker.remote.ownership.owns_remote_session', new_callable=AsyncMock, return_value=True))
        self.callback = AsyncMock()
        self.pool = PrivateHttpWorkflowPool(endpoint='https://worker.invalid/', worker_id=self.worker,
            lease_ref=self.ref, memory_sample=lambda: (100, 2**30), memory_request_bytes=lambda _key: 1024,
            on_result=self.callback, capacity=2)
        self.redis_write = self.enterContext(patch.object(self.pool.native, '_write_context_to_redis', new_callable=AsyncMock))
        self.enterContext(patch.object(self.pool.native, '_write_active_execution_lease', new_callable=AsyncMock))
        self.manifest = {'workflows/test.py': 'a' * 64}
        self.context = {'execution_id': self.run, 'file_path': 'workflows/test.py', 'function_name': 'run',
                        'content_hash': 'a' * 64, 'timeout_seconds': 30, 'engine_token': 'synthetic-only-token'}
        self.active = {'execution_id': self.run, 'workflow_id': str(uuid4()), 'workflow_name': 'test',
                       'org_id': None, 'user_id': str(uuid4()), 'user_name': 'test', 'user_email': 'test@example.test',
                       'sync': False, 'event': None}
        self.pool.bind_manifest(self.run, self.manifest)

    async def route(self, context=None, active=None):
        await self.pool.route_execution(execution_id=self.run,
            context=self.context if context is None else context,
            active_execution=self.active if active is None else active, credential_route=self.pool.credential_route())

    def provider(self, *, ack=None, finish=None, lose=False):
        self.sent, self.started = [], asyncio.Event()
        ack = ack or asyncio.Event()
        finish = finish or asyncio.Event()

        def handle(request):
            # Transport receipt exists before the first physical POST.
            self.assertEqual(self.pool.journal.inventory()[0]['execution_id'], self.run)
            self.sent.append(json.loads(request.content))
            self.started.set()
            return httpx.Response(200, headers={'Content-Type': 'application/x-ndjson'},
                                  stream=Frames(self.run, ack, finish, lose=lose))

        self.enterContext(patch('http_invocation.httpx.AsyncClient',
            side_effect=lambda **kw: CLIENT(transport=httpx.MockTransport(handle), **kw)))
        return ack, finish

    async def test_refusals_precede_native_bookkeeping(self):
        for change in [{'content_hash': None}, {'content_hash': 'b' * 64}, {'code': ''},
                       {'timeout_seconds': 31}, {'is_platform_admin': True}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                await self.route({**self.context, **change})
        self.redis_write.assert_not_awaited()
        self.assertEqual(self.pool.journal.inventory(), [])
        self.assertEqual(self.pool.mux.retained_count, 0)
        for manifest in [{'../escape.py': 'a' * 64}, {'/absolute.py': 'a' * 64}, {'ok.py': 'invalid'}]:
            with self.assertRaises(ValueError):
                self.pool.bind_manifest(str(uuid4()), manifest)
        with self.assertRaises(ValueError):
            PrivateHttpWorkflowPool(endpoint='http://other.invalid', worker_id=self.worker, lease_ref=self.ref,
                memory_sample=lambda: (100, 1000), memory_request_bytes=lambda _key: 1,
                on_result=self.callback, capacity=1)

    async def test_owner_loss_at_receipt_reservation_never_starts_native_dispatch(self):
        self.journal_owner.return_value = False
        with self.assertRaises(CredentialRouteChanged):
            await self.route()
        self.redis_write.assert_not_awaited()
        self.assertEqual(self.pool.journal.inventory(), [])
        self.assertEqual(self.pool.mux.retained_count, 0)

    async def test_oversized_first_dispatch_is_rejected_before_native_bookkeeping(self):
        self.enterContext(patch.object(self.pool.native, '_get_redis',
            new=AsyncMock(return_value=SimpleNamespace(delete=AsyncMock()))))
        self.provider()
        with self.assertRaises(ValueError):
            await self.route({**self.context, 'parameters': {'blob': 'x' * 262144}})
        self.redis_write.assert_not_awaited()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.pool.journal.inventory(), [])
        self.assertEqual(self.pool.mux.retained_count, 0)
        with self.assertRaises(KeyError):
            self.pool.attempt(self.run)

    async def test_escaped_unicode_is_budgeted_as_encoded_bytes_before_bookkeeping(self):
        with self.assertRaises(ValueError):
            await self.route({**self.context, 'parameters': {'blob': '\u6f22' * 50000}})
        self.redis_write.assert_not_awaited()
        self.assertEqual(self.pool.journal.inventory(), [])
        self.assertEqual(self.pool.mux.retained_count, 0)

    async def test_combined_manifest_and_context_budget_precedes_bookkeeping(self):
        self.run = str(uuid4())
        manifest = {f'm{i}_' + 'x' * 490 + '.py': 'a' * 64 for i in range(128)}
        self.pool.bind_manifest(self.run, manifest)
        self.context.update(execution_id=self.run, file_path=next(iter(manifest)), parameters={'blob': 'x' * 200000})
        self.active['execution_id'] = self.run
        with self.assertRaises(ValueError):
            await self.route()
        self.redis_write.assert_not_awaited()
        self.assertEqual(self.pool.journal.inventory(), [])

    async def test_maximum_budget_request_is_not_replaced_by_a_smaller_context_limit(self):
        context = {**self.context, 'parameters': {'blob': ''},
                   'workflow_deadline': '9999-12-31T23:59:59.999999+00:00'}
        envelope = {'mode': 'canonical', 'worker_id': str(self.worker), 'execution_id': self.run,
                    'context': context, 'manifest': self.manifest}
        _, empty = HttpInvocation.validated_request(envelope)
        self.context['parameters'] = {'blob': 'x' * (262144 - len(empty))}
        ack, finish = self.provider()
        ack.set()
        await self.route()
        self.assertLessEqual(len(json.dumps(self.sent[0], separators=(',', ':')).encode()), 262144)
        self.assertGreaterEqual(len(json.dumps(self.sent[0], separators=(',', ':')).encode()), 262137)
        self.assertNotEqual(self.sent[0]['context']['workflow_deadline'], context['workflow_deadline'])
        finish.set()
        await self.pool.attempt(self.run).delivery
        await self.pool.retire()

    async def test_duplicate_snapshot_and_ack_retirement_use_actual_native_pool(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def consume(_result):
            entered.set()
            await release.wait()

        self.callback.side_effect = consume
        ack, finish = self.provider()
        ack.set()
        original, active = copy.deepcopy(self.context), copy.deepcopy(self.active)
        await self.route()
        attempt = self.pool.attempt(self.run)
        self.assertNotIn('synthetic-only-token', repr(attempt))
        self.context['engine_token'], self.active['sync'] = 'replacement', True
        await self.route(original, active)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]['context']['engine_token'], 'synthetic-only-token')
        self.assertFalse(attempt.info.active_execution['sync'])
        with self.assertRaises(ConnectionError):
            await self.route(self.context, active)
        finish.set()
        await attempt.receipt
        await entered.wait()
        with self.assertRaises(RuntimeError):
            await self.pool.retire()
        release.set()
        self.assertEqual(await attempt.delivery, {'acknowledged': True})
        self.callback.assert_awaited_once()
        self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 0)
        await self.pool.retire()
        self.assertEqual(self.pool.mux.retained_count, 0)
        with self.assertRaises(RuntimeError):
            await self.route(original, active)

    async def test_cancelled_routing_waiter_keeps_pre_dispatch_receipt_and_physical_task(self):
        ack, finish = self.provider()
        waiter = asyncio.create_task(self.route())
        await self.started.wait()
        attempt = self.pool.attempt(self.run)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(attempt.routed.cancelled())
        self.assertEqual(self.pool.journal.inventory()[0]['collection_state'], 'pending')
        ack.set()
        await self.route()
        finish.set()
        await attempt.receipt
        await attempt.delivery
        self.assertEqual(len(self.sent), 1)
        await self.pool.retire()

    async def test_lost_stream_retains_failed_receipt_and_native_obligation_without_relaunch(self):
        ack, finish = self.provider(lose=True)
        ack.set()
        await self.route()
        attempt = self.pool.attempt(self.run)
        finish.set()
        with self.assertRaises(httpx.ReadError):
            await attempt.receipt
        with self.assertRaises(httpx.ReadError):
            await attempt.delivery
        await self.route()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 1)
        with self.assertRaises(RuntimeError):
            await self.pool.retire()
        self.callback.assert_not_awaited()

    async def test_failed_automatic_callback_retains_result_for_explicit_reconciliation(self):
        ack, finish = self.provider()
        ack.set()
        self.callback.side_effect = ConnectionError('Synthetic ACK loss')
        await self.route()
        attempt = self.pool.attempt(self.run)
        finish.set()
        with self.assertRaisesRegex(ConnectionError, 'Synthetic ACK loss'):
            await attempt.delivery
        self.assertFalse(self.pool.journal.inventory()[0]['acknowledged'])
        self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 1)
        with self.assertRaises(RuntimeError):
            await self.pool.retire()
        self.callback.side_effect = None
        self.assertTrue(await self.pool.reconcile(self.run))
        self.assertEqual(len(self.sent), 1)
        await self.pool.retire()

    async def test_cancelled_delivery_waiter_does_not_cancel_reporting(self):
        ack, finish = self.provider()
        ack.set()
        await self.route()
        attempt = self.pool.attempt(self.run)

        async def wait():
            return await attempt.delivery

        waiter = asyncio.create_task(wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        finish.set()
        self.assertEqual(await attempt.delivery, {'acknowledged': True})
        self.callback.assert_awaited_once()
        await self.pool.retire()

    async def test_persistent_service_uses_native_profile_refusal_before_effects(self):
        with self.assertRaisesRegex(RuntimeError, 'persistent services'):
            await self.pool.route_service(service_id='service', attempt_id='attempt', lease_token='lease', context={})
        self.redis_write.assert_not_awaited()
        self.assertEqual(self.pool.native.service_processes, {})

    async def test_retained_terminal_adoption_keeps_failed_receipt_and_recovers_lost_ack(self):
        ack, finish = self.provider(lose=True)
        ack.set()
        await self.route()
        attempt = self.pool.attempt(self.run)
        finish.set()
        with self.assertRaises(httpx.ReadError):
            await attempt.delivery
        evidence = {'runtime_handle': attempt.invocation.runtime_handle,
                    'result': {'execution_id': self.run, 'success': True, 'result': 7}}
        self.callback.side_effect = ConnectionError('Synthetic retained ACK loss')
        with self.assertRaisesRegex(ConnectionError, 'retained ACK loss'):
            await self.pool.reconcile_retained(self.run, evidence)
        self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 1)
        self.assertFalse(self.pool.journal.inventory()[0]['acknowledged'])
        with self.assertRaises(ValueError):
            await self.pool.reconcile_retained(self.run, {**evidence, 'result': {**evidence['result'], 'result': 9}})
        self.callback.side_effect = None
        self.assertTrue(await self.pool.reconcile_retained(self.run, evidence))
        self.assertTrue(await self.pool.reconcile_retained(self.run, evidence))
        self.assertEqual(self.callback.await_count, 2)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 0)
        with self.assertRaises(httpx.ReadError):
            await attempt.receipt
        await self.pool.retire()

    async def test_retained_terminal_refuses_wrong_identity_and_stale_owner_before_callback(self):
        ack, finish = self.provider(lose=True)
        ack.set()
        await self.route()
        attempt = self.pool.attempt(self.run)
        finish.set()
        with self.assertRaises(httpx.ReadError):
            await attempt.delivery
        result = {'execution_id': self.run, 'success': True}
        for evidence in [
            {'runtime_handle': 'another-handle', 'result': result},
            {'runtime_handle': attempt.invocation.runtime_handle, 'result': {**result, 'execution_id': str(uuid4())}},
            {'runtime_handle': attempt.invocation.runtime_handle, 'result': {**result, 'success': 'yes'}},
        ]:
            with self.assertRaises(ValueError):
                await self.pool.reconcile_retained(self.run, evidence)
        evidence = {'runtime_handle': attempt.invocation.runtime_handle, 'result': result}
        self.owner.return_value = False
        with self.assertRaises(CredentialRouteChanged):
            await self.pool.reconcile_retained(self.run, evidence)
        self.callback.assert_not_awaited()
        self.assertIsNone(attempt.retained_fingerprint)
        self.owner.return_value = True
        self.assertTrue(await self.pool.reconcile_retained(self.run, evidence))
        await self.pool.retire()

    async def test_original_deadline_reports_before_launch_ack_without_releasing_capacity(self):
        self.context['timeout_seconds'] = 1
        ack, finish = self.provider()
        routing = asyncio.create_task(self.route())
        await self.started.wait()
        attempt = self.pool.attempt(self.run)
        original = attempt.info.started_at
        with patch('attempt_deadline.owns_remote_session', new_callable=AsyncMock, return_value=True):
            self.assertEqual(await attempt.deadline, {'expired': True})
            self.assertFalse(routing.done())
            self.assertFalse(attempt.settled.done())
            self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 1)
            self.assertEqual(self.callback.await_args.args[0]['error_type'], 'TimeoutError')
            self.assertEqual(attempt.info.started_at, original)
            with self.assertRaises(RuntimeError):
                await self.pool.retire()
            ack.set()
            await routing
            finish.set()
            await attempt.delivery
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 0)
        await self.pool.retire()

    async def test_transport_loss_before_ack_retains_native_capacity_and_never_relaunches(self):
        self.context['timeout_seconds'] = 1
        sends = []
        cleanup = AsyncMock()
        self.enterContext(patch.object(self.pool.native, '_get_redis',
            new=AsyncMock(return_value=SimpleNamespace(delete=cleanup))))

        def lose_before_ack(request):
            sends.append(json.loads(request.content))
            raise httpx.ReadError('Synthetic response lost before ACK')

        self.enterContext(patch('http_invocation.httpx.AsyncClient',
            side_effect=lambda **kw: CLIENT(transport=httpx.MockTransport(lose_before_ack), **kw)))
        with patch('attempt_deadline.owns_remote_session', new_callable=AsyncMock, return_value=True):
            try:
                await self.route()
            except httpx.ReadError:
                pass  # Expose the old definitive-failure path before the fix.
            attempt = self.pool.attempt(self.run)
            with self.assertRaises(httpx.ReadError):
                await attempt.receipt
            with self.assertRaises(httpx.ReadError):
                await attempt.delivery
            self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 1,
                             'uncertain started invocation lost native capacity')
            cleanup.assert_not_awaited()
            await self.route()
            self.assertEqual(len(sends), 1)
            self.assertEqual(await attempt.deadline, {'expired': True})
            self.assertEqual(self.callback.await_args.args[0]['error_type'], 'TimeoutError')
            self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 1)
            with self.assertRaises(RuntimeError):
                await self.pool.retire()

    async def test_explicit_deadline_recovery_allows_retained_ack_without_rewriting_failed_receipts(self):
        self.context['timeout_seconds'] = 1
        ack, finish = self.provider(lose=True)
        ack.set()
        self.callback.side_effect = ConnectionError('Synthetic deadline receipt lost')
        with patch('attempt_deadline.owns_remote_session', new_callable=AsyncMock, return_value=True):
            await self.route()
            attempt = self.pool.attempt(self.run)
            finish.set()
            with self.assertRaises(httpx.ReadError):
                await attempt.delivery
            with self.assertRaisesRegex(ConnectionError, 'deadline receipt lost'):
                await attempt.deadline
            self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 1)
            with self.assertRaises(ConnectionError):
                await self.pool.reconcile_deadline(self.run)
            self.callback.side_effect = None
            await self.pool.reconcile_deadline(self.run)
            evidence = {'runtime_handle': attempt.invocation.runtime_handle,
                        'result': {'execution_id': self.run, 'success': True}}
            error = None
            try:
                consumed = await self.pool.reconcile_retained(self.run, evidence)
            except ConnectionError as exc:
                error = type(exc).__name__
                consumed = False
            self.assertIsNone(error, 'recovered canonical deadline still blocks retained settlement')
            self.assertTrue(consumed)
            with self.assertRaisesRegex(ConnectionError, 'deadline receipt lost'):
                await attempt.deadline
            with self.assertRaises(httpx.ReadError):
                await attempt.receipt
            self.assertEqual(len(self.sent), 1)
            self.assertEqual(self.pool.native._build_heartbeat()['busy_count'], 0)
            await self.pool.retire()


if __name__ == '__main__':
    main()
