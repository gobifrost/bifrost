const test = require("node:test");
const assert = require("node:assert/strict");

const { findOpenDependabotAlert } = require("./dependabot_alert_lookup.cjs");

const repository = { owner: "gobifrost", repo: "bifrost" };
const pullNumber = 920;

function dependency(overrides = {}) {
  return {
    dependencyName: "source-map-js",
    directory: "/api/src/services/app_compiler",
    packageEcosystem: "npm_and_yarn",
    ...overrides,
  };
}

function alert(overrides = {}) {
  return {
    state: "open",
    dependency: {
      manifest_path: "api/src/services/app_compiler/package-lock.json",
      package: { name: "source-map-js", ecosystem: "npm" },
    },
    security_vulnerability: { vulnerable_version_range: ">= 1.0.0, < 1.2.2" },
    ...overrides,
  };
}

function githubFixture({ files = [
  { filename: "api/src/services/app_compiler/package-lock.json", status: "modified" },
], alerts = [alert()] } = {}) {
  const calls = [];
  const listFiles = () => {};
  const listAlertsForRepo = () => {};
  return {
    calls,
    github: {
      rest: { pulls: { listFiles }, dependabot: { listAlertsForRepo } },
      paginate: async (method, args) => {
        calls.push({ method, args });
        if (method === listFiles) return files;
        if (method === listAlertsForRepo) return alerts;
        throw new Error("unexpected pagination endpoint");
      },
    },
  };
}

test("finds the current npm advisory in a changed manifest", async () => {
  const f = githubFixture();

  assert.equal(
    await findOpenDependabotAlert({ github: f.github, repository, pullNumber, dependencies: [dependency()] }),
    "OPEN",
  );
  assert.deepEqual(f.calls.map(({ args }) => args), [
    { ...repository, pull_number: pullNumber, per_page: 100 },
    { ...repository, state: "open", per_page: 100 },
  ]);
});

test("requires the exact changed manifest and Dependabot directory", async () => {
  const wrongFile = githubFixture({ files: [{ filename: "client/package-lock.json", status: "modified" }] });
  assert.equal(
    await findOpenDependabotAlert({ github: wrongFile.github, repository, pullNumber, dependencies: [dependency()] }),
    "",
  );

  const wrongDirectory = githubFixture();
  assert.equal(
    await findOpenDependabotAlert({ github: wrongDirectory.github, repository, pullNumber, dependencies: [dependency({ directory: "/client" })] }),
    "",
  );
});

test("returns no advisory when no matching alert is open", async () => {
  const f = githubFixture({ alerts: [alert({ state: "dismissed" })] });
  assert.equal(
    await findOpenDependabotAlert({ github: f.github, repository, pullNumber, dependencies: [dependency()] }),
    "",
  );
});

test("uses paginated alerts beyond the first page", async () => {
  const f = githubFixture({ alerts: [
    ...Array.from({ length: 100 }, (_, index) => alert({
      dependency: { manifest_path: `unrelated/${index}/package-lock.json`, package: { name: `unrelated-${index}`, ecosystem: "npm" } },
    })),
    alert(),
  ] });
  assert.equal(
    await findOpenDependabotAlert({ github: f.github, repository, pullNumber, dependencies: [dependency()] }),
    "OPEN",
  );
  assert.equal(f.calls.length, 2);
});

test("fails closed when GitHub cannot list alerts", async () => {
  const f = githubFixture();
  f.github.paginate = async (method, args) => {
    if (method === f.github.rest.pulls.listFiles) return [{ filename: "api/src/services/app_compiler/package-lock.json", status: "modified" }];
    throw new Error(`Dependabot alerts are unavailable for ${args.owner}/${args.repo}`);
  };

  await assert.rejects(
    findOpenDependabotAlert({ github: f.github, repository, pullNumber, dependencies: [dependency()] }),
    /Dependabot alerts are unavailable/,
  );
});

test("does not associate an exact manifest alert from another ecosystem", async () => {
  const f = githubFixture();
  assert.equal(
    await findOpenDependabotAlert({ github: f.github, repository, pullNumber, dependencies: [dependency({ packageEcosystem: "pip" })] }),
    "",
  );
});

test("associates pip alerts from the root manifest", async () => {
  const f = githubFixture({
    files: [{ filename: "pyproject.toml", status: "modified" }],
    alerts: [{
      state: "open",
      dependency: {
        manifest_path: "pyproject.toml",
        package: { name: "fastapi", ecosystem: "pip" },
      },
    }],
  });
  assert.equal(
    await findOpenDependabotAlert({
      github: f.github,
      repository,
      pullNumber,
      dependencies: [{ dependencyName: "fastapi", directory: "/", packageEcosystem: "pip" }],
    }),
    "OPEN",
  );
});
