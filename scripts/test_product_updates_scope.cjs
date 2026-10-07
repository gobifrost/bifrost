const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeMergeGroup } = require("./product_updates_scope.cjs");

const group = {
  base_sha: "5d4d5224f008e7e041f01300d8577f744776daee",
  head_sha: "299f38904e5e8913b949f6fa07bf293427ca3ac9",
  base_ref: "refs/heads/main",
};
const oid = (character) => character.repeat(40);
const entry = ({ base, head, number, login = "octo" }) => ({
  baseCommit: { oid: base },
  headCommit: { oid: head },
  pullRequest: { number, author: login == null ? null : { login } },
});

test("normalizes squash merge-queue metadata without comparing the PR branch head", async () => {
  const listQueueEntries = async (baseRef) => {
    assert.equal(baseRef, "main");
    return [entry({ base: group.base_sha, head: group.head_sha, number: 918, login: "jackmusick" })];
  };
  const result = await normalizeMergeGroup({
    mergeGroup: group,
    listQueueEntries,
  });

  assert.deepEqual(result.pull_requests, [{ number: 918, user: { login: "jackmusick" } }]);
});

test("normalizes chained queue entries and every batch entry with matching commit metadata", async () => {
  const intermediate = oid("c");
  const result = await normalizeMergeGroup({
    mergeGroup: group,
    listQueueEntries: async () => [
      entry({ base: intermediate, head: group.head_sha, number: 30, login: "zoe" }),
      entry({ base: intermediate, head: group.head_sha, number: 10, login: "ada" }),
      entry({ base: group.base_sha, head: intermediate, number: 20, login: "bert" }),
      entry({ base: oid("d"), head: oid("e"), number: 99, login: "ignored" }),
    ],
  });

  assert.deepEqual(result.pull_requests, [
    { number: 10, user: { login: "ada" } },
    { number: 20, user: { login: "bert" } },
    { number: 30, user: { login: "zoe" } },
  ]);
});

test("fails closed for missing, ambiguous, cyclic, or incomplete queue metadata", async (t) => {
  await t.test("missing link", async () => {
    await assert.rejects(
      normalizeMergeGroup({ mergeGroup: group, listQueueEntries: async () => [] }),
      /could not resolve queued pull requests/
    );
  });

  await t.test("ambiguous link", async () => {
    await assert.rejects(
      normalizeMergeGroup({
        mergeGroup: group,
        listQueueEntries: async () => [
          entry({ base: oid("c"), head: group.head_sha, number: 1 }),
          entry({ base: group.base_sha, head: group.head_sha, number: 2 }),
        ],
      }),
      /ambiguous/
    );
  });

  await t.test("cycle", async () => {
    const intermediate = oid("c");
    await assert.rejects(
      normalizeMergeGroup({
        mergeGroup: group,
        listQueueEntries: async () => [
          entry({ base: intermediate, head: group.head_sha, number: 1 }),
          entry({ base: group.head_sha, head: intermediate, number: 2 }),
        ],
      }),
      /cycle/
    );
  });

  await t.test("missing author", async () => {
    await assert.rejects(
      normalizeMergeGroup({
        mergeGroup: group,
        listQueueEntries: async () => [entry({ base: group.base_sha, head: group.head_sha, number: 1, login: null })],
      }),
      /author login/
    );
  });

  await t.test("incomplete event", async () => {
    await assert.rejects(
      normalizeMergeGroup({ mergeGroup: { head_sha: group.head_sha }, listQueueEntries: async () => [] }),
      /base_sha/
    );
  });
});
