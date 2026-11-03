"""Source regressions; real PostgreSQL/Pyodide qualification is separate."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from attempt_journal import AttemptJournal
from capability_revocation import AttemptCapabilityRevoker
from src.worker.remote.protocol import CredentialRouteChanged


class RevocationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lease = SimpleNamespace(worker_id=uuid4(), launcher_id=uuid4(), session_generation=1)
        self.enterContext(patch('attempt_journal.owns_remote_session', new_callable=AsyncMock, return_value=True))
        self.fence = self.enterContext(patch('capability_revocation.fence_remote_owner', new_callable=AsyncMock, return_value=True))

        @asynccontextmanager
        async def transaction():
            yield object()

        self.enterContext(patch('capability_revocation.get_db_context', transaction))
        self.finished = asyncio.Event()

        async def execution():
            await self.finished.wait()
            return {'execution_id': 'execution', 'success': True}

        self.journal = AttemptJournal()
        self.receipt = await self.journal.dispatch(self.lease, 'route', 'execution', execution)
        self.revoker = AttemptCapabilityRevoker(self.journal)
        self.close = AsyncMock(return_value={'execution_id': 'execution', 'runtime_handle': 'handle',
                                            'capabilities_revoked': True, 'physical_stopped': False})
        self.revoker.bind('route', 'handle', self.close)

    async def asyncTearDown(self):
        self.finished.set()
        await self.receipt

    async def test_revocation_is_idempotent_without_terminal_ack_or_collection_cancellation(self):
        for _ in range(2):
            result = await self.revoker.revoke(self.lease, 'route')
            self.assertFalse(result['physical_stopped'])
            self.assertFalse(result['execution_settled'])
        self.close.assert_awaited_once()
        self.assertFalse(self.journal.routes['route'].acknowledged)
        self.assertFalse(self.journal.routes['route'].task.done())
        with self.assertRaises(RuntimeError):
            await self.journal.retire()

    async def test_stale_and_foreign_owner_never_reach_capability(self):
        self.fence.return_value = False
        with self.assertRaises(CredentialRouteChanged):
            await self.revoker.revoke(self.lease, 'route')
        foreign = SimpleNamespace(worker_id=uuid4(), launcher_id=uuid4())
        with self.assertRaises(ValueError):
            await self.revoker.revoke(foreign, 'route')
        self.close.assert_not_awaited()

    async def test_lost_control_ack_is_uncertain_and_not_retried(self):
        original = ConnectionError('Synthetic lost control acknowledgement')
        self.close.side_effect = original
        with self.assertRaises(ConnectionError) as raised:
            await self.revoker.revoke(self.lease, 'route')
        self.assertIs(raised.exception, original)
        self.close.assert_awaited_once()
        self.assertEqual(self.revoker._acknowledged, {})
        self.assertFalse(self.journal.routes['route'].acknowledged)

    async def test_owner_expiry_after_effect_retains_uncertain_ack_and_route(self):
        self.fence.side_effect = [True, False]
        with self.assertRaises(CredentialRouteChanged):
            await self.revoker.revoke(self.lease, 'route')
        self.close.assert_awaited_once()
        self.assertEqual(self.revoker._acknowledged, {})
        self.assertFalse(self.journal.routes['route'].acknowledged)

    async def test_ack_snapshot_cannot_mutate_during_post_fence(self):
        calls = 0

        async def fence(_db):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.close.return_value['physical_stopped'] = True
            return True

        self.fence.side_effect = fence
        response = await self.revoker.revoke(self.lease, 'route')
        self.assertIs(response['physical_stopped'], False)
        self.assertIs(self.revoker._acknowledged['route']['physical_stopped'], False)

    async def test_stronger_or_mismatched_provider_ack_cannot_settle_execution(self):
        original = dict(self.close.return_value)
        for change in [{'physical_stopped': True}, {'execution_id': 'other'}, {'runtime_handle': 'other'}]:
            self.close.return_value = {**original, **change}
            with self.assertRaises(ValueError):
                await self.revoker.revoke(self.lease, 'route')
        self.assertEqual(self.revoker._acknowledged, {})
        self.assertFalse(self.journal.routes['route'].task.done())
