// Shared capacity protocol engine (JavaScript).
//
// One implementation of the contract in docs/architecture/external-worker-capacity.md,
// alongside the Python core shipped by the contract change, so the two providers
// share semantics instead of drifting.
//
// Deliberately pure: no fetch, no Durable Objects, no Cloudflare APIs. The
// transport (capacity.mjs) and the durable store are injected, which is what
// makes the lifecycle testable without a deployment.

export const STATE = Object.freeze({
    PREPARED: "prepared",
    STARTING: "starting",
    RUNNING: "running",
    STOPPED: "stopped",
    DELETED: "deleted",
});

const LIVE = new Set([STATE.PREPARED, STATE.STARTING, STATE.RUNNING]);
const RESERVATION_FIELDS = [
    "worker_id",
    "worker_version",
    "memory_limit_bytes",
    "max_lifetime_seconds",
    "drain_grace_seconds",
];

export class ProtocolError extends Error {
    constructor(status, message) {
        super(message);
        this.status = status;
    }
}

export const newResourceUid = () =>
    [...crypto.getRandomValues(new Uint8Array(16))]
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");

/**
 * @param {object} options
 * @param {object} options.profile   advertised capabilities
 * @param {object} options.store     { read, write, countLive } — all async
 * @param {object} options.runtime   { start, destroy, observe } — all async
 * @param {object} [options.clock]   { now } injectable for tests
 */
export function createEngine({ profile, store, runtime, clock = { now: () => Date.now() } }) {
    const chains = new Map();

    // Serialize mutations per worker: Durable Objects give us one isolate, but
    // concurrent requests can still interleave across awaits.
    function serialize(workerId, fn) {
        const prior = chains.get(workerId) ?? Promise.resolve();
        const next = prior.then(fn, fn);
        chains.set(workerId, next.catch(() => {}));
        return next;
    }

    function resource(row) {
        return {
            scope: profile.scope,
            worker_id: row.worker_id,
            worker_version: row.reservation.worker_version,
            resource_uid: row.resource_uid,
            state: row.state,
            out_of_memory: Boolean(row.out_of_memory),
        };
    }

    function settle(row, observation) {
        let state = observation.state;
        if (state === STATE.RUNNING && row.state === STATE.STARTING) {
            const elapsed = clock.now() - (row.started_at ?? 0);
            if (elapsed < profile.starting_grace_ms) state = STATE.STARTING;
        }
        if ((state === STATE.STOPPED) && LIVE.has(row.state)) {
            row.stopped_at = row.stopped_at ?? clock.now();
        }
        row.state = state;
        row.out_of_memory = Boolean(observation.out_of_memory);
        row.intent = null;
        row.revision = (row.revision ?? 0) + 1;
        return row;
    }

    function validateReservation(workerId, reservation) {
        for (const field of RESERVATION_FIELDS) {
            if (reservation?.[field] === undefined) {
                throw new ProtocolError(400, `reservation misses ${field}`);
            }
        }
        if (reservation.worker_id !== workerId) {
            throw new ProtocolError(400, "reservation worker_id does not match the URL");
        }
        if (reservation.memory_limit_bytes !== profile.memory_limit_bytes) {
            throw new ProtocolError(409, "reservation memory does not match advertised capacity");
        }
        if (reservation.worker_version !== profile.worker_version) {
            throw new ProtocolError(409, "reservation build does not match advertised capacity");
        }
        if (Number(reservation.max_lifetime_seconds) > profile.max_lifetime_seconds) {
            throw new ProtocolError(409, "reservation lifetime exceeds advertised capacity");
        }
    }

    return {
        async get(workerId) {
            const row = await store.read(workerId);
            if (!row || row.scope !== profile.scope) return null;
            if (row.state === STATE.DELETED || row.state === STATE.PREPARED) return resource(row);

            let observation;
            try {
                observation = await runtime.observe(row);
            } catch {
                // Unreachable is not absent: keep the last known state.
                return resource(row);
            }
            if (observation.state !== row.state) {
                // Observation is authoritative. Persisting a terminal
                // transition here is what arms the no-restart fence.
                settle(row, observation);
                await store.write(workerId, row);
            }
            return resource(row);
        },

        prepare(workerId, reservation) {
            return serialize(workerId, async () => {
                validateReservation(workerId, reservation);
                const existing = await store.read(workerId);
                if (existing) {
                    if (existing.scope !== profile.scope) {
                        throw new ProtocolError(409, "worker belongs to another scope");
                    }
                    if (existing.state === STATE.DELETED) {
                        throw new ProtocolError(409, "worker incarnation already terminated");
                    }
                    if (JSON.stringify(existing.reservation) !== JSON.stringify(reservation)) {
                        throw new ProtocolError(409, "conflicting reservation for this worker");
                    }
                    return resource(existing);
                }
                if ((await store.countLive(profile.scope)) >= profile.max_workers) {
                    throw new ProtocolError(409, "capacity ceiling reached");
                }
                // Commit the reservation before any provider work so an
                // interrupted prepare converges on readback.
                const row = {
                    scope: profile.scope,
                    worker_id: workerId,
                    reservation,
                    resource_uid: newResourceUid(),
                    handle: {},
                    state: STATE.PREPARED,
                    revision: 1,
                    created_at: clock.now(),
                    started_at: null,
                    stopped_at: null,
                    intent: null,
                };
                await store.write(workerId, row);
                return resource(row);
            });
        },

        start(workerId, body) {
            return serialize(workerId, async () => {
                const row = await store.read(workerId);
                if (!row || row.scope !== profile.scope) throw new ProtocolError(404, "unknown worker");
                if (row.state === STATE.DELETED) {
                    throw new ProtocolError(409, "worker incarnation already terminated");
                }
                if (body?.resource_uid !== row.resource_uid) {
                    throw new ProtocolError(409, "resource UID does not match the prepared incarnation");
                }
                if (row.state === STATE.STARTING || row.state === STATE.RUNNING) {
                    return resource(row); // replay: never launch twice
                }
                if (row.state === STATE.STOPPED) {
                    throw new ProtocolError(409, "worker incarnation already stopped");
                }
                if (row.intent === "start") {
                    // A previous attempt may have launched a container whose
                    // response we never saw. Observe before acting.
                    let prior;
                    try {
                        prior = await runtime.observe(row);
                    } catch {
                        throw new ProtocolError(502, "start outcome unknown; provider unreachable");
                    }
                    if (prior.state === STATE.STARTING || prior.state === STATE.RUNNING) {
                        settle(row, prior);
                        await store.write(workerId, row);
                        return resource(row);
                    }
                    if (prior.state === STATE.STOPPED || prior.state === STATE.DELETED) {
                        settle(row, { ...prior, state: STATE.STOPPED });
                        await store.write(workerId, row);
                        throw new ProtocolError(409, "worker incarnation already stopped");
                    }
                }

                row.intent = "start";
                row.revision = (row.revision ?? 0) + 1;
                await store.write(workerId, row);

                const startedAt = clock.now();
                let observation;
                try {
                    observation = await runtime.start(row);
                } catch (error) {
                    // Keep the intent: reconciliation decides from readback.
                    throw new ProtocolError(502, `start outcome unknown: ${error.name}`);
                }
                row.started_at = startedAt;
                if (observation.state === STATE.RUNNING) {
                    observation = { ...observation, state: STATE.STARTING };
                }
                settle(row, observation);
                row.handle = observation.handle ?? row.handle;
                await store.write(workerId, row);
                return resource(row);
            });
        },

        remove(workerId, body) {
            return serialize(workerId, async () => {
                const row = await store.read(workerId);
                if (!row || row.scope !== profile.scope) throw new ProtocolError(404, "unknown worker");
                if (body?.resource_uid !== row.resource_uid) {
                    throw new ProtocolError(409, "resource UID does not match this worker");
                }
                if (row.state === STATE.DELETED) return resource(row);

                row.intent = "delete";
                await store.write(workerId, row);
                try {
                    await runtime.destroy(row);
                } catch (error) {
                    throw new ProtocolError(502, `delete outcome unknown: ${error.name}`);
                }
                row.state = STATE.DELETED;
                row.intent = null;
                row.revision = (row.revision ?? 0) + 1;
                await store.write(workerId, row);
                return resource(row);
            });
        },
    };
}
