"use strict";

function queueEntryMetadata(entry) {
  const head = entry && entry.headCommit && entry.headCommit.oid;
  const base = entry && entry.baseCommit && entry.baseCommit.oid;
  return { head, base };
}

/**
 * Resolve the exact merge-queue chain using GitHub's GraphQL MergeQueueEntry
 * commit metadata. Merge-queue candidates can be squash commits, so PR branch
 * ancestry is neither available nor authoritative here.
 */
async function normalizeMergeGroup({ mergeGroup, listQueueEntries }) {
  const base = mergeGroup && mergeGroup.base_sha;
  const head = mergeGroup && mergeGroup.head_sha;
  if (typeof base !== "string" || !base || typeof head !== "string" || !head) {
    throw new Error("merge_group payload lacks base_sha or head_sha");
  }

  const baseRef = String(mergeGroup.base_ref || "").replace(/^refs\/heads\//, "");
  if (!baseRef || head === base) {
    throw new Error("merge_group payload lacks a base_ref or candidate changes");
  }
  const entries = await listQueueEntries(baseRef);
  const pullRequests = new Map();
  const visited = new Set();
  let candidateHead = head;

  while (candidateHead !== base) {
    if (visited.has(candidateHead)) {
      throw new Error("merge queue metadata contains a cycle");
    }
    visited.add(candidateHead);

    const matchingEntries = entries.filter((entry) => queueEntryMetadata(entry).head === candidateHead);
    if (matchingEntries.length === 0) {
      throw new Error("could not resolve queued pull requests for merge_group");
    }

    const bases = new Set();
    for (const entry of matchingEntries) {
      const { base: entryBase } = queueEntryMetadata(entry);
      if (typeof entryBase !== "string" || !entryBase) {
        throw new Error("queued pull request lacks base commit metadata");
      }
      bases.add(entryBase);
    }
    if (bases.size !== 1) {
      throw new Error("merge queue metadata has ambiguous candidate links");
    }

    for (const entry of matchingEntries) {
      const number = entry && entry.pullRequest && entry.pullRequest.number;
      const login = entry && entry.pullRequest && entry.pullRequest.author && entry.pullRequest.author.login;
      if (!Number.isInteger(number)) {
        throw new Error("queued pull request lacks a number");
      }
      if (typeof login !== "string" || !login) {
        throw new Error(`queued pull request #${number} lacks an author login`);
      }
      const previousLogin = pullRequests.get(number);
      if (previousLogin && previousLogin !== login) {
        throw new Error(`queued pull request #${number} has ambiguous author metadata`);
      }
      pullRequests.set(number, login);
    }

    candidateHead = bases.values().next().value;
  }

  return {
    ...mergeGroup,
    pull_requests: [...pullRequests.entries()]
      .map(([number, login]) => ({ number, user: { login } }))
      .sort((left, right) => left.number - right.number),
  };
}

module.exports = { normalizeMergeGroup };
