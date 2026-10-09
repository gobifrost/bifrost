// Executed only from the trusted pull_request_target base checkout.
const LEDGER = "product-updates/dispositions.json";
// One file per disposition key, so concurrent PRs never edit the same file.
const ITEMS = "product-updates/dispositions";
const DEPENDENCY_FILES = new Set([
  "pyproject.toml", "requirements.lock", "requirements-piptools.lock",
  "client/package.json", "client/package-lock.json",
  "scripts/docs/package.json", "scripts/docs/package-lock.json",
  "api/src/services/app_compiler/package.json",
  "api/src/services/app_compiler/package-lock.json",
  "api/Dockerfile", "api/Dockerfile.dev", "client/Dockerfile", "client/Dockerfile.dev",
]);

function isActionPinUpdate(file) {
  if (typeof file.patch !== "string") return false;
  const removed = [], added = [];
  for (const line of file.patch.split("\n")) {
    if (!line.startsWith("+") && !line.startsWith("-")) continue;
    const match = line.slice(1).match(/^\s*(?:-\s*)?uses:\s*(\S+@)[a-f0-9]{40}(?:\s+#.*)?\s*$/);
    if (!match) return false;
    (line.startsWith("+") ? added : removed).push(match[1]);
  }
  return added.length > 0 && JSON.stringify(added) === JSON.stringify(removed);
}

async function readLedgerFile(github, repository, path, ref) {
  const { data: file } = await github.rest.repos.getContent({ ...repository, path, ref });
  if (file.type !== "file" || file.encoding !== "base64") throw new Error("Canonical disposition ledger is unavailable");
  return JSON.parse(Buffer.from(file.content, "base64").toString("utf8"));
}

async function recordDependabotDisposition({ github, repository, number, alertState }) {
  if (!["", "OPEN", "FIXED", "DISMISSED"].includes(alertState)) {
    throw new Error("Unknown Dependabot advisory state");
  }
  const key = `pr:${number}`;
  const itemPath = `${ITEMS}/${key.replace(":", "-")}.json`;
  const args = { ...repository, pull_number: number };
  const { data: pr } = await github.rest.pulls.get(args);
  if (pr.state !== "open" || pr.base.ref !== "main" ||
      pr.user.login !== "dependabot[bot]" || pr.user.type !== "Bot" ||
      pr.head.repo?.full_name !== pr.base.repo.full_name ||
      pr.base.repo.full_name !== `${repository.owner}/${repository.repo}` ||
      !pr.head.ref.startsWith("dependabot/")) {
    throw new Error("Disposition automation requires an open, same-repository Dependabot PR to main");
  }
  const files = await github.paginate(github.rest.pulls.listFiles, { ...args, per_page: 100 });
  if (!files.some(file => DEPENDENCY_FILES.has(file.filename) || /^\.github\/workflows\/[^/]+\.ya?ml$/.test(file.filename)) ||
      files.some(file => file.status === "renamed" ||
        (/^\.github\/workflows\/[^/]+\.ya?ml$/.test(file.filename) && !isActionPinUpdate(file)) ||
        (file.filename !== LEDGER && file.filename !== itemPath && !DEPENDENCY_FILES.has(file.filename) &&
          !/^\.github\/workflows\/[^/]+\.ya?ml$/.test(file.filename)))) {
    throw new Error("Dependabot PR changes files outside dependency maintenance");
  }
  const ledger = await readLedgerFile(github, repository, LEDGER, pr.head.sha);
  // Header items remain only until the data moves into per-item files.
  const headerItems = Object.hasOwn(ledger, "items") ? ledger.items : {};
  if (ledger.schema_version !== 1 || !headerItems || typeof headerItems !== "object" || Array.isArray(headerItems)) {
    throw new Error("Invalid canonical disposition ledger");
  }
  let existing;
  try {
    existing = await readLedgerFile(github, repository, itemPath, pr.head.sha);
  } catch (error) {
    // A missing file means no disposition has been recorded for this PR yet.
    if (error.status !== 404) throw error;
  }
  // Human decisions, including security notices, always take precedence.
  if (Object.hasOwn(headerItems, key)) return { changed: false };
  if (existing !== undefined) {
    if (existing.key !== key) throw new Error(`${itemPath} does not hold ${key}`);
    return { changed: false };
  }
  const item = {
    key,
    classification: "omit",
    entry_ids: [],
    review: {
      status: "approved",
      evidence: [pr.html_url, `https://github.com/${repository.owner}/${repository.repo}/blob/${pr.base.sha}/docs/product-updates-authoring.md`],
    },
    reason: "Dependabot dependency maintenance; omitted under the repository's customer-facing selection policy. CI and dependency security review remain required.",
    ...(alertState === "OPEN" ? { security_review: { status: "required", review_ref: pr.html_url } } : {}),
  };
  const { data: commit } = await github.rest.git.getCommit({ ...repository, commit_sha: pr.head.sha });
  const { data: blob } = await github.rest.git.createBlob({ ...repository, content: `${JSON.stringify(item, null, 2)}\n`, encoding: "utf-8" });
  const { data: tree } = await github.rest.git.createTree({ ...repository, base_tree: commit.tree.sha, tree: [{ path: itemPath, mode: "100644", type: "blob", sha: blob.sha }] });
  const { data: update } = await github.rest.git.createCommit({ ...repository, message: "chore: record dependency update disposition", tree: tree.sha, parents: [pr.head.sha] });
  // Never overwrite a concurrent rebase or a maintainer's branch update.
  await github.rest.git.updateRef({ ...repository, ref: `heads/${pr.head.ref}`, sha: update.sha, force: false });
  return { changed: true, sha: update.sha };
}
module.exports = { recordDependabotDisposition };
