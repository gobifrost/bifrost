import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read = name => JSON.parse(readFileSync(new URL(name, import.meta.url), 'utf8'));
const api = read('matched-api-v1.json');
assert.equal(api.source, '1d33150eb376b71f8a81b427920eda1b0447f829');
assert.equal(api.revision, '20261101_table_name_unique');
assert.deepEqual(api.statuses, [200, 401]);
assert.equal(api.passed, true);
assert.equal(api.owned_running_after_cleanup, '');
assert.equal(api.cloud_invocations, 0);
for (const name of ['archive-qualified-v1.json', 'archive-qualified-v2.json']) {
  const archive = read(name);
  assert.equal(archive.archives.length, 17);
  assert.equal(archive.volumes_deleted, false);
  assert.equal(archive.guard_changed, false);
  for (const item of archive.archives) {
    assert.equal(item.original_sha256, '61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731');
    assert.match(item.sha256, /^[a-f0-9]{64}$/);
  }
}
console.log('Matched API/schema/auth and stopped owned project verified; guarded execution remains a separate gate.');
