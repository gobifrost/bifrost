const test = require('node:test');
const assert = require('node:assert/strict');
const { recordDependabotDisposition } = require('./dependabot_product_updates.cjs');
function fixture() {
  const pr = { state: 'open', user: { login: 'dependabot[bot]', type: 'Bot' }, html_url: 'https://github.com/gobifrost/bifrost/pull/920', base: { ref: 'main', sha: 'a'.repeat(40), repo: { full_name: 'gobifrost/bifrost' } }, head: { ref: 'dependabot/npm/source-map-js', sha: 'b'.repeat(40), repo: { full_name: 'gobifrost/bifrost' } } };
  const ledger = { schema_version: 1, inventory: {}, items: { 'pr:1': { classification: 'highlight' } } };
  const writes = [];
  const files = [{ filename: 'api/src/services/app_compiler/package-lock.json', status: 'modified' }];
  const github = { paginate: async () => files, rest: { pulls: { get: async () => ({ data: pr }), listFiles() {} }, repos: { getContent: async (args) => { assert.equal(args.ref, pr.head.sha); return { data: { type: 'file', encoding: 'base64', content: Buffer.from(JSON.stringify(ledger)).toString('base64') } }; } }, git: {
    getCommit: async () => ({ data: { tree: { sha: 'original-tree' } } }),
    createBlob: async args => { writes.push(args); return { data: { sha: 'blob' } }; },
    createTree: async args => { writes.push(args); return { data: { sha: 'tree' } }; },
    createCommit: async args => { writes.push(args); return { data: { sha: 'new-commit' } }; },
    updateRef: async args => { writes.push(args); },
  } } };
  const run = alertState => recordDependabotDisposition({ github, repository: { owner: 'gobifrost', repo: 'bifrost' }, number: 920, alertState });
  return { pr, ledger, writes, files, github, run };
}
test('persists routine omission without modifying other dispositions or files', async () => {
  const f = fixture();
  assert.deepEqual(await f.run(''), { changed: true, sha: 'new-commit' });
  const result = JSON.parse(f.writes[0].content);
  assert.deepEqual(result.items['pr:1'], f.ledger.items['pr:1']);
  assert.equal(result.items['pr:920'].classification, 'omit');
  assert.equal(result.items['pr:920'].security_review, undefined);
  assert.deepEqual(f.writes[1].tree.map(x => x.path), ['product-updates/dispositions.json']);
  assert.deepEqual(f.writes[2].parents, [f.pr.head.sha]);
  assert.equal(f.writes[3].force, false);
});
test('preserves required security review for advisory updates', async () => {
  const f = fixture(); await f.run('OPEN');
  const item = JSON.parse(f.writes[0].content).items['pr:920'];
  assert.deepEqual(item.security_review, { status: 'required', review_ref: f.pr.html_url });
});
test('never replaces a human disposition and is idempotent', async () => {
  const f = fixture(); f.ledger.items['pr:920'] = { classification: 'highlight', review: { status: 'draft' } };
  assert.deepEqual(await f.run('OPEN'), { changed: false }); assert.deepEqual(f.writes, []);
});
test('rejects human authors, forks, wrong branches, and unrelated changes before any write', async () => {
  for (const mutate of [f => f.pr.user.login = 'human', f => f.pr.user.type = 'User', f => f.pr.head.repo.full_name = 'other/fork', f => f.pr.head.ref = 'feature', f => f.pr.base.ref = 'release', f => f.files.push({ filename: 'api/src/main.py', status: 'modified' }), f => f.files[0].status = 'renamed', f => f.files.splice(0)]) {
    const f = fixture(); mutate(f); await assert.rejects(f.run('')); assert.deepEqual(f.writes, []);
  }
});
test('a concurrent branch update fails without force or retry', async () => {
  const f = fixture(); let count = 0;
  f.github.rest.git.updateRef = async args => { assert.equal(args.force, false); count++; throw new Error('not fast forward'); };
  await assert.rejects(f.run(''), /not fast forward/); assert.equal(count, 1);
});
test('unknown advisory metadata fails closed', async () => {
  const f = fixture(); await assert.rejects(f.run('unknown'), /Unknown/); assert.deepEqual(f.writes, []);
});

test('workflow changes must only update the same fully pinned action', async () => {
  const patch = `@@ -1 +1 @@\n-        uses: actions/checkout@${'a'.repeat(40)} # v1\n+        uses: actions/checkout@${'b'.repeat(40)} # v2`;
  const f = fixture(); f.files.splice(0, 1, { filename: '.github/workflows/ci.yml', status: 'modified', patch });
  await f.run(''); assert.equal(f.writes.length, 4);
  for (const bad of [undefined, '+        run: echo unsafe', patch.replace('actions/checkout@' + 'b'.repeat(40), 'other/action@' + 'b'.repeat(40)), patch.replace('b'.repeat(40), 'main')]) {
    const badFixture = fixture(); badFixture.files.splice(0, 1, { filename: '.github/workflows/ci.yml', status: 'modified', patch: bad });
    await assert.rejects(badFixture.run(''), /outside dependency maintenance/); assert.deepEqual(badFixture.writes, []);
  }
});

test('fixed and dismissed advisory metadata are recognized states', async () => {
  for (const state of ['FIXED', 'DISMISSED']) {
    const f = fixture(); await f.run(state);
    assert.equal(JSON.parse(f.writes[0].content).items['pr:920'].security_review, undefined);
  }
});
