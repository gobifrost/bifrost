import {WorkerEntrypoint} from 'cloudflare:workers';
import {handleSignalProbe, writeSignalLog} from './policy.mjs';
export class Effects extends WorkerEntrypoint {
  async record(event) {
    if (!['before', 'caught', 'after'].includes(event)) throw new TypeError('Child event only');
    const status = await writeSignalLog(this.env.DEV_API, this.ctx.props.token, this.ctx.props.caseId, event);
    if (status !== 200) throw new Error('Independent observer rejected event');
  }
}
export default {fetch(request, env, ctx) {
  return handleSignalProbe(request, env, props => ctx.exports.Effects({props}), promise => ctx.waitUntil(promise));
}};
