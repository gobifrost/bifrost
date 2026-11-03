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
        self._retired = False

    def bind(self, execution_id, collector):
        if self._retired:
            raise RuntimeError('Workflow collector transport retired')
        if execution_id in self._slots:
            raise RuntimeError('Execution collector already bound')
        self._slots[execution_id] = {
            'collector': collector, 'payload': None, 'accepted': None,
            'collection': None, 'result': None, 'generation': None,
            'retained_acknowledged': False,
        }

    def collection(self, execution_id):
        return self._slots[execution_id]['collection']

    def result(self, execution_id):
        return self._slots[execution_id]['result']

    def acknowledge_retained(self, execution_id, result):
        """Trusted parent calls only after canonical retained-result ACK."""
        slot = self._slots[execution_id]
        task = slot['collection']
        if (task is None or not task.done()
            or (not task.cancelled() and task.exception() is None)
            or not isinstance(result, dict) or result.get('execution_id') != execution_id
            or not isinstance(result.get('success'), bool)):
            raise ValueError('Failed collection and canonical retained result required')
        slot['result'] = result
        slot['retained_acknowledged'] = True

    @property
    def retained_count(self):
        return len(self._slots)

    def retire(self):
        """Call after acknowledged transport-journal retirement; keep uncertainty.

        Completed collection alone is not canonical ACK. This component has no
        terminal authority. The caller first retires the companion transport
        journal after acknowledgements through the actual upstream consumer.
        """
        for slot in self._slots.values():
            task = slot['collection']
            if (task is None or not task.done()
                or ((task.cancelled() or task.exception() is not None) and not slot['retained_acknowledged'])):
                raise RuntimeError('Unsettled workflow collection remains')
        self._slots.clear()
        self._retired = True

    async def request(self, action, payload, *, expected_generation=None):
        # Fence before looking up a retained dispatch, including duplicate reads.
        if self._retired:
            raise ConnectionError('Workflow collector transport retired')
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
