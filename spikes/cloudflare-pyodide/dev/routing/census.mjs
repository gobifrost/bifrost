// Workspace eligibility census.
//
// Drives the same `decideBackend` rule the dispatcher uses over real files, so
// "can Cloudflare run our normal workloads?" stops being an opinion.
//
// Usage:
//   node census.mjs --workspace ../..            # discover .bifrost/workflows.yaml
//   node census.mjs --repo ../..                 # workbook-shaped Python in a tree
//   node census.mjs --workspace <dir> --allowance 12 --out results/census.json
//
// Static analysis only: no workspace is executed, no API is called. The
// subrequest figure is a heuristic (SDK call sites + entry/module resolution),
// and egress is a dispatching-time declaration, so this census treats a file as
// having no declared egress requirement. Both are marked in the output.
import {readFile, readdir, stat, writeFile} from 'node:fs/promises';
import {join, relative, extname, dirname} from 'node:path';
import {decideBackend} from './eligibility.mjs';

const STDLIB = new Set(`__future__ _thread abc aifc argparse array ast asynchat asyncio asyncore atexit audioop base64
bdb binascii bisect builtins bz2 cProfile calendar cgi cgitb chunk cmath cmd code codecs codeop
collections colorsys compileall concurrent configparser contextlib contextvars copy copyreg
crypt csv ctypes curses dataclasses datetime dbm decimal difflib dis distutils doctest email encodings
ensurepip enum errno faulthandler fcntl filecmp fileinput fnmatch formatter fractions ftplib
functools gc getopt getpass gettext glob graphlib grp gzip hashlib heapq hmac html http idlelib
imaplib imghdr imp importlib inspect io ipaddress itertools json keyword lib2to3 linecache locale
logging lzma mailbox mailcap marshal math mimetypes mmap modulefinder msilib msvcrt multiprocessing
netrc nis nntplib numbers operator optparse os ossaudiodev parser pathlib pdb pickle pickletools
pipes pkgutil platform plistlib poplib posix posixpath pprint profile pstats pty pwd py_compile
pyclbr pydoc pydoc_data pyexpat queue quopri random re readline reprlib resource rlcompleter runpy
sched secrets select selectors shelve shlex shutil signal site smtpd smtplib sndhdr socket
socketserver spwd sqlite3 sre_compile sre_constants sre_parse ssl stat statistics string
stringprep struct subprocess sunau symbol symtable sys sysconfig syslog tabnanny tarfile telnetlib
tempfile termios test textwrap threading time timeit tkinter token tokenize tomllib trace traceback
tracemalloc tty turtle turtledemo types typing unicodedata unittest urllib uu uuid venv warnings
wave weakref webbrowser winreg winsound wsgiref xdrlib xml xmlrpc zipapp zipfile zipimport zlib
zoneinfo`.split(/\s+/).filter(Boolean));

// Roots provided by the platform/runtime, not by the workspace: the bundled SDK,
// vendored engine modules, and Pyodide/Workers shims. They are neither
// third-party packages nor workspace modules that must resolve in a manifest.
const PROVIDED = new Set(['bifrost', 'src', 'shared', 'bifrost_shared', 'workers', 'pyodide', 'js', '_pyodide']);

// Project-local import roots. A root is treated as local when the workspace
// actually contains it; this list only seeds the check.
const LOCAL_HINT = new Set(['bifrost', 'src', 'shared', 'modules', 'workflows', 'functions',
  'apps', 'helpers', 'lib', 'core', 'services', 'models', 'utils', 'providers', 'config',
  'engine', 'runtime_runner', 'bifrost_shared']);

// Child-side runtime traps. Warnings do not necessarily make a file ineligible;
// they are reported so a reviewer sees them.
const HARD_TRAPS = [
  [/\btime\.process_time\s*\(/, 'time.process_time() raises in Pyodide'],
  [/\bsubprocess\b/, 'subprocess is unavailable in Pyodide'],
  [/\bmultiprocessing\b/, 'multiprocessing is unavailable in Pyodide'],
  [/\bfcntl\b/, 'fcntl is unavailable in Pyodide'],
  [/\bos\.fork\s*\(/, 'os.fork() is unavailable in Pyodide'],
  [/\bctypes\b/, 'ctypes is unavailable in Pyodide'],
  [/\bsocket\.socket\s*\(/, 'raw sockets are unavailable; use declared egress'],
  [/\bsignal\.\w+\s*\(/, 'POSIX signal handling is unavailable'],
];
const SOFT_TRAPS = [
  [/\bthreading\b/, 'threads import but do not run in parallel'],
  [/\bresource\b/, 'resource is not available in Pyodide'],
];

const args = process.argv.slice(2);
const flag = (name, fallback) => args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback;
const allowance = Number(flag('allowance', '12'));
const outPath = flag('out', null);
const wsRoot = flag('workspace', null);
const repoRoot = flag('repo', null);

const SKIP_DIRS = new Set(['node_modules', '.git', '.venv', 'venv', 'python_modules', 'vendor',
  '.wrangler', '.cloudflare', 'dist', 'build', '__pycache__', 'site-packages', 'tests', 'test',
  '__tests__']);
const SKIP_PATH = /(^|\/)(tests?|__tests__|test-support)\//;

async function walk(dir, files = []) {
  let entries;
  try { entries = await readdir(dir, {withFileTypes: true}); } catch { return files; }
  for (const e of entries) {
    if (e.isDirectory()) {
      if (SKIP_DIRS.has(e.name)) continue;
      await walk(join(dir, e.name), files);
    } else if (e.isFile() && !/^(test_.*|.*_test)\.py$/.test(e.name)) {
      files.push(join(dir, e.name));
    }
  }
  return files;
}

function importsOf(source) {
  const roots = new Set();
  for (const line of source.split(/\r?\n/)) {
    const m = line.match(/^\s*from\s+([A-Za-z_][\w.]*)\s+import\b/);
    if (m) { roots.add(m[1].split('.')[0]); continue; }
    const n = line.match(/^\s*import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*)/);
    if (n) for (const part of n[1].split(',')) roots.add(part.trim().split('.')[0]);
  }
  return roots;
}

function dottedImports(source) {
  const mods = [];
  for (const line of source.split(/\r?\n/)) {
    const m = line.match(/^\s*from\s+([A-Za-z_][\w.]*)\s+import\b/);
    if (m) mods.push(m[1]);
  }
  return mods;
}

async function exists(p) {
  try { await stat(p); return true; } catch { return false; }
}

// Resolve a local dotted import to a workspace storage path, mirroring the
// engine's logical-name -> base-path rule.
async function localStoragePath(workspaceRoot, dotted) {
  const base = dotted.replaceAll('.', '/');
  for (const cand of [`${base}.py`, `${base}/__init__.py`]) {
    if (await exists(join(workspaceRoot, cand))) return cand;
  }
  return null;
}

function subrequestEstimate(source, localCount) {
  const calls = (source.match(/\b(?:await\s+)?(?:sdk|tables|files|config|ai|integrations|agents|forms|events|storage|http|bifrost)\.[a-zA-Z_]+\s*\(/g) ?? []).length;
  return calls + 2 + localCount; // + entry resolve + entry load
}

// Approximate per-workflow call density: the manifest names the entry function,
// and each workflow is dispatched separately, so counting the whole file would
// over-estimate multi-workflow modules.
function functionSlice(source, name) {
  if (!name) return source;
  const start = source.match(new RegExp(`^[ \\t]*(?:async\\s+)?def\\s+${name}\\s*\\(`, 'm'));
  if (!start) return source;
  const rest = source.slice(start.index + start[0].length);
  const next = rest.search(/^[ \t]*(?:async\s+)?def\s+\w+\s*\(/m);
  return next === -1 ? rest : rest.slice(0, next);
}

async function censusFile(workspaceRoot, file, opts) {
  const source = await readFile(file, 'utf8');
  const roots = importsOf(source);
  const declaredModules = [];
  const unresolvedLocal = [];
  if (opts.resolveLocal !== false) {
    for (const dotted of dottedImports(source)) {
      const root = dotted.split('.')[0];
      if (PROVIDED.has(root) || !LOCAL_HINT.has(root)) continue;
      const p = await localStoragePath(workspaceRoot, dotted);
      if (p) declaredModules.push(p); else unresolvedLocal.push(dotted);
    }
  }
  const packages = [...roots].filter((r) => !STDLIB.has(r) && !LOCAL_HINT.has(r) && !PROVIDED.has(r));
  const scope = functionSlice(source, opts.functionName);
  const traps = [];
  for (const [re, why] of HARD_TRAPS) if (re.test(scope)) traps.push({severity: 'hard', why});
  for (const [re, why] of SOFT_TRAPS) if (re.test(scope)) traps.push({severity: 'soft', why});

  const declaration = {
    modules: declaredModules,
    packages,
    egressHosts: [],
    egressRequired: [],
    estimatedSubrequests: subrequestEstimate(scope, declaredModules.length),
    cpuBudget: opts.cpuBudget,
    source: scope,
  };
  const decision = decideBackend({declaration, bundleManifest: opts.manifest?.length ? opts.manifest : declaredModules,
    subrequestAllowance: opts.allowance});
  // A local import that does not resolve on disk fails at dispatch, so the
  // census must not call it eligible just because it is not third-party.
  if (unresolvedLocal.length > 0) {
    decision.reasons.push(`unresolved local module: ${unresolvedLocal.join(', ')}`);
    decision.backend = 'linux';
  }
  return {
    path: relative(workspaceRoot, file).replaceAll('\\', '/'),
    backend: decision.backend,
    reasons: decision.reasons,
    packages, declared_modules: declaredModules, unresolved_local: unresolvedLocal,
    estimated_subrequests: declaration.estimatedSubrequests,
    traps,
  };
}

async function workspaceManifest(root) {
  const files = (await walk(root)).filter((f) => extname(f) === '.py' && !SKIP_PATH.test(f));
  return {files, manifest: files.map((f) => relative(root, f).replaceAll('\\', '/'))};
}

function summarize(rows, label) {
  const eligible = rows.filter((r) => r.backend === 'cloudflare');
  const reasons = new Map();
  for (const r of rows) for (const reason of r.reasons) {
    const key = reason.split(':')[0];
    reasons.set(key, (reasons.get(key) ?? 0) + 1);
  }
  const trapCounts = new Map();
  for (const r of rows) for (const t of r.traps) trapCounts.set(t.why, (trapCounts.get(t.why) ?? 0) + 1);
  return {
    label,
    files: rows.length,
    eligible: eligible.length,
    ineligible: rows.length - eligible.length,
    eligible_pct: rows.length ? Math.round((1000 * eligible.length) / rows.length) / 10 : null,
    by_reason: Object.fromEntries([...reasons].sort((a, b) => b[1] - a[1])),
    traps: Object.fromEntries([...trapCounts].sort((a, b) => b[1] - a[1])),
  };
}

function roleOf(p) {
  const n = p.replaceAll('\\', '/');
  if (n.startsWith('examples/')) return 'example workspace';
  if (n.startsWith('api/bifrost/commands/')) return 'cli';
  if (n.startsWith('api/bifrost/')) return 'sdk';
  if (n.startsWith('api/src/') || n.startsWith('api/shared/')) return 'platform';
  if (n.startsWith('spikes/')) return 'spike fixture';
  if (n.startsWith('docs/')) return 'docs';
  return 'other';
}

const report = {generated_at: new Date().toISOString(), allowance, workspaces: [], repo: null};

if (wsRoot) {
  const touched = (await walk(wsRoot)).filter((f) => f.replaceAll('\\', '/').endsWith('.bifrost/workflows.yaml'));
  for (const manifestPath of touched) {
    const root = dirname(dirname(manifestPath));
    const {manifest} = await workspaceManifest(root);
    const yaml = await readFile(manifestPath, 'utf8');
    const entries = [];
    let cur = null;
    for (const line of yaml.split(/\r?\n/)) {
      const p = line.match(/^\s*path:\s*["']?([^"'\r\n]+?)["']?\s*$/);
      if (p) { if (cur?.path) entries.push(cur); cur = {path: p[1].trim()}; continue; }
      const f = line.match(/^\s*function_name:\s*["']?([^"'\r\n]+?)["']?\s*$/);
      if (f && cur) cur.function_name = f[1].trim();
    }
    if (cur?.path) entries.push(cur);
    const rows = [];
    for (const entry of entries) {
      const file = join(root, entry.path);
      if (!(await exists(file))) {
        rows.push({path: entry.path, workflow: entry.function_name ?? null, backend: 'linux', reasons: ['missing workflow file']});
        continue;
      }
      const row = await censusFile(root, file, {manifest, allowance,
        cpuBudget: flag('cpu', '1000ms'), functionName: entry.function_name});
      rows.push({...row, workflow: entry.function_name ?? null});
    }
    report.workspaces.push({root: relative(wsRoot, root).replaceAll('\\', '/') || '.', ...summarize(rows, relative(wsRoot, root)), rows});
  }
}

if (repoRoot) {
  const all = (await walk(repoRoot)).filter((f) => extname(f) === '.py' && !SKIP_PATH.test(f));
  const rows = [];
  for (const file of all) {
    const source = await readFile(file, 'utf8');
    const isWorkflow = /^\s*(?:from|import)\s+bifrost\b/m.test(source) && /^\s*async\s+def\s/m.test(source);
    if (!isWorkflow) continue;
    rows.push({...await censusFile(repoRoot, file, {manifest: [], allowance,
      cpuBudget: flag('cpu', '1000ms'), resolveLocal: false}),
      path: relative(repoRoot, file).replaceAll('\\', '/'), role: roleOf(relative(repoRoot, file))});
  }
  report.repo = {...summarize(rows, 'repo workflow-shaped Python'), rows};
  report.repo.by_role = Object.fromEntries([...new Set(rows.map((r) => r.role))].map((role) => {
    const subset = rows.filter((r) => r.role === role);
    return [role, {files: subset.length, eligible: subset.filter((r) => r.backend === 'cloudflare').length}];
  }));
  report.repo.allowance_sensitivity = Object.fromEntries([12, 50, 200].map((a) => {
    const n = rows.filter((r) => decideBackend({
      declaration: {modules: r.declared_modules, packages: r.packages, egressHosts: [], egressRequired: [],
        estimatedSubrequests: r.estimated_subrequests, cpuBudget: flag('cpu', '1000ms')},
      bundleManifest: r.declared_modules, subrequestAllowance: a}).backend === 'cloudflare').length;
    return [String(a), n];
  }));
}

const lines = ['# Eligibility census', ''];
for (const w of report.workspaces) lines.push(`- workspace \`${w.root}\`: ${w.eligible}/${w.files} eligible (${w.eligible_pct}%)`);
if (report.repo) lines.push(`- repo workflow-shaped: ${report.repo.eligible}/${report.repo.files} eligible (${report.repo.eligible_pct}%)`);
lines.push('', `Subrequest allowance: ${allowance}. Static analysis only.`);
report.summary_text = lines.join('\n');
console.log(report.summary_text);
if (outPath) await writeFile(outPath, JSON.stringify(report, null, 2) + '\n');
