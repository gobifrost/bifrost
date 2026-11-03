"""Attempt-scoped native workflow requests; no dispatch, credentials or retries."""
import asyncio
import json


class WorkflowRequestMux:
    """Bind trusted collectors before native dispatch; retain uncertainty per run.

    Upstream owns claims, fencing and terminal ACKs. The collector receives an
    immutable native request snapshot and an accepted-handle callback. Caller
    cancellation never cancels already-dispatched physical collection.
    """
    def __init__(self, generation):
        self._generation = generation
        self._slots = {}

    def bind(self, execution_id, collector):
        if execution_id in self._slots:
            raise RuntimeError('Execution collector already bound')
        self._slots[execution_id] = {
            'collector': collector, 'payload': None, 'accepted': None,
            'collection': None, 'result': None, 'generation': None,
        }

    def collection(self, execution_id):
        return self._slots[execution_id]['collection']

    def result(self, execution_id):
        return self._slots[execution_id]['result']

    async def request(self, action, payload, *, expected_generation=None):
        # Fence before looking up a retained dispatch, including duplicate reads.
        admitted_generation = self._generation()
        if action != 'workflow' or expected_generation != admitted_generation:
            raise ConnectionError('Workflow transport/session rejected')
        snapshot = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)
        frozen = json.loads(snapshot)
        execution_id = frozen.get('execution_id')
        slot = self._slots.get(execution_id)
        if slot is None or not isinstance(frozen.get('route_id'), str) or not frozen['route_id']:
            raise ConnectionError('Unbound workflow request')
        if slot['payload'] is not None and slot['generation'] != admitted_generation:
            raise ConnectionError('Retained workflow belongs to a different session')
        if slot['payload'] is not None and slot['payload'] != snapshot:
            raise ConnectionError('Retained workflow request changed; reconciliation required')
        if slot['collection'] is None:
            accepted = asyncio.get_running_loop().create_future()
            slot['payload'] = snapshot
            slot['generation'] = admitted_generation
            slot['accepted'] = accepted

            def on_accepted(handle):
                if accepted.done():
                    raise ConnectionError('Duplicate acceptance; reconciliation required')
                accepted.set_result(handle)

            async def collect():
                if self._generation() != admitted_generation:
                    raise ConnectionError('Workflow owner changed before collection')
                slot['result'] = await slot['collector'](frozen, on_accepted)
                return slot['result']

            slot['collection'] = asyncio.create_task(collect())
        accepted, collection = slot['accepted'], slot['collection']
        await asyncio.wait((accepted, collection), return_when=asyncio.FIRST_COMPLETED)
        if self._generation() != admitted_generation:
            raise ConnectionError('Workflow owner changed while awaiting acceptance')
        if not accepted.done():
            await asyncio.shield(collection)
            raise ConnectionError('Workflow completed without acceptance')
        return {'runtime_handle': accepted.result()}
