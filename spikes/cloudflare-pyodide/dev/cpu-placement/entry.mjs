// Temporary guarded synthetic probe, never workflow dispatch admission.
import {WorkerEntrypoint} from 'cloudflare:workers';
import {recordCpuEffect} from '../../backend/cpu-placement.mjs';
import {handleCpuProbe} from './policy.mjs';

export class Effects extends WorkerEntrypoint {
  async record(event) {
    await recordCpuEffect(this.env.DEV_API, this.ctx.props.token, this.ctx.props.caseId, event);
  }
}
export default {
  fetch(request, env, ctx) {
    return handleCpuProbe(request, env, input => ctx.exports.Effects({props: input}));
  },
};
