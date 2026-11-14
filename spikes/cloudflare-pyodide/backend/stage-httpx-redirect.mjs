// Build the fixed Python fixture module; does not install or invoke a Worker.
import {readFileSync, writeFileSync} from 'node:fs';
if (process.argv.length !== 3) throw new Error('Usage: node stage-httpx-redirect.mjs <new-output-file>');
const source = readFileSync(new URL('httpx_redirect_patch.py', import.meta.url), 'utf8');
writeFileSync(process.argv[2], `export default ${JSON.stringify(source)};`, {flag: 'wx'});
