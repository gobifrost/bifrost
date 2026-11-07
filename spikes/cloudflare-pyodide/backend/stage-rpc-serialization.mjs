import {readFileSync, writeFileSync} from 'node:fs';
if (process.argv.length !== 3) throw new Error('Usage: node stage-rpc-serialization.mjs <new-output-file>');
writeFileSync(process.argv[2], `export default ${JSON.stringify(readFileSync(new URL('rpc_serialization.py', import.meta.url), 'utf8'))};`, {flag: 'wx'});
