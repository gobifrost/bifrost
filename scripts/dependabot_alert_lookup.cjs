const path = require("node:path");

const ECOSYSTEMS = new Map([
  ["npm_and_yarn", "npm"],
  ["pip", "pip"],
]);

function normalizePath(value) {
  if (typeof value !== "string" || value.length === 0) return null;
  const normalized = value.replace(/^\/+/, "").replace(/\/+$/, "");
  return normalized || ".";
}

function alertMatchesDependency(alert, dependency, changedManifests) {
  if (alert?.state !== "open") return false;
  const manifestPath = normalizePath(alert.dependency?.manifest_path);
  const directory = normalizePath(dependency?.directory);
  const isExactTarget = manifestPath && directory &&
    alert.dependency?.package?.name === dependency?.dependencyName &&
    path.posix.dirname(manifestPath) === directory &&
    changedManifests.has(manifestPath);
  if (!isExactTarget) return false;

  const expectedEcosystem = ECOSYSTEMS.get(dependency.packageEcosystem);
  return expectedEcosystem !== undefined && expectedEcosystem === alert.dependency?.package?.ecosystem;
}

async function findOpenDependabotAlert({ github, repository, pullNumber, dependencies }) {
  if (!github?.paginate || !github.rest?.pulls?.listFiles || !github.rest?.dependabot?.listAlertsForRepo) {
    throw new TypeError("Dependabot alert lookup requires GitHub pull-request and alert clients");
  }
  if (!repository?.owner || !repository?.repo || !Number.isInteger(pullNumber) || pullNumber < 1 || !Array.isArray(dependencies)) {
    throw new TypeError("Dependabot alert lookup received invalid pull-request metadata");
  }

  const [files, alerts] = await Promise.all([
    github.paginate(github.rest.pulls.listFiles, { ...repository, pull_number: pullNumber, per_page: 100 }),
    github.paginate(github.rest.dependabot.listAlertsForRepo, { ...repository, state: "open", per_page: 100 }),
  ]);
  const changedManifests = new Set(files
    .filter(file => file?.status !== "removed" && file?.status !== "renamed")
    .map(file => normalizePath(file?.filename))
    .filter(Boolean));

  return alerts.some(alert => dependencies.some(dependency => alertMatchesDependency(alert, dependency, changedManifests)))
    ? "OPEN"
    : "";
}

module.exports = { findOpenDependabotAlert };
