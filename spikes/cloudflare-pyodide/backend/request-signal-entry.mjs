import {WorkerEntrypoint} from 'cloudflare:workers';
import {forwardSignalProbe, postSignalEvent} from './request-signal-probe.mjs';
export class Effects extends WorkerEntrypoint {
  async record(event) {
    if (!['before', 'caught', 'after'].includes(event)) throw new TypeError('Invalid child event');
    await postSignalEvent(this.ctx.props.id, event);
  }
}
export default {
  fetch(request, env, ctx) {
    if (env.LOCAL_PROBE !== 'true' || request.method !== 'POST') return new Response('Local POST only', {status: 403});
    return forwardSignalProbe(request, env.LOADER, id => ctx.exports.Effects({props: {id}}),
      id => postSignalEvent(id, 'parent_abort_received'), promise => ctx.waitUntil(promise));
  },
};
