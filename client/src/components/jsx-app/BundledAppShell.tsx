/**
 * Bundled App Shell — loads an app via esbuild-produced bundle.
 *
 * Fetches /api/applications/{id}/bundle-manifest, populates globalThis.$bifrost
 * with the platform scope so the bundle's `import { X } from "bifrost"` resolves,
 * then dynamically imports the entry module and calls its mount() export.
 *
 * This is the "normal React app" path — the bundle is a real ES module with
 * real source maps, real component names in DevTools, and browser-level caching.
 */

import { useEffect, useState } from "react";
import { authFetch } from "@/lib/api-client";
import { $ as platformScope } from "@/lib/app-code-runtime";
import {
	webSocketService,
	type AppCodeFileUpdate,
	type BundleMessage,
} from "@/services/websocket";
import { useAppBuilderStore } from "@/stores/app-builder.store";
import { AppLoadingSkeleton } from "./AppLoadingSkeleton";

/**
 * Set the platform-scope global the bundler reads at runtime.
 *
 * The bundler synthesizes a `node_modules/bifrost/index.js` whose body emits
 * `globalThis.__bifrost_platform[...]` accesses. We populate that global
 * here. (The shared-host React/Router/Lucide modules are wired up at app
 * startup by `initReactShim` and live in a single import map; this module
 * only needs to ensure the platform-scope global is in place plus an
 * import-map entry for any per-app npm deps.)
 */
function setPlatformScope(): void {
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	(globalThis as any).__bifrost_platform = platformScope;
}

/**
 * Ensure user-declared npm dependencies resolve via esm.sh in the browser.
 *
 * The host shim's import map already covers React, React Router, Lucide,
 * and the JSX runtimes; this function only writes a SECOND map for
 * per-app user dependencies (e.g. `recharts`, `react-quill-new`). No
 * specifier overlaps with the shim's map, so the browser merges both maps
 * cleanly without "rule was removed, conflicted with an existing rule"
 * warnings.
 *
 * Import maps are immutable once a module that matches has been resolved,
 * so if the user's deps change between apps within the same page-lifetime,
 * we trigger a full reload — the next load installs the deps map from the
 * start.
 */
function ensureUserDepsImportMap(dependencies: Record<string, string>): void {
	const depKeys = Object.keys(dependencies);
	if (depKeys.length === 0) return;

	const existing = document.querySelector<HTMLScriptElement>(
		"script[data-bifrost-deps-import-map]",
	);
	if (existing) {
		try {
			const current = JSON.parse(existing.textContent || "{}");
			const missing = depKeys.filter((k) => !current.imports?.[k]);
			if (missing.length > 0) {
				location.reload();
			}
		} catch {
			/* ignore */
		}
		return;
	}

	// esm.sh bundles its own copy of React by default, which produces a
	// dual-React hazard: libraries like recharts call useContext from their
	// bundled React, but host context providers live in the host's React —
	// null mismatch. `?external=...` tells esm.sh to leave those specifiers
	// as bare imports at runtime, which the host shim's import map then
	// points at the host's shared copies.
	const REACT_EXTERNALS =
		"react,react-dom,react-dom/client,react/jsx-runtime,react-router-dom";
	const userDepImports: Record<string, string> = {};
	for (const [name, version] of Object.entries(dependencies)) {
		userDepImports[name] = `https://esm.sh/${name}@${version}?external=${REACT_EXTERNALS}`;
	}

	const scriptEl = document.createElement("script");
	scriptEl.type = "importmap";
	scriptEl.dataset.bifrostDepsImportMap = "true";
	scriptEl.textContent = JSON.stringify({ imports: userDepImports });
	// Import maps must be inserted before any module scripts that use them.
	document.head.insertBefore(scriptEl, document.head.firstChild);
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
				}

				// Make sure the platform-scope global is populated and any
				// user-declared npm deps are reachable via esm.sh. The shared
				// host modules (React, Router, Lucide, JSX runtimes) live in
				// the import map installed by initReactShim at startup.
				setPlatformScope();
				ensureUserDepsImportMap(dependencies);

				if (controller.signal.aborted) return;
				if (loadedEntry === entry) return;

				const entryUrl = `${baseUrl}/${entry}?mode=${mode}`;
				const nextCssHref = css ? `${baseUrl}/${css}?mode=${mode}` : null;

				// Load JS and CSS in parallel, but don't commit either until
				// BOTH have resolved — otherwise the component renders for a
				// tick before the <link> attaches and we get a FOUC.
				const [module] = await Promise.all([
					// @vite-ignore: runtime URL, don't try to resolve at build time.
					import(/* @vite-ignore */ entryUrl),
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

		// Subscribe to bundle updates for this app. Success → reload entry.
		// Failure → show banner over last-good render.
		let unsub: (() => void) | null = null;
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
