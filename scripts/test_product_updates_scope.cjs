const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeMergeGroup } = require("./product_updates_scope.cjs");

const group = { base_sha: "a".repeat(40), head_sha: "b".repeat(40), base_ref: "refs/heads/main" };

test("normalizes every open PR head reachable from a merge candidate", async () => {
  const calls = [];
  const result = await normalizeMergeGroup({
    mergeGroup: group,
    listOpenPulls: async (base) => {
      assert.equal(base, "main");
      return [
        { number: 42, user: { login: "octo" }, head: { sha: "c".repeat(40) } },
        { number: 43, user: { login: "hubot" }, head: { sha: "d".repeat(40) } },
      ];
    },
    compare: async ({ base, head }) => {
      calls.push({ base, head });
      return { status: base === "c".repeat(40) ? "ahead" : "behind" };
    },
  });

  assert.deepEqual(result.pull_requests, [{ number: 42, user: { login: "octo" } }]);
  assert.equal(calls.length, 2);
});

test("rejects a merge group whose queued PRs cannot be resolved", async () => {
  await assert.rejects(
    normalizeMergeGroup({
      mergeGroup: group,
      listOpenPulls: async () => [{ number: 42, user: { login: "octo" }, head: { sha: "c".repeat(40) } }],
      compare: async () => ({ status: "behind" }),
    }),
    /could not resolve every queued pull request/
  );
});

test("uses a normalized base ref and rejects incomplete group metadata", async () => {
  await assert.rejects(
    normalizeMergeGroup({
      mergeGroup: { head_sha: "b".repeat(40), base_ref: "main" },
      listOpenPulls: async () => [],
      compare: async () => ({ status: "ahead" }),
    }),
    /base_sha/
  );
});
