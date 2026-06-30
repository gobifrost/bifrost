#!/usr/bin/env node
/**
 * Bootstrap a screenshots.yaml manifest for the gobifrost site.
 *
 * Inputs:
 *   --docs-repo <path>    path to gobifrost checkout
 *   --bifrost-repo <path> path to this bifrost checkout (defaults to script's repo)
 *   --force               overwrite existing screenshots.yaml
 *
 * Outputs (written into docs repo):
 *   screenshots.yaml      draft manifest covering every existing image reference
 *   bootstrap-report.md   gap list + low-confidence flags
 */
import { readFileSync, writeFileSync, existsSync, readdirSync, statSync, renameSync } from "node:fs";
import { resolve, relative, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as yaml from "js-yaml";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function parseArgs(argv) {
  const out = { force: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--docs-repo") out.docsRepo = argv[++i];
    else if (a === "--bifrost-repo") out.bifrostRepo = argv[++i];
    else if (a === "--force") out.force = true;
    else if (a === "-h" || a === "--help") {
      console.log(readFileSync(__filename, "utf8").split("\n").slice(1, 14).join("\n"));
      process.exit(0);
    } else {
      console.error(`unknown arg: ${a}`);
      process.exit(2);
    }
  }
  if (!out.docsRepo) {
    console.error("--docs-repo is required");
    process.exit(2);
  }
  if (!out.bifrostRepo) {
    out.bifrostRepo = resolve(__dirname, "..", "..");
  }
  return out;
}

function walkFiles(root, predicate) {
  const out = [];
  function visit(dir) {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      const s = statSync(p);
      if (s.isDirectory()) visit(p);
      else if (predicate(p)) out.push(p);
    }
  }
  visit(root);
  return out;
}

function inventoryMdx(docsRepo) {
  const contentDir = resolve(docsRepo, "src/content/docs");
  const mdxFiles = walkFiles(contentDir, (p) => p.endsWith(".mdx") || p.endsWith(".md"));
  const out = [];
  for (const file of mdxFiles) {
    const raw = readFileSync(file, "utf8");
    const fmMatch = raw.match(/^---\n([\s\S]*?)\n---\n/);
    let frontmatter = {};
    let body = raw;
    if (fmMatch) {
      try {
        frontmatter = yaml.load(fmMatch[1]) ?? {};
      } catch {
        frontmatter = {};
      }
      body = raw.slice(fmMatch[0].length);
    }

    const lines = body.split("\n");
    const headings = [];
    for (let i = 0; i < lines.length; i++) {
      const m = lines[i].match(/^(#{1,4})\s+(.+?)\s*$/);
      if (m) headings.push({ line: i, level: m[1].length, text: m[2] });
    }

    const imageRe = /!\[([^\]]*)\]\(([^)]+)\)/g;
    const images = [];
    let m;
    while ((m = imageRe.exec(body)) !== null) {
      const before = body.slice(0, m.index);
      const lineIdx = before.split("\n").length - 1;
      const nearestHeading = [...headings].reverse().find((h) => h.line <= lineIdx) ?? null;
      const ref = m[2].trim();
      if (!/\.(png|jpg|jpeg|gif|webp|svg)$/i.test(ref)) continue;
      images.push({
        alt: m[1],
        ref,
        line: lineIdx,
        nearestHeading: nearestHeading
          ? { text: nearestHeading.text, level: nearestHeading.level }
          : null,
      });
    }

    out.push({
      file,
      relPath: relative(docsRepo, file),
      frontmatter,
      headings,
      images,
    });
  }
  return out;
}

function inventoryRoutes(bifrostRepo) {
  const appPath = resolve(bifrostRepo, "client/src/App.tsx");
  if (!existsSync(appPath)) return [];
  const src = readFileSync(appPath, "utf8");

  // Strip JSX comments {/* ... */} and JS line comments to simplify scanning.
  const stripped = src
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");

  // Walk character-by-character tracking <Route ... > opens, self-closing />, and </Route> closes.
  // For each open, capture the attribute block until the matching > or />.
  const out = [];
  const stack = []; // ancestor path segments
  let i = 0;
  const n = stripped.length;
  while (i < n) {
    if (stripped[i] === "<" && stripped.slice(i, i + 6) === "<Route") {
      // find the end of the opening tag, respecting {} nesting
      let j = i + 6;
      let depth = 0;
      let inStr = null;
      while (j < n) {
        const c = stripped[j];
        if (inStr) {
          if (c === inStr && stripped[j - 1] !== "\\") inStr = null;
        } else if (c === '"' || c === "'") {
          inStr = c;
        } else if (c === "{") {
          depth++;
        } else if (c === "}") {
          depth--;
        } else if (c === ">" && depth === 0) {
          break;
        }
        j++;
      }
      if (j >= n) break;
      const tagBody = stripped.slice(i + 6, j); // content between "<Route" and ">"
      const selfClose = stripped[j - 1] === "/";
      const pathMatch = tagBody.match(/\bpath\s*=\s*["']([^"']+)["']/);
      const indexMatch = /\bindex\b/.test(tagBody);
      const elMatch = tagBody.match(/element\s*=\s*\{\s*<([A-Z]\w+)/);

      const segment = pathMatch ? pathMatch[1] : indexMatch ? "" : null;
      let fullPath = null;
      if (segment !== null) {
        const parentPath = stack.map((s) => s.replace(/^\/|\/$/g, "")).filter(Boolean).join("/");
        const seg = segment.replace(/^\/|\/$/g, "");
        fullPath = "/" + [parentPath, seg].filter(Boolean).join("/");
        if (fullPath === "/") fullPath = "/";
      }
      if (fullPath !== null) {
        out.push({
          path: fullPath,
          segment,
          element: elMatch ? elMatch[1] : null,
          line: stripped.slice(0, i).split("\n").length,
          index: indexMatch,
        });
      }
      if (!selfClose) {
        stack.push(segment ?? "");
      }
      i = j + 1;
      continue;
    }
    if (stripped[i] === "<" && stripped.slice(i, i + 8) === "</Route>") {
      stack.pop();
      i += 8;
      continue;
    }
    i++;
  }
  return out;
}

function tokenize(s) {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .split(" ")
    .filter((t) => t.length > 2);
}

function jaccard(a, b) {
  if (!a.length || !b.length) return 0;
  const A = new Set(a), B = new Set(b);
  let inter = 0;
  for (const x of A) if (B.has(x)) inter++;
  return inter / (A.size + B.size - inter);
}

const SEED_RULES = [
  { match: /^\/forms/, seed: "forms_with_sample_org" },
  { match: /^\/agents/, seed: "agent_with_run_history" },
  { match: /^\/workflows/, seed: "org_with_one_workflow" },
  { match: /^\/integrations/, seed: "org_with_integration" },
  { match: /^\/apps/, seed: "org_with_one_app" },
  { match: /^\/(login|setup|mfa-setup|device|register)/, seed: null, auth_as: "unauthenticated" },
  { match: /^\/$/, seed: "org_with_one_workflow" },
];

const TYPE_RULES = [
  { match: /\/getting-started\//, type: "tutorial" },
  { match: /\/how-to-guides\//, type: "how-to" },
  { match: /\/sdk-reference\//, type: "reference" },
  { match: /\/core-concepts\//, type: "explanation" },
  { match: /\/about\//, type: "explanation" },
  { match: /\/troubleshooting\//, type: "how-to" },
];

function diataxisTypeFor(mdxRel) {
  for (const r of TYPE_RULES) if (r.match.test(mdxRel)) return r.type;
  return "explanation";
}

function seedFor(routePath) {
  for (const r of SEED_RULES) if (r.match.test(routePath)) return { seed: r.seed, auth_as: r.auth_as };
  return { seed: null };
}

function makeIdFromImage(imageAbsRel, mdxRel) {
  const slug = imageAbsRel
    .replace(/^src\/assets\//, "")
    .replace(/\.[^.]+$/, "")
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  if (slug && !/^image(-\d+)?$/.test(slug.split("-").pop())) return slug;
  const mdxSlug = mdxRel
    .replace(/^src\/content\/docs\//, "")
    .replace(/\.mdx?$/, "")
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  return `${mdxSlug}-${slug.split("-").pop()}`;
}

function resolveImageRef(mdxFileAbs, ref, docsRepo) {
  if (ref.startsWith("/")) return ref.slice(1);
  if (ref.startsWith("../") || ref.startsWith("./")) {
    const abs = resolve(dirname(mdxFileAbs), ref);
    return relative(docsRepo, abs);
  }
  return ref;
}

// Map of route-family hints. If any token in the MDX context appears here,
// boost routes whose top-level path segment matches.
const ROUTE_FAMILY_HINTS = {
  workflow: "workflows",
  workflows: "workflows",
  form: "forms",
  forms: "forms",
  "form-builder": "forms",
  agent: "agents",
  agents: "agents",
  app: "apps",
  apps: "apps",
  integration: "integrations",
  integrations: "integrations",
  organization: "organizations",
  organizations: "organizations",
  user: "users",
  users: "users",
  history: "history",
  dashboard: "",
  login: "login",
  setup: "setup",
};

function familyForTokens(tokens) {
  const families = new Set();
  for (const t of tokens) {
    const f = ROUTE_FAMILY_HINTS[t];
    if (f !== undefined) families.add(f);
  }
  return families;
}

function topSegment(routePath) {
  return routePath.replace(/^\//, "").split("/")[0] ?? "";
}

function inferRoute(mdxRelPath, headingText, frontmatterTitle, routes) {
  const mdxTokens = [
    ...tokenize(mdxRelPath.replace(/^src\/content\/docs\//, "")),
    ...tokenize(headingText ?? ""),
    ...tokenize(frontmatterTitle ?? ""),
  ];
  const families = familyForTokens(mdxTokens);

  let best = { route: null, score: 0, reason: "no-match" };
  for (const r of routes) {
    const routeTokens = [
      ...tokenize(r.path),
      ...tokenize(r.element ?? ""),
    ];
    let s = jaccard(mdxTokens, routeTokens);

    // Boost if route's top segment matches one of the inferred families.
    if (families.size && families.has(topSegment(r.path))) {
      s += 0.5;
    }
    // Penalize parameterized routes when MDX context doesn't clearly need them.
    if (/[:*]/.test(r.path)) s -= 0.1;
    // Penalize the catch-all "/" unless heading mentions dashboard.
    if (r.path === "/" && !mdxTokens.includes("dashboard")) s -= 0.3;

    if (s > best.score) {
      best = { route: r, score: s };
    }
  }
  return best;
}

function main() {
  const args = parseArgs(process.argv);
  const manifestPath = resolve(args.docsRepo, "screenshots.yaml");
  const reportPath = resolve(args.docsRepo, "bootstrap-report.md");

  if (existsSync(manifestPath) && !args.force) {
    console.error(`screenshots.yaml already exists at ${manifestPath}; pass --force to overwrite.`);
    process.exit(1);
  }

  console.log(`Inventorying MDX files in ${args.docsRepo} ...`);
  const mdx = inventoryMdx(args.docsRepo);
  const totalImages = mdx.reduce((n, p) => n + p.images.length, 0);
  console.log(`  ${mdx.length} MDX files, ${totalImages} image references`);

  console.log(`Inventorying client routes in ${args.bifrostRepo} ...`);
  const routes = inventoryRoutes(args.bifrostRepo);
  console.log(`  ${routes.length} routes found`);

  const entries = [];
  const flags = [];
  const gapPages = [];
  const seenIds = new Map();

  for (const page of mdx) {
    if (!page.images.length) {
      gapPages.push({
        page: page.relPath,
        title: page.frontmatter?.title ?? null,
      });
      continue;
    }
    for (const img of page.images) {
      const imageRel = resolveImageRef(page.file, img.ref, args.docsRepo);
      const heading = img.nearestHeading?.text ?? page.frontmatter?.title ?? null;
      const inferred = inferRoute(page.relPath, heading, page.frontmatter?.title, routes);
      const route = inferred.route ? "/" + inferred.route.path.replace(/^\//, "") : null;
      const seedInfo = route ? seedFor(route) : { seed: null };
      const dxType = diataxisTypeFor(page.relPath);

      let id = makeIdFromImage(imageRel, page.relPath);
      let i = 1;
      while (seenIds.has(id)) id = `${makeIdFromImage(imageRel, page.relPath)}-${++i}`;
      seenIds.set(id, true);

      const lowConfidence = !route || route === "/" || inferred.score < 0.3;

      const entry = {
        id,
        image: imageRel,
        route: route ?? "/",
        ...(seedInfo.seed ? { seed: seedInfo.seed } : {}),
        ...(seedInfo.auth_as ? { auth_as: seedInfo.auth_as } : {}),
        diataxis: {
          page: page.relPath,
          type: dxType,
          ...(heading ? { heading } : {}),
        },
        captured_at: null,
      };
      entries.push(entry);

      if (lowConfidence) {
        flags.push({
          id,
          image: imageRel,
          page: page.relPath,
          heading,
          inferredRoute: route,
          score: inferred.score.toFixed(2),
          reason: route ? "low-confidence-match" : "no-route-match",
        });
      }
    }
  }

  const manifest = {
    version: 1,
    defaults: {
      auth_as: "platform_admin",
      viewport: { width: 1440, height: 900 },
      pad: 16,
    },
    entries,
  };
  // Atomic write: write to temp file then rename, so a concurrent reader
  // never sees a half-written manifest (closes CodeQL js/file-system-race).
  const manifestTmp = `${manifestPath}.tmp-${process.pid}-${Date.now()}`;
  writeFileSync(manifestTmp, yaml.dump(manifest, { lineWidth: 120, noRefs: true }));
  renameSync(manifestTmp, manifestPath);

  const reportLines = [];
  reportLines.push(`# Bootstrap Report`);
  reportLines.push("");
  reportLines.push(`Generated against bifrost @ \`${args.bifrostRepo}\` and docs @ \`${args.docsRepo}\`.`);
  reportLines.push("");
  reportLines.push(`## Summary`);
  reportLines.push(`- MDX files scanned: **${mdx.length}**`);
  reportLines.push(`- Image references found: **${totalImages}**`);
  reportLines.push(`- Manifest entries written: **${entries.length}**`);
  reportLines.push(`- Low-confidence/missing routes: **${flags.length}**`);
  reportLines.push(`- Pages without screenshots: **${gapPages.length}**`);
  reportLines.push("");

  if (flags.length) {
    reportLines.push(`## Low-confidence route inferences`);
    reportLines.push("");
    reportLines.push(`These entries need a human to confirm or correct the \`route:\` field before the first capture.`);
    reportLines.push("");
    reportLines.push(`| id | page | heading | inferred route | score | reason |`);
    reportLines.push(`|---|---|---|---|---|---|`);
    for (const f of flags) {
      reportLines.push(
        `| \`${f.id}\` | \`${f.page}\` | ${f.heading ?? "_(none)_"} | \`${f.inferredRoute ?? "?"}\` | ${f.score} | ${f.reason} |`,
      );
    }
    reportLines.push("");
  }

  if (gapPages.length) {
    reportLines.push(`## Pages without screenshots`);
    reportLines.push("");
    reportLines.push(`These MDX files have zero image references. Either they're text-only by design, or they need entries added manually.`);
    reportLines.push("");
    for (const g of gapPages) {
      reportLines.push(`- \`${g.page}\`${g.title ? ` — ${g.title}` : ""}`);
    }
    reportLines.push("");
  }

  reportLines.push(`## Next steps`);
  reportLines.push("");
  reportLines.push(`1. Review the low-confidence rows above and correct \`route:\` in \`screenshots.yaml\`.`);
  reportLines.push(`2. For any entry where the screenshot should be cropped or have a callout, add \`capture.crop\` or \`capture.callouts\` (use the draw-a-box helper).`);
  reportLines.push(`3. Run \`npm run lint:manifest\` to validate.`);
  reportLines.push(`4. Run the capture pipeline.`);
  reportLines.push("");

  writeFileSync(reportPath, reportLines.join("\n"));

  console.log("");
  console.log(`Wrote ${manifestPath}`);
  console.log(`Wrote ${reportPath}`);
  console.log(`  Entries:           ${entries.length}`);
  console.log(`  Flagged for review: ${flags.length}`);
  console.log(`  Pages w/o images:   ${gapPages.length}`);
}

main();
