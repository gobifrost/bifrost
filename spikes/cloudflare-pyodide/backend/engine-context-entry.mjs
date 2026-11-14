// Local-only fresh-child comparison. No API capability or general egress.
import baseline from './baseline.mjs';
import overlay from './overlay.mjs';
import source from './probe-source.mjs';
export default {
  async fetch(request, env) {
    if (request.method !== 'POST') return new Response('POST only', {status: 405});
    const {mode} = await request.json();
    if (!['baseline', 'engine-only', 'engine-context'].includes(mode)) return new Response('Denied', {status: 422});
    const selected = {...baseline};
    if (mode !== 'baseline') selected['src/services/execution/engine.py'] = overlay.files['src/services/execution/engine.py'];
    if (mode === 'engine-context') Object.assign(selected, overlay.files);
    const modules = {};
    for (const [name, value] of Object.entries(selected)) modules[name] = value.base64 === undefined ? value : {
      data: Uint8Array.from(atob(value.base64), c => c.charCodeAt(0)).buffer,
    };
    modules['main.py'] = {py: source};
    return env.LOADER.load({compatibilityDate: '2026-10-30', compatibilityFlags: ['python_workers'],
      mainModule: 'main.py', modules, env: {}, globalOutbound: null,
      limits: {cpuMs: 1000, subRequests: 0}}).getEntrypoint().fetch(
        new Request('https://probe.invalid', {method: 'POST', body: JSON.stringify({mode})}));
  },
};
