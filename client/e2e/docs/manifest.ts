/**
 * Read and validate the docs screenshot manifest from the mounted docs repo.
 *
 * The same shape is enforced by the Zod schema in
 * `bifrost-integrations-docs/scripts/manifest/schema.mjs`. We don't import
 * Zod here to keep the playwright-runner image lean — light validation
 * inside the capture spec is enough; the docs repo's `npm run lint:manifest`
 * is where strict validation happens.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import yaml from "js-yaml";

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Callout extends Rect {
  color?: string;
  label?: string;
}

export interface MockSpec {
  url: string;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  status?: number;
  body?: unknown;
  fixture?: string;
}

/**
 * One UI action to perform after the page settles, before the screenshot.
 * Mirrors the docs repo's Zod schema (scripts/manifest/schema.mjs). Each
 * action is exactly one of these six shapes:
 *   - { click: "<selector>" }              → page.locator(selector).click()
 *   - { fill: { selector, value } }        → page.locator(selector).fill(value)
 *   - { wait_for: "<selector>" }           → page.locator(selector).waitFor({ state: 'visible' })
 *   - { wait_for_hidden: "<selector>" }    → page.locator(selector).waitFor({ state: 'hidden' })
 *   - { wait_ms: <number> }                → page.waitForTimeout(ms)
 *   - { scroll_into_view: "<selector>" }   → page.locator(selector).scrollIntoViewIfNeeded()
 */
export type ActionSpec =
  | { click: string }
  | { fill: { selector: string; value: string } }
  | { wait_for: string }
  | { wait_for_hidden: string }
  | { wait_ms: number }
  | { scroll_into_view: string }
  // Push a path via the SPA's history without a hard reload. Use after a
  // nav_via sidebar click to deep-link further (e.g. into /mcp-servers/<id>)
  // without re-triggering Vite proxy rules that hard navigation would hit.
  | { goto_spa: string };

export interface CaptureSpec {
  selector?: string;
  pad?: number;
  fullPage?: boolean;
  crop?: Rect;
  callouts?: Callout[];
  mocks?: MockSpec[];
  settle_ms?: number;
  actions?: ActionSpec[];
}

export interface Diataxis {
  page: string;
  type: "tutorial" | "how-to" | "reference" | "explanation";
  heading?: string;
}

export interface ManifestEntry {
  id: string;
  image: string;
  route: string;
  external?: boolean;
  auth_as?: "platform_admin" | "org1_user" | "org2_user" | "unauthenticated";
  seed?: string;
  viewport?: { width: number; height: number };
  capture?: CaptureSpec;
  // Optional SPA navigation. When set, the spec navigates to `from` first,
  // then clicks the named link/element to reach `route` via in-app routing.
  // Use this for routes that share a prefix with a Vite proxy rule (e.g.
  // /mcp-servers collides with the /mcp proxy in dev builds), where a hard
  // page.goto() would be intercepted before the SPA can handle it.
  nav_via?: { from: string; click: string };
  diataxis: Diataxis;
  source_globs?: string[];
  captured_at?: { bifrost_sha: string | null; timestamp: string | null } | null;
}

export interface Manifest {
  version: 1;
  defaults: {
    auth_as: string;
    viewport: { width: number; height: number };
    pad: number;
    settle_ms?: number;
    mocks?: MockSpec[];
    crop?: Rect;
  };
  entries: ManifestEntry[];
}

/**
 * Merge entry-specific mocks on top of manifest defaults. An entry mock with
 * the same `${method} ${url}` key overrides the default — useful when a
 * multi-step page needs a different fixture for the same endpoint.
 */
export function effectiveMocks(
  entry: ManifestEntry,
  manifest: Manifest,
): MockSpec[] {
  const defaultMocks = manifest.defaults.mocks ?? [];
  const entryMocks = entry.capture?.mocks ?? [];
  const byKey = new Map<string, MockSpec>();
  for (const m of defaultMocks) {
    byKey.set(`${m.method ?? "GET"} ${m.url}`, m);
  }
  for (const m of entryMocks) {
    byKey.set(`${m.method ?? "GET"} ${m.url}`, m);
  }
  return Array.from(byKey.values());
}

export function effectiveSettleMs(
  entry: ManifestEntry,
  manifest: Manifest,
): number {
  return entry.capture?.settle_ms ?? manifest.defaults.settle_ms ?? 500;
}

export function loadManifest(docsRepoPath: string): Manifest {
  const manifestPath = path.join(docsRepoPath, "screenshots.yaml");
  const raw = fs.readFileSync(manifestPath, "utf8");
  const parsed = yaml.load(raw) as Manifest | undefined;
  if (!parsed || typeof parsed !== "object" || parsed.version !== 1) {
    throw new Error(
      `Invalid manifest at ${manifestPath} — expected version: 1 at top level`,
    );
  }
  if (!Array.isArray(parsed.entries)) {
    throw new Error(`Invalid manifest at ${manifestPath} — missing entries[]`);
  }
  return parsed;
}

export function selectEntries(
  manifest: Manifest,
  ids: string[] | null,
): ManifestEntry[] {
  if (!ids || ids.length === 0) return manifest.entries;
  const wanted = new Set(ids);
  return manifest.entries.filter((e) => wanted.has(e.id));
}

export function effectiveViewport(
  entry: ManifestEntry,
  manifest: Manifest,
): { width: number; height: number } {
  return entry.viewport ?? manifest.defaults.viewport;
}

export function effectiveAuth(
  entry: ManifestEntry,
  manifest: Manifest,
): string {
  return entry.auth_as ?? manifest.defaults.auth_as;
}
