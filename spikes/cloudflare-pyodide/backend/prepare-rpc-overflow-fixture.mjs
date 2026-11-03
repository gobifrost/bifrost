// Separate authored 5 MB result case; preserve shared test source.
import {readFileSync, writeFileSync} from 'node:fs';
if (process.argv.length !== 4) throw new Error('Usage: node prepare-rpc-overflow-fixture.mjs <shared-fixture> <new-output>');
let source = readFileSync(process.argv[2], 'utf8').replace(/\r\n/g, '\n');
function replace(before, after) {
  if (source.split(before).length !== 2) throw new Error('Oversized fixture source changed');
  source = source.replace(before, after);
}
replace('1200000 if oversized_body else 300000', '5000000 if oversized_body else 300000');
replace("('ExecutorBodyTooLargeError' if oversized_body", "('ExecutorRpcResultTooLargeError' if oversized_body");
replace(`                            if (outcome.get('response_eof') is not True
                                or outcome.get('engine_outcome_delivered') is not False
                                or not 1048576 < outcome.get('response_bytes', 0) <= 4194304):
                                raise RuntimeError('Oversized body EOF evidence invalid')
                            report['oversized_body_failure_verified'] = True`,
`                            if (outcome.get('execute_returned') is not True
                                or outcome.get('engine_outcome_delivered') is not False
                                or outcome.get('kind') != 'rpc_result_too_large'
                                or outcome.get('rpc_wire_limit_bytes') != 4194304):
                                raise RuntimeError('RPC wire failure evidence invalid')
                            report['rpc_wire_budget_failure_verified'] = True`);
writeFileSync(process.argv[3], source, {flag: 'wx'});
