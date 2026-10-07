"use strict";

function normalizedBaseRef(baseRef) {
  return String(baseRef || "").replace(/^refs\/heads\//, "");
}

/**
 * Resolve every open pull request whose head is an ancestor of the exact
 * merge-group candidate. The caller supplies paginated GitHub API adapters so
 * this code is testable without an Actions runtime.
 */
async function normalizeMergeGroup({ mergeGroup, listOpenPulls, compare }) {
  const base = mergeGroup && mergeGroup.base_sha;
  const head = mergeGroup && mergeGroup.head_sha;
  const baseRef = normalizedBaseRef(mergeGroup && mergeGroup.base_ref);
  if (!base || !head || !baseRef) {
    throw new Error("merge_group payload lacks base_sha, head_sha, or base_ref");
  }

  const pulls = [];
  for (const pull of await listOpenPulls(baseRef)) {
    if (!Number.isInteger(pull.number) || !pull.head || typeof pull.head.sha !== "string") continue;
    const ancestry = await compare({ base: pull.head.sha, head });
    if (ancestry.status === "ahead" || ancestry.status === "identical") {
      const login = pull.user && pull.user.login;
      if (typeof login !== "string" || !login) {
        throw new Error(`queued pull request #${pull.number} lacks an author login`);
      }
      pulls.push({ number: pull.number, user: { login } });
    }
  }
  if (pulls.length === 0) {
    throw new Error("could not resolve every queued pull request for merge_group");
  }
  return {
    ...mergeGroup,
    pull_requests: [...new Map(pulls.map((pull) => [pull.number, pull])).values()].sort((a, b) => a.number - b.number),
  };
}

module.exports = { normalizeMergeGroup };
