// Separate synthetic suppression case; never changes shared fixture/upstream code.
import {readFileSync, writeFileSync} from 'node:fs';
if (process.argv.length !== 4 && !(process.argv.length === 5 && ['--unsuppressed', '--post-revocation-log'].includes(process.argv[4]))) {
  throw new Error('Usage: node prepare-engine-cancel-fixture.mjs <shared-fixture> <new-output> [--unsuppressed|--post-revocation-log]');
}
let source = readFileSync(process.argv[2], 'utf8').replace(/\r\n/g, '\n');
function replace(before, after) {
  if (source.split(before).length !== 2) throw new Error('Shared cancellation fixture shape changed');
  source = source.replace(before, after);
}
replace(`                            "        held = await vendor.post('https://vendor.example/hold', content='fails-first', headers={'Authorization': 'Bearer synthetic-vendor'})\\n"`,
`                            "        try:\\n"
                            "            await vendor.post('https://vendor.example/hold', content='fails-first', headers={'Authorization': 'Bearer synthetic-vendor'})\\n"
                            "        except asyncio.CancelledError:\\n"
                            "            logging.getLogger(__name__).warning('engine_cooperative_cancel_suppressed')\\n"
                            "        else:\\n"
                            "            raise RuntimeError('Actual engine cancellation was not observed')\\n"`);
replace('if held.status_code != 200 or late_https.status_code != 410:', 'if late_https.status_code != 410:');
replace(`                    if https_cancel:
                        failure_consume_gate.set()`, `                    if https_cancel:
                        # Deliberate fixture consumer hold, not automatic provider settlement.`);
replace(`                            response.raise_for_status()
                            return response.json()`, `                            response.raise_for_status()
                            cancel_response = await api.post('http://bifrost-pyodide-ddf316c1:8799/__fixture_cancel',
                                json={'execution_id': run, 'runtime_handle': first.invocation.runtime_handle})
                            cancel_response.raise_for_status()
                            cancellation = cancel_response.json()
                            if (not cancellation['cancellation_requested'] or cancellation['physical_stopped']
                                or cancellation['execution_settled']):
                                raise RuntimeError('Invalid cooperative control acknowledgement')
                            report['engine_cooperative_control'] = cancellation
                            return response.json()`);
replace("if mux.collection(run).done() or native._build_heartbeat()['busy_count'] != 1:",
  "if native._build_heartbeat()['busy_count'] != 1:");
replace(`                        report['native_busy_counts'].append(1)
                        released = await api.post('http://bifrost-pyodide-ddf316c1:8820/release')`,
`                        report['native_busy_counts'].append(1)
                        async with asyncio.timeout(10):
                            early_result = await asyncio.shield(first.receipt)
                        if not early_result['success']:
                            raise RuntimeError('Suppressed engine cancellation did not preserve success')
                        report['engine_result_before_vendor_release'] = True
                        report['consumer_ack_deliberately_held'] = True
                        released = await api.post('http://bifrost-pyodide-ddf316c1:8820/release')`);
replace(`                        released.raise_for_status()
                        result = await first.receipt`, `                        released.raise_for_status()
                        failure_consume_gate.set()
                        result = await first.receipt`);
if (process.argv[4] === '--unsuppressed') {
  replace(`                            "            logging.getLogger(__name__).warning('engine_cooperative_cancel_suppressed')\\n"`,
    `                            "            raise\\n"`);
  replace(`                        async with asyncio.timeout(10):
                            early_result = await asyncio.shield(first.receipt)
                        if not early_result['success']:
                            raise RuntimeError('Suppressed engine cancellation did not preserve success')
                        report['engine_result_before_vendor_release'] = True
                        report['consumer_ack_deliberately_held'] = True`,
`                        try:
                            async with asyncio.timeout(10):
                                await asyncio.shield(first.receipt)
                        except TimeoutError:
                            raise RuntimeError('Unsuppressed collection did not settle within bound')
                        except Exception as exc:
                            collection_error_type = type(exc).__name__
                        else:
                            raise RuntimeError('Unsuppressed cancellation fabricated an engine result')
                        if native._build_heartbeat()['busy_count'] != 1:
                            raise RuntimeError('Failed collection released native capacity')
                        # No engine result exists; permit the real deadline callback.
                        failure_consume_gate.set()
                        async with asyncio.timeout(35):
                            while True:
                                async with get_db_context() as db:
                                    current = await db.get(Execution, failed['execution'].id)
                                    if current.status == ExecutionStatus.TIMEOUT:
                                        break
                                await asyncio.sleep(0.1)
                        after_timeout = await api.post('/api/sdk/config/get',
                            headers={'Authorization': 'Bearer ' + failed['token']}, json={'key': failed['key']})
                        if after_timeout.status_code != 401 or native._build_heartbeat()['busy_count'] != 1:
                            raise RuntimeError('Timeout did not revoke identity while retaining uncertain capacity')
                        await first.deadline
                        try:
                            await adapter.retire()
                        except RuntimeError as exc:
                            if str(exc) != 'unacknowledged physical routes remain':
                                raise
                        else:
                            raise RuntimeError('Uncertain physical route was silently retired')
                        released = await api.post('http://bifrost-pyodide-ddf316c1:8820/release')
                        released.raise_for_status()
                        print(json.dumps({'unsuppressed_cancellation_diagnostic': True,
                            'collection_error_type': collection_error_type,
                            'canonical_status': 'Timeout', 'access_status': after_timeout.status_code,
                            'native_busy_count': 1, 'physical_stopped': False,
                            'retirement_refused': True,
                            'vendor_after_release': (await api.get('http://bifrost-pyodide-ddf316c1:8820/snapshot')).json()}))
                        raise RuntimeError('Unsuppressed cancellation retains uncertain native obligation; retirement not qualified')`);
}
if (process.argv[4] === '--post-revocation-log') {
  replace("engine_cooperative_cancel_suppressed')", "engine_cooperative_cancel_suppressed credential=' + os.environ['BIFROST_ACCESS_TOKEN'])");
  replace("                        report['engine_result_before_vendor_release'] = True", `                        marker = 'engine_cooperative_cancel_suppressed credential=[REDACTED]'
                        if not any(row['message'] == marker for row in early_result['logs']):
                            raise RuntimeError('Post-revocation captured log redaction missing')
                        log_delivery = first.evidence.get('log_delivery')
                        if log_delivery != {'kind': 'runtime_log_delivery_unconfirmed', 'unsuccessful_log_requests': 2,
                                            'captured_log_entries': 4, 'persistence_verified': False, 'automatic_replay_safe': False}:
                            raise RuntimeError('Missing bounded post-revocation log delivery evidence')
                        report['post_revocation_log_redaction_verified'] = True
                        report['post_revocation_log_delivery'] = log_delivery
                        report['engine_result_before_vendor_release'] = True`);
}
writeFileSync(process.argv[3], source, {flag: 'wx'});
