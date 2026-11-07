// Offline unit evidence only, not physical runtime or signed-dispatch acceptance.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
const bytes=readFileSync(new URL('../results/remote-ack-boundary/receipt.json',import.meta.url));
assert.equal(createHash('sha256').update(bytes).digest('hex'),'ef7f433c93e0d979b5081d0837c29e90bdecec2296737f1f8935d2c984f2d613');
const report=JSON.parse(bytes);
assert.equal(report.source,'45bcb38dd6412d64aa1d308479178904cd2ceab6');
assert.equal(report.qualification_passed,true);
assert.deepEqual(report.junit,{cases:5,failures:0,errors:0,skipped:0});
assert.equal(report.physical_invocations,0);
assert.equal(report.cloud_invocations,0);
assert.deepEqual(report.running_after_cleanup,[]);
assert.ok(report.commands.every(c=>c.exit_code===0));
console.log('Five remote ACK/callback unit cases and Ruff receipt verified; physical and signed-control adoption not implied.');
