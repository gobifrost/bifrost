// Guarded experimental deployment of the locally qualified source overlay.
import candidate from './canonical-entry.mjs';
import overlay from './engine-context-overlay.mjs';
export {CanonicalApi} from './canonical-entry.mjs';
export default {
  async fetch(request, env, ctx) {
    return candidate.fetch(request, {...env, LOADER: {load: code => env.LOADER.load({
      ...code, modules: {...code.modules, ...overlay.files},
    })}}, ctx);
  },
};
