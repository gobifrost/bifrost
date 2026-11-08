// Explicit experimental overlay; never deployed or enabled by default.
import local from './canonical-local-entry.mjs';
import overlay from './engine-context-overlay.mjs';
export {CanonicalApi} from './canonical-local-entry.mjs';
export default {
  async fetch(request, env, ctx) {
    if (env.ISOLATED_TEST_STACK !== 'true') return new Response('Local test stack required', {status: 403});
    return local.fetch(request, {...env, LOADER: {load: code => env.LOADER.load({
      ...code, modules: {...code.modules, ...overlay.files},
    })}}, ctx);
  },
};
