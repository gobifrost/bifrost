/**
 * Bundled App Shell — loads an app via esbuild-produced bundle.
 *
 * Fetches /api/applications/{id}/bundle-manifest, then dynamically imports
 * the entry module and mounts its default export inline so the bundled
 * subtree inherits the host SPA's context providers.
 *
 * This is the "normal React app" path — the bundle is a real ES module with
 * real source maps, real component names in DevTools, and browser-level caching.
 *
 * Resolving bare imports inside the bundle:
 * - Platform externals (react, react-dom, react-router-dom, lucide-react,
 *   react/jsx-runtime, react/jsx-dev-runtime, react-dom/client) resolve via
 *   the static import map in `client/index.html` to small stubs that read
 *   from `globalThis.__bifrost_*` populated by `initReactShim()` at boot.
 * - User-declared dependencies (Application.dependencies) only have map
 *   entries when the app actually has user deps. Since we can't append to
 *   the static map, we lazy-load es-module-shims and use shim mode for
 *   the dynamic import — shim mode supports late importmap registration.
 *   Apps with no user deps pay zero polyfill cost.
 */

import type * as React from "react";
import { useEffect, useState } from "react";
import { authFetch } from "@/lib/api-client";
import { setDefaultAppScope } from "@/lib/app-sdk/tables";
import {
	webSocketService,
	type AppCodeFileUpdate,
	type BundleMessage,
} from "@/services/websocket";
import { useAppBuilderStore } from "@/stores/app-builder.store";
import { AppLoadingSkeleton } from "./AppLoadingSkeleton";

// jsDelivr — JSPM's CDN 404s on floating tags (`@2`), only exact versions
// resolve. Pinned to an exact version for reproducible loads.
const ESM_SHIMS_URL = "https://cdn.jsdelivr.net/npm/es-module-shims@2.8.0/dist/es-module-shims.js";
// Force esm.sh to leave React/Router as bare specifiers in its own response so
// they resolve back through our static import map to the host's copies. Same
// instance everywhere -> no "two Reacts" hooks failure when a user dep calls
// useContext/useState.
const REACT_EXTERNALS =
	"react,react-dom,react-dom/client,react/jsx-runtime,react-router-dom";

// Browser-side shape that es-module-shims adds to window once loaded.
interface ImportShimWindow {
	importShim?: (specifier: string) => Promise<unknown>;
	esmsInitOptions?: { shimMode?: boolean };
}

let esModuleShimsPromise: Promise<void> | null = null;
function ensureEsModuleShimsLoaded(): Promise<void> {
	if (esModuleShimsPromise) return esModuleShimsPromise;
	esModuleShimsPromise = new Promise<void>((resolve, reject) => {
		const w = window as unknown as ImportShimWindow;
		if (typeof w.importShim === "function") {
			resolve();
			return;
		}
		// shimMode: true so the shim handles ALL module loads it can see —
		// including <script type="importmap-shim"> entries we register below
		// for user deps. Native modules continue to load natively unless they
		// reference a shim-mode specifier.
		w.esmsInitOptions = { shimMode: true };
		const script = document.createElement("script");
		script.async = true;
		script.src = ESM_SHIMS_URL;
		script.onload = () => resolve();
		script.onerror = () =>
			reject(new Error(`Failed to load es-module-shims from ${ESM_SHIMS_URL}`));
		document.head.appendChild(script);
	});
	return esModuleShimsPromise;
}

// Track which user-dep maps we've already registered. The shim accepts late
// registration but we don't need to keep adding the same entries.
const registeredUserDepMaps = new Set<string>();

function registerUserDepImportMap(dependencies: Record<string, string>): void {
	if (Object.keys(dependencies).length === 0) return;

	// Always include the platform keys in the shim-mode map too — shim-mode
	// modules can't see the native importmap, so they need their own copy.
	const imports: Record<string, string> = {
		"react": "/__bifrost_modules/react.js",
		"react-dom": "/__bifrost_modules/react-dom.js",
		"react-dom/client": "/__bifrost_modules/react-dom-client.js",
		"react/jsx-runtime": "/__bifrost_modules/react-jsx-runtime.js",
		"react/jsx-dev-runtime": "/__bifrost_modules/react-jsx-dev-runtime.js",
		"react-router-dom": "/__bifrost_modules/react-router-dom.js",
		"lucide-react": "/__bifrost_modules/lucide-react.js",
	};
	for (const [name, version] of Object.entries(dependencies)) {
		imports[name] = `https://esm.sh/${name}@${version}?external=${REACT_EXTERNALS}`;
	}

	const key = JSON.stringify(imports);
	if (registeredUserDepMaps.has(key)) return;
	registeredUserDepMaps.add(key);

	const script = document.createElement("script");
	script.type = "importmap-shim";
	script.textContent = JSON.stringify({ imports });
	document.head.appendChild(script);
}

interface BundleManifest {
	entry: string;
	css: string | null;
	base_url: string;
	mode: "preview" | "live";
	dependencies: Record<string, string>;
	// Server set to true iff the first-view auto-migration rewrote files
	// under _repo/<app>/. Surfaced as a dismissible info banner so the
	// developer knows to pull on next workspace sync.
	migrated?: boolean;
	// Org-scoped apps carry their organization id; global apps have null.
	// Mirrors how org-scoped workflows always run as their org regardless
	// of who triggered them.
	organization_id?: string | null;
}

interface BundledAppShellProps {
	appId: string;
	appSlug: string;
	isPreview: boolean;
}

// React component type exported by the bundled entry.
type BundledAppComponent = React.ComponentType<Record<string, never>>;

export function BundledAppShell({ appId, appSlug, isPreview }: BundledAppShellProps) {
	// The bundle's default export is a React component. We render it INLINE
	// via React.createElement so the bundled subtree inherits all of the
	// host's context providers (AuthContext, QueryClientProvider, theme, etc.).
	// Earlier we used `createRoot(container).render(...)` inside the bundle's
	// `mount()` — that created a sibling root with no provider inheritance
	// and broke every hook that read host context (e.g. useUser → useAuth).
	const [BundledApp, setBundledApp] = useState<BundledAppComponent | null>(null);
	const [loadedEntry, setLoadedEntry] = useState<string | null>(null);
	const [cssHref, setCssHref] = useState<string | null>(null);
	// Org-scoped app: tells the table SDK to default `scope` to the app's
	// org for `tables.*` and `useTable` calls inside the bundle. Captured
	// from the first successful manifest fetch.
	const [appOrgId, setAppOrgId] = useState<string | null>(null);

	const [loadError, setLoadError] = useState<string | null>(null);
	// Build errors from hot-reload rebuilds. The last-good bundle keeps
	// rendering underneath; this banner sits on top.
	const [buildErrors, setBuildErrors] = useState<BundleMessage[] | null>(null);
	const [buildErrorDismissed, setBuildErrorDismissed] = useState(false);

	// Auto-migration notice shown when the first-view bundle-manifest fetch
	// reports that server-side migrate-imports rewrote files under _repo/.
	// Persisted-dismissed via localStorage so it doesn't re-appear on every
	// navigation within the same app.
	const migrateDismissKey = `bifrost.automigrate-dismissed.${appId}`;
	const [migrateNotice, setMigrateNotice] = useState(false);
	const [migrateNoticeDismissed, setMigrateNoticeDismissed] = useState(
		() => {
			try {
				return localStorage.getItem(migrateDismissKey) === "1";
			} catch {
				return false;
			}
		},
	);

	const setAppContext = useAppBuilderStore((state) => state.setAppContext);

	// Populate the app-builder store so platform wrappers (Link/NavLink/etc)
	// know the app's base path when they transform `to` props.
	useEffect(() => {
		setAppContext(appSlug, isPreview);
		return () => setAppContext("", false);
	}, [appSlug, isPreview, setAppContext]);

	// Install the app's org as the default scope for table SDK calls. The
	// returned cleanup restores the prior value, so navigating between apps
	// (or to a non-app page) flips the default back. Mirrors how org-scoped
	// workflows always run as their org regardless of caller.
	useEffect(() => {
		if (appOrgId === null) return;
		const restore = setDefaultAppScope(appOrgId);
		return restore;
	}, [appOrgId]);

	// Load-or-reload the bundle. Called on initial mount AND on every
	// successful rebuild pubsub event. Setting the component state triggers
	// React to re-render with the new bundle — the host provider tree stays
	// intact so every context provider is reachable from inside the bundle.
	useEffect(() => {
		const controller = new AbortController();

		async function loadBundle(entryOverride?: string, cssOverride?: string | null) {
			try {
				const mode = isPreview ? "draft" : "live";
				let entry: string;
				let css: string | null;
				let baseUrl: string;
				let dependencies: Record<string, string>;

				if (entryOverride !== undefined) {
					// Hot-reload path — skip re-fetching the manifest.
					entry = entryOverride;
					css = cssOverride ?? null;
					baseUrl = `/api/applications/${appId}/bundle-asset`;
					dependencies = {};
				} else {
					setLoadError(null);

					const resp = await authFetch(
						`/api/applications/${appId}/bundle-manifest?mode=${mode}`,
						{ signal: controller.signal },
					);
					if (!resp.ok) {
						const txt = await resp.text();
						throw new Error(`Bundle manifest fetch failed: ${resp.status} ${txt}`);
					}
					const manifest: BundleManifest = await resp.json();
					entry = manifest.entry;
					css = manifest.css;
					baseUrl = manifest.base_url;
					dependencies = manifest.dependencies ?? {};

					// Server may have run migrate-imports against _repo/<app>/
					// before bundling. Surface a non-fatal info banner so the
					// developer pulls on next sync.
					if (manifest.migrated) {
						setMigrateNotice(true);
					}

					// Capture the org for table-SDK scoping. Org-scoped apps
					// default `scope` to this value; global apps leave it null
					// and fall back to the caller's-org behavior.
					setAppOrgId(manifest.organization_id ?? null);
				}

				if (controller.signal.aborted) return;
				if (loadedEntry === entry) return;

				const entryUrl = `${baseUrl}/${entry}?mode=${mode}`;
				const nextCssHref = css ? `${baseUrl}/${css}?mode=${mode}` : null;

				// User-dep apps go through es-module-shims so that the user-dep
				// importmap can be registered after page load. Apps with only
				// platform externals use the native dynamic import, which
				// resolves through the static map in index.html.
				const hasUserDeps = Object.keys(dependencies).length > 0;
				let dynamicImport: (url: string) => Promise<{ default?: unknown }>;
				if (hasUserDeps) {
					await ensureEsModuleShimsLoaded();
					registerUserDepImportMap(dependencies);
					const w = window as unknown as ImportShimWindow;
					if (typeof w.importShim !== "function") {
						throw new Error("es-module-shims loaded but importShim is undefined");
					}
					const importShim = w.importShim;
					dynamicImport = (url) =>
						importShim(url) as Promise<{ default?: unknown }>;
				} else {
					dynamicImport = (url) =>
						import(/* @vite-ignore */ url) as Promise<{ default?: unknown }>;
				}

				// Load JS and CSS in parallel, but don't commit either until
				// BOTH have resolved — otherwise the component renders for a
				// tick before the <link> attaches and we get a FOUC.
				const [module] = await Promise.all([
					dynamicImport(entryUrl),
					nextCssHref ? preloadStylesheet(nextCssHref, controller.signal) : Promise.resolve(),
				]);

				if (controller.signal.aborted) return;

				if (typeof module.default !== "function") {
					throw new Error(
						"Bundle does not have a default export (expected a React component)",
					);
				}

				setCssHref(nextCssHref);
				setBundledApp(() => module.default as BundledAppComponent);
				setLoadedEntry(entry);

				// Successful reload — clear any prior build-error banner.
				setBuildErrors(null);
				setBuildErrorDismissed(false);
			} catch (err) {
				if (controller.signal.aborted) return;
				// LOAD error vs BUILD error: if we've never loaded a bundle,
				// show a full-screen error; otherwise it's a failed hot-reload
				// and we surface it via the banner while keeping last-good live.
				if (!BundledApp) {
					setLoadError(err instanceof Error ? err.message : String(err));
				} else {
					setBuildErrors([
						{
							text: err instanceof Error ? err.message : String(err),
							file: null,
							line: null,
							column: null,
							line_text: null,
						},
					]);
					setBuildErrorDismissed(false);
				}
			}
		}

		loadBundle();

		// Preview-only: subscribe to draft bundle updates for this app.
		// Success → reload entry. Failure → show banner over last-good render.
		let unsub: (() => void) | null = null;
		if (isPreview) {
			(async () => {
				try {
					await webSocketService.connectToAppDraft(appId);
					unsub = webSocketService.onAppCodeFileUpdate(
						appId,
						(update: AppCodeFileUpdate) => {
							if (update.error && update.error.messages.length > 0) {
								setBuildErrors(update.error.messages);
								setBuildErrorDismissed(false);
							} else if (update.bundle) {
								loadBundle(update.bundle.entry, update.bundle.css);
							}
						},
					);
				} catch (e) {
					console.warn("[Bifrost] Failed to subscribe to app updates:", e);
				}
			})();
		}

		return () => {
			controller.abort();
			if (unsub) unsub();
		};
		// We intentionally omit loadedEntry / BundledApp from deps — those
		// are updated from inside this effect and would cause a cycle.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [appId, appSlug, isPreview]);

	if (loadError) {
		return (
			<div className="flex items-center justify-center h-full min-h-[200px] p-4">
				<div className="p-6 bg-red-50 dark:bg-red-950/20 border border-red-200 dark:border-red-800 rounded-lg max-w-lg">
					<h2 className="text-lg font-semibold text-red-700 dark:text-red-400">
						Bundle Load Error
					</h2>
					<pre className="mt-3 p-3 bg-red-100 dark:bg-red-900/30 rounded text-sm text-red-800 dark:text-red-200 overflow-auto whitespace-pre-wrap">
						{loadError}
					</pre>
				</div>
			</div>
		);
	}

	const showBanner =
		buildErrors && buildErrors.length > 0 && !buildErrorDismissed;
	const showMigrateNotice = migrateNotice && !migrateNoticeDismissed;

	return (
		<div className="relative h-full w-full">
			{cssHref && <BundleStyles href={cssHref} />}
			{BundledApp ? (
				<BundledApp />
			) : (
				<AppLoadingSkeleton message="Loading application..." />
			)}
			{showBanner && buildErrors && (
				<BuildErrorBanner
					errors={buildErrors}
					onDismiss={() => setBuildErrorDismissed(true)}
				/>
			)}
			{showMigrateNotice && (
				<AutoMigrateNotice
					onDismiss={() => {
						setMigrateNoticeDismissed(true);
						try {
							localStorage.setItem(migrateDismissKey, "1");
						} catch {
							/* ignore */
						}
					}}
				/>
			)}
		</div>
	);
}

/**
 * Warm the browser cache for a stylesheet before we mount the bundled
 * component, so the <link> that BundleStyles appends applies on first paint.
 */
function preloadStylesheet(href: string, signal: AbortSignal): Promise<void> {
	return new Promise((resolve, reject) => {
		const el = document.createElement("link");
		el.rel = "preload";
		el.as = "style";
		el.href = href;
		const cleanup = () => {
			el.remove();
			signal.removeEventListener("abort", onAbort);
		};
		const onAbort = () => {
			cleanup();
			reject(new DOMException("Aborted", "AbortError"));
		};
		el.onload = () => {
			cleanup();
			resolve();
		};
		el.onerror = () => {
			cleanup();
			// Non-fatal — let the bundle render even if CSS fails so the user
			// sees *something* instead of a hang.
			resolve();
		};
		if (signal.aborted) {
			onAbort();
			return;
		}
		signal.addEventListener("abort", onAbort);
		document.head.appendChild(el);
	});
}

/**
 * Inject a <link> stylesheet into the document head and remove it on cleanup.
 * Rendered as a React component so it participates in the normal lifecycle.
 */
function BundleStyles({ href }: { href: string }) {
	useEffect(() => {
		const el = document.createElement("link");
		el.rel = "stylesheet";
		el.href = href;
		el.dataset.bifrostBundle = "true";
		document.head.appendChild(el);
		return () => {
			el.remove();
		};
	}, [href]);
	return null;
}

/**
 * Dismissible info banner shown once per app after server-side auto-migration.
 * Blue/gray info styling — this is not an error. Same structural shape as
 * BuildErrorBanner so the two stack predictably in the top-right.
 */
function AutoMigrateNotice({ onDismiss }: { onDismiss: () => void }) {
	return (
		<div className="absolute top-3 right-3 left-3 z-50 rounded-lg border border-blue-300 bg-blue-50 shadow-lg dark:border-blue-700 dark:bg-blue-950/90">
			<div className="flex items-start gap-3 p-3">
				<div className="flex-1">
					<div className="mb-1 flex items-center justify-between">
						<h3 className="text-sm font-semibold text-blue-700 dark:text-blue-300">
							App updated for new runtime
						</h3>
						<button
							type="button"
							onClick={onDismiss}
							className="text-blue-600 hover:text-blue-800 dark:text-blue-300 dark:hover:text-blue-100"
							aria-label="Dismiss"
						>
							×
						</button>
					</div>
					<p className="text-sm text-blue-800 dark:text-blue-200">
						Your app was automatically updated to the new runtime. Review the
						changes in your workspace on your next{" "}
						<code className="rounded bg-blue-100 px-1 dark:bg-blue-900/60">
							bifrost pull
						</code>
						.
					</p>
				</div>
			</div>
		</div>
	);
}

/**
 * Dismissible banner shown over the last-good bundle when a rebuild fails.
 * The underlying bundle keeps rendering so the user can navigate around and
 * see what they just broke without losing state.
 */
function BuildErrorBanner({
	errors,
	onDismiss,
}: {
	errors: BundleMessage[];
	onDismiss: () => void;
}) {
	return (
		<div className="absolute top-3 right-3 left-3 z-50 rounded-lg border border-red-300 bg-red-50 shadow-lg dark:border-red-700 dark:bg-red-950/90">
			<div className="flex items-start gap-3 p-3">
				<div className="flex-1">
					<div className="mb-1 flex items-center justify-between">
						<h3 className="text-sm font-semibold text-red-700 dark:text-red-300">
							Build failed — showing last good bundle
						</h3>
						<button
							type="button"
							onClick={onDismiss}
							className="text-red-600 hover:text-red-800 dark:text-red-300 dark:hover:text-red-100"
							aria-label="Dismiss"
						>
							×
						</button>
					</div>
					<ul className="space-y-1 text-sm text-red-800 dark:text-red-200">
						{errors.slice(0, 5).map((e, i) => (
							<li key={i} className="font-mono">
								{e.file && (
									<span className="font-semibold">
										{e.file}
										{e.line !== null ? `:${e.line}` : ""}
										{e.column !== null ? `:${e.column}` : ""}
										{" — "}
									</span>
								)}
								<span>{e.text}</span>
							</li>
						))}
						{errors.length > 5 && (
							<li className="italic">… and {errors.length - 5} more</li>
						)}
					</ul>
				</div>
			</div>
		</div>
	);
}
