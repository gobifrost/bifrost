// Dispatcher-side eligibility rule: pure decision only.
//
// Source of truth: spikes/cloudflare-pyodide/backend/ELIGIBILITY.md.
// This module returns a routing decision; it must not enqueue, dispatch,
// or call any API. There is no orchestration layer here by design.
//
// Inputs (all declaration-driven, per ELIGIBILITY.md "Eligibility inputs"):
//   attempt = {
//     declaration: {
//       modules: string[],            // declared workspace modules (requirements.modules)
//       packages: string[],           // imported third-party distributions
//       egressHosts: string[],        // declared exact HTTPS hostnames (egress.hosts)
//       egressRequired: string[],     // hosts the workflow will actually reach (must be a subset)
//       estimatedSubrequests: number, // SDK calls + third-party HTTP + module resolution
//       cpuBudget: number|string,     // bounded operator-set budget per workload class
//       source: string,               // workflow source, scanned for runtime traps
//     },
//     source: string,                 // alternate location for workflow source
//     bundleManifest: string[] | { modules: string[] }, // staged-bundle manifest
//     subrequestAllowance: number,    // configured subrequest allowance (the real bound)
//   }
//
// Fail-closed: unknown/missing declaration, unparseable estimate, missing
// allowance/budget/manifest, or an allowlist miss => { backend: 'linux', ... }
// with an explicit reason string. Never a silent default to cloudflare, and
// never a silent fallback mid-run (this function only decides before dispatch).

// Curated third-party allowlist. Source: backend/DEPENDENCY-CENSUS.md
// (shipped bundle census + measured requests/msal/PyJWT result) as summarized
// by backend/ELIGIBILITY.md ("Third-party packages"): httpx, pydantic(+core),
// python-dotenv, PyYAML, redis, requests, urllib3, charset-normalizer, msal,
// PyJWT, certifi, plus their bundled transitive/platform companions
// (idna, typing_extensions, typing_inspection, annotated_types,
// workers-runtime-sdk). Anything outside this set routes to Linux until it
// has its own measured import test.
const ALLOWLIST = new Set(
  [
    'httpx',
    'pydantic',
    'pydantic-core',
    'python-dotenv',
    'pyyaml',
    'redis',
    'requests',
    'urllib3',
    'charset-normalizer',
    'msal',
    'pyjwt',
    'certifi',
    'idna',
    'typing-extensions',
    'typing-inspection',
    'annotated-types',
    'workers-runtime-sdk',
  ].map(normalizeDistribution),
);

// Python distribution-name normalization (PEP 503 style): case-insensitive,
// runs of '-', '_', '.' are equivalent.
function normalizeDistribution(name) {
  return String(name).toLowerCase().replace(/[-_.]+/g, '-');
}

// Exact HTTPS hostname check: bare hostname only, no scheme, port, path,
// query, wildcard, or whitespace.
function isExactHostname(host) {
  if (typeof host !== 'string') return false;
  if (host !== host.trim() || host === '') return false;
  if (host.includes('://') || host.includes('/') || host.includes('?') || host.includes('#')) return false;
  if (host.includes('*') || host.includes(' ') || host.includes(':') || host.includes('@')) return false;
  if (host.length > 253) return false;
  const labels = host.split('.');
  if (labels.length < 2) return false;
  return labels.every(
    (label) =>
      label.length >= 1 &&
      label.length <= 63 &&
      /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/i.test(label),
  );
}

function parseCount(value) {
  if (typeof value === 'number') return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value.trim());
    return parsed;
  }
  return NaN;
}

// Static source trap from ELIGIBILITY.md "Known runtime traps": workflow
// source containing time.process_time( is ineligible for Cloudflare.
const PROCESS_TIME_NEEDLE = 'time.process_time(';
const PROCESS_TIME_EXPLANATION =
  'time.process_time() raises in Pyodide (RuntimeError: processor time ... cannot be represented). ' +
  'Workflows measuring their own CPU this way must run on Linux.';

export function containsProcessTimeTrap(source) {
  return typeof source === 'string' && source.includes(PROCESS_TIME_NEEDLE);
}

export function decideBackend(attempt) {
  const reasons = [];
  const linux = (reason) => {
    reasons.push(reason);
    return { backend: 'linux', reasons };
  };

  if (!attempt || typeof attempt !== 'object') {
    return linux('missing declaration: attempt is not an object; failing closed to linux, never a silent default to cloudflare');
  }
  const declaration = attempt.declaration;
  if (!declaration || typeof declaration !== 'object') {
    return linux('missing declaration: no declaration provided; failing closed to linux, never a silent default to cloudflare');
  }

  // 1. Modules: every declared workspace module must resolve in the staged bundle manifest.
  const declaredModules = declaration.modules;
  if (!Array.isArray(declaredModules)) {
    reasons.push('missing declaration: requirements.modules is not declared; failing closed to linux');
  } else {
    const manifest = attempt.bundleManifest;
    const manifestModules =
      (Array.isArray(manifest) && manifest) ||
      (manifest && typeof manifest === 'object' && Array.isArray(manifest.modules) && manifest.modules) ||
      null;
    if (manifestModules === null) {
      reasons.push('missing declaration: staged-bundle manifest is missing or unparseable; failing closed to linux');
    } else {
      const staged = new Set(manifestModules.map(String));
      for (const mod of declaredModules) {
        if (!staged.has(String(mod))) {
          reasons.push(`undeclared module: workspace module '${mod}' does not resolve in the staged bundle manifest; failing closed to linux`);
        }
      }
    }
  }

  // 2. Third-party packages: every imported distribution must be in the curated allowlist.
  const packages = declaration.packages;
  if (!Array.isArray(packages)) {
    reasons.push('missing declaration: third-party packages are not declared; failing closed to linux');
  } else {
    for (const pkg of packages) {
      if (typeof pkg !== 'string' || pkg.trim() === '') {
        reasons.push('missing declaration: package entry is not a parseable distribution name; failing closed to linux');
        continue;
      }
      if (!ALLOWLIST.has(normalizeDistribution(pkg.trim()))) {
        reasons.push(`allowlist miss: package '${pkg}' is not in the curated allowlist (backend/DEPENDENCY-CENSUS.md); failing closed to linux`);
      }
    }
  }

  // 3. Egress: exact HTTPS hostnames only; every required host must be declared.
  const egressHosts = declaration.egressHosts;
  if (!Array.isArray(egressHosts)) {
    reasons.push('missing declaration: egress.hosts is not declared; failing closed to linux');
  } else {
    const declared = new Set();
    for (const host of egressHosts) {
      if (!isExactHostname(host)) {
        reasons.push(`invalid egress host: '${host}' is not an exact HTTPS hostname; failing closed to linux`);
      } else {
        declared.add(String(host).toLowerCase());
      }
    }
    const required = declaration.egressRequired ?? declaration.requiredHosts ?? declaration.egressNeeded ?? [];
    if (!Array.isArray(required)) {
      reasons.push('missing declaration: required egress hosts are unparseable; failing closed to linux');
    } else {
      for (const host of required) {
        if (!isExactHostname(host) || !declared.has(String(host).toLowerCase())) {
          reasons.push(`undeclared egress host: '${host}' is not in the declared egress.hosts allowlist; failing closed to linux`);
        }
      }
    }
  }

  // 4. Subrequests: the estimate must parse and fit the configured allowance.
  // This is the real bound, not wall clock.
  const estimate = parseCount(declaration.estimatedSubrequests);
  if (!Number.isFinite(estimate) || estimate < 0 || !Number.isInteger(estimate)) {
    reasons.push('unparseable estimate: estimated subrequests is missing or not a non-negative integer; failing closed to linux');
  }
  const allowanceRaw = attempt.subrequestAllowance ?? attempt.allowance ?? declaration.subrequestAllowance;
  const allowance = parseCount(allowanceRaw);
  if (!Number.isFinite(allowance) || allowance < 0 || !Number.isInteger(allowance)) {
    reasons.push('missing declaration: subrequest allowance is missing or unparseable; failing closed to linux');
  }
  if (
    Number.isFinite(estimate) && Number.isInteger(estimate) && estimate >= 0 &&
    Number.isFinite(allowance) && Number.isInteger(allowance) && allowance >= 0 &&
    estimate > allowance
  ) {
    reasons.push(`subrequest over budget: estimated ${estimate} exceeds configured allowance ${allowance}; failing closed to linux`);
  }

  // 5. CPU: a bounded budget the operator sets per workload class. CPU burn is
  // an operator cost, not a platform safety gate; no kill primitive is assumed.
  // A missing, unbounded, or unparseable budget fails closed to linux.
  const cpuBudget = declaration.cpuBudget ?? declaration.cpu_budget;
  let cpuOk = false;
  if (typeof cpuBudget === 'number') {
    cpuOk = Number.isFinite(cpuBudget) && cpuBudget > 0;
  } else if (typeof cpuBudget === 'string' && cpuBudget.trim() !== '') {
    const lowered = cpuBudget.trim().toLowerCase();
    if (lowered === 'unlimited' || lowered === 'unbounded' || lowered === 'none' || lowered === 'infinity') {
      cpuOk = false;
    } else {
      const match = lowered.match(/^(\d+(?:\.\d+)?)\s*(ms|s|sec|secs|second|seconds)?$/);
      cpuOk = match !== null && Number(match[1]) > 0 && Number.isFinite(Number(match[1]));
    }
  }
  if (!cpuOk) {
    reasons.push('missing declaration: CPU budget is missing, unbounded, or unparseable; failing closed to linux');
  }

  // 6. Static source trap: time.process_time() raises in Pyodide.
  const source = declaration.source ?? attempt.source;
  if (typeof source === 'string' && source !== '' && containsProcessTimeTrap(source)) {
    reasons.push(PROCESS_TIME_EXPLANATION);
  }

  if (reasons.length > 0) {
    return { backend: 'linux', reasons };
  }
  return { backend: 'cloudflare', reasons: [] };
}

export default decideBackend;
