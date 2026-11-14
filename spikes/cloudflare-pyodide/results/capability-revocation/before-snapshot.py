"""Companion control preparation; never an upstream physical-cancel ACK."""
import asyncio
import re

from src.core.database import get_db_context
from src.worker.remote.fencing import LeaseRef, fence_remote_owner, remote_owner_context
from src.worker.remote.protocol import CredentialRouteChanged


class AttemptCapabilityRevoker:
    def __init__(self, journal):
        self.journal = journal
        self._bindings = {}
        self._acknowledged = {}

    def bind(self, route_id, runtime_handle, close):
        route = self.journal.routes[route_id]
        if (route_id in self._bindings or not callable(close) or not isinstance(runtime_handle, str)
            or re.fullmatch(r'[A-Za-z0-9._:-]{1,255}', runtime_handle) is None):
            raise ValueError('One trusted physical capability binding required')
        self._bindings[route_id] = (route, runtime_handle, close)

    async def revoke(self, lease, route_id):
        route, handle, close = self._bindings[route_id]
        if self.journal.routes.get(route_id) is not route:
            raise ValueError('Original physical route no longer retained')
        async with route.delivery_lock:
            route.require_owner(lease)
            # Ownership rotation takes an exclusive worker-row lock. Hold the
            # upstream shared fence through callback completion and commit.
            # A bounded trusted callback is required; no network retries here.
            with remote_owner_context(LeaseRef(lease)):
                async with get_db_context() as db:
                    if not await fence_remote_owner(db):
                        raise CredentialRouteChanged('Stale capability revocation owner')
                    response = self._acknowledged.get(route_id)
                    if response is None:
                        async with asyncio.timeout(5):
                            response = await close()
                        if (not isinstance(response, dict)
                            or set(response) != {'execution_id', 'runtime_handle', 'capabilities_revoked', 'physical_stopped'}
                            or response['execution_id'] != route.execution_id or response['runtime_handle'] != handle
                            or response['capabilities_revoked'] is not True or response['physical_stopped'] is not False):
                            raise ValueError('Matching capability-only acknowledgement required')
                    if not await fence_remote_owner(db):
                        raise CredentialRouteChanged('Owner expired during capability revocation')
            # Commit failure or callback loss must not manufacture an ACK.
            self._acknowledged[route_id] = dict(response)
            # Do not acknowledge route.task, consume a result, settle a native
            # process, release memory reservations, or claim physical stop.
            return {**response, 'execution_settled': False}
