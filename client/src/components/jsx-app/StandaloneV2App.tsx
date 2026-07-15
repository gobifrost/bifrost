/**
 * StandaloneV2App mounts a standalone_v2 Solution app in the same document.
 *
 * The app's immutable Vite entry is loaded through its canonical URL. The
 * entry registers a reusable mount function, and the shell calls that function
 * once per host mount. This preserves browser ES-module identity across lazy
 * chunks while still giving every mount its own React root and bootstrap.
 */
import { useEffect, useRef, useState } from "react";

import { useOrgScope } from "@/hooks/useOrgScope";
import { clearAuthTokens, getActiveToken } from "@/lib/auth-token";

export interface BifrostAppBootstrap {
	/** Router basename so app URLs live below /apps/{slug}. */
	basename: string;
	/** Absolute API base for BifrostProvider. */
	baseUrl: string;
	/** Current viewer bearer token. */
	token: string;
	/** Active organization scope, or null for the caller's default. */
	orgScope: string | null;
	/** Installed app id used to scope portable workflow references. */
	appId: string;
	/** Ask the platform to log out. */
	onLogout: () => void;
	/** Platform theme at mount time. */
	theme: "light" | "dark";
}

export interface StandaloneV2Module {
	mount: (
		mountEl: HTMLElement,
		bootstrap: BifrostAppBootstrap,
	) => () => void;
}

interface LegacyBifrostAppBootstrap extends BifrostAppBootstrap {
	mountEl: HTMLElement;
	registerUnmount: (teardown: () => void) => void;
}

declare global {
	interface Window {
		/** Static module factories keyed by the canonical entry URL. */
		__BIFROST_APP_MODULES__?: Map<string, StandaloneV2Module>;
		/** Legacy side-effect entry transport. New apps do not read this. */
		__BIFROST_APP__?: LegacyBifrostAppBootstrap;
	}
}

export type StandaloneV2RuntimeContract = "mount-v1" | null;

const moduleLoads = new Map<string, Promise<StandaloneV2Module>>();
const loadedLegacyEntries = new Set<string>();
const activeLegacyEntries = new Set<string>();
let legacyLoadQueue: Promise<void> = Promise.resolve();
let activeLegacyLoadEntry: string | null = null;

interface PendingLegacyLoad {
	bootstrap: LegacyBifrostAppBootstrap;
	promise: Promise<LegacyLoadResult>;
}

const pendingLegacyLoads = new Map<string, PendingLegacyLoad>();

function registeredModule(entryUrl: string): StandaloneV2Module | undefined {
	return window.__BIFROST_APP_MODULES__?.get(entryUrl);
}

/**
 * Load a mount-v1 module with a real module script. Using import() here would
 * make the host's Vite dev transform append `?import`, splitting the module
 * identity from the canonical URLs emitted inside the app's own chunk graph.
 */
function loadMountModule(entryUrl: string): Promise<StandaloneV2Module> {
	const registered = registeredModule(entryUrl);
	if (registered) return Promise.resolve(registered);

	const pending = moduleLoads.get(entryUrl);
	if (pending) return pending;

	const load = new Promise<StandaloneV2Module>((resolve, reject) => {
		const script = document.createElement("script");
		script.type = "module";
		script.src = entryUrl;
		script.dataset.bifrostAppEntry = entryUrl;
		const cleanup = () => script.remove();
		script.onload = () => {
			cleanup();
			const appModule = registeredModule(entryUrl);
			if (!appModule) {
				reject(
					new Error(
						"The application declares the mount-v1 runtime but did not register a mount() function.",
					),
				);
				return;
			}
			resolve(appModule);
		};
		script.onerror = () => {
			cleanup();
			reject(new Error(`Failed to load the application entry: ${entryUrl}`));
		};
		document.head.appendChild(script);
	}).catch((error: unknown) => {
		moduleLoads.delete(entryUrl);
		throw error;
	});

	moduleLoads.set(entryUrl, load);
	return load;
}

type LegacyLoadResult =
	| { kind: "module"; appModule: StandaloneV2Module }
	| { kind: "legacy-loaded" }
	| { kind: "legacy-reload" }
	| { kind: "legacy-concurrent" };

/**
 * Old Apps v2 entries mount as a top-level side effect. Serialize their first
 * evaluation so a slow, cancelled entry can only observe its own tombstoned
 * bootstrap, never a newer app's live mount. Once a legacy entry has been
 * torn down it needs a fresh document module map, so re-entry reloads the page.
 */
function loadLegacyOrUnmarkedModule(
	entryUrl: string,
	bootstrap: LegacyBifrostAppBootstrap,
): Promise<LegacyLoadResult> {
	const existing = pendingLegacyLoads.get(entryUrl);
	if (existing) {
		if (existing.bootstrap.mountEl.isConnected) {
			return Promise.resolve({ kind: "legacy-concurrent" });
		}
		// React development StrictMode performs setup → cleanup → setup before a
		// pending module script executes. Transfer that one in-flight legacy load
		// to the current mount; distinct entry URLs remain serialized below.
		existing.bootstrap = bootstrap;
		if (activeLegacyLoadEntry === entryUrl) {
			window.__BIFROST_APP__ = bootstrap;
		}
		return existing.promise;
	}

	const pending: PendingLegacyLoad = {
		bootstrap,
		promise: Promise.resolve({ kind: "legacy-loaded" }),
	};
	const task = legacyLoadQueue.then(async (): Promise<LegacyLoadResult> => {
		const appModule = registeredModule(entryUrl);
		if (appModule) return { kind: "module", appModule };

		if (loadedLegacyEntries.has(entryUrl)) {
			return activeLegacyEntries.has(entryUrl)
				? { kind: "legacy-concurrent" }
				: { kind: "legacy-reload" };
		}

		activeLegacyLoadEntry = entryUrl;
		window.__BIFROST_APP__ = pending.bootstrap;
		await new Promise<void>((resolve, reject) => {
			const script = document.createElement("script");
			script.type = "module";
			script.src = entryUrl;
			script.dataset.bifrostLegacyAppEntry = entryUrl;
			const cleanup = () => script.remove();
			script.onload = () => {
				cleanup();
				resolve();
			};
			script.onerror = () => {
				cleanup();
				reject(new Error(`Failed to load the legacy application entry: ${entryUrl}`));
			};
			document.head.appendChild(script);
		});

		const registeredAfterLoad = registeredModule(entryUrl);
		if (registeredAfterLoad) {
			return { kind: "module", appModule: registeredAfterLoad };
		}
		loadedLegacyEntries.add(entryUrl);
		return { kind: "legacy-loaded" };
	}).finally(() => {
		if (activeLegacyLoadEntry === entryUrl) activeLegacyLoadEntry = null;
		if (pendingLegacyLoads.get(entryUrl) === pending) {
			pendingLegacyLoads.delete(entryUrl);
		}
	});

	pending.promise = task;
	pendingLegacyLoads.set(entryUrl, pending);
	legacyLoadQueue = task.then(
		() => undefined,
		() => undefined,
	);
	return task;
}

interface StandaloneV2AppProps {
	appId: string;
	appSlug: string;
	isPreview: boolean;
	entry: string;
	css: string | null;
	baseUrl: string;
	appOrgId: string | null;
	runtimeContract: StandaloneV2RuntimeContract;
}

export function StandaloneV2App({
	appId,
	appSlug,
	isPreview,
	entry,
	css,
	baseUrl,
	appOrgId,
	runtimeContract,
}: StandaloneV2AppProps) {
	const containerRef = useRef<HTMLDivElement>(null);
	const [loadError, setLoadError] = useState<string | null>(null);
	const { scope } = useOrgScope();

	const token = getActiveToken();
	const error = token ? loadError : "Not authenticated — cannot mount the application.";

	useEffect(() => {
		const mountEl = containerRef.current;
		if (!mountEl || !token) return;

		setLoadError(null);
		const basename = isPreview
			? `/apps/${appSlug}/preview`
			: `/apps/${appSlug}`;
		const orgScope =
			appOrgId ?? (scope.type === "organization" ? scope.orgId : null);
		const entryUrl = new URL(`${baseUrl}/${entry}`, window.location.origin).href;
		mountEl.dataset.bifrostEntry = entryUrl;

		let cssEl: HTMLLinkElement | null = null;
		if (css) {
			cssEl = document.createElement("link");
			cssEl.rel = "stylesheet";
			cssEl.href = new URL(`${baseUrl}/${css}`, window.location.origin).href;
			document.head.appendChild(cssEl);
		}

		let cancelled = false;
		let appTeardown: (() => void) | null = null;
		const bootstrap: BifrostAppBootstrap = {
			basename,
			baseUrl: window.location.origin,
			token,
			orgScope,
			appId,
			onLogout: () => {
				clearAuthTokens();
				window.location.assign("/login");
			},
			theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
		};

		const reportError = (value: unknown) => {
			if (!cancelled) {
				setLoadError(
					value instanceof Error ? value.message : "Failed to load the application.",
				);
			}
		};
		const mountModule = (appModule: StandaloneV2Module) => {
			if (cancelled) return;
			const teardown = appModule.mount(mountEl, bootstrap);
			if (typeof teardown !== "function") {
				throw new Error("The application's mount() function must return an unmount function.");
			}
			appTeardown = teardown;
		};

		let legacyBootstrap: LegacyBifrostAppBootstrap | null = null;
		if (runtimeContract === "mount-v1") {
			loadMountModule(entryUrl).then(mountModule).catch(reportError);
		} else {
			legacyBootstrap = {
				...bootstrap,
				mountEl,
				registerUnmount: (teardown: () => void) => {
					appTeardown = teardown;
				},
			};
			loadLegacyOrUnmarkedModule(entryUrl, legacyBootstrap)
				.then((result) => {
					if (cancelled) return;
					if (result.kind === "module") {
						mountModule(result.appModule);
					} else if (result.kind === "legacy-loaded") {
						activeLegacyEntries.add(entryUrl);
					} else if (result.kind === "legacy-reload") {
						window.location.reload();
					} else {
						throw new Error(
							"This legacy Apps v2 entry cannot be mounted concurrently. Redeploy it with the mount-v1 lifecycle.",
						);
					}
				})
				.catch(reportError);
		}

		return () => {
			cancelled = true;
			cssEl?.remove();
			activeLegacyEntries.delete(entryUrl);
			try {
				appTeardown?.();
			} catch {
				// The root is detached below even if application teardown throws.
			}

			if (legacyBootstrap) {
				legacyBootstrap.mountEl = document.createElement("div");
				legacyBootstrap.registerUnmount = (teardown: () => void) => {
					try {
						teardown();
					} catch {
						// A late legacy root is already isolated in a detached node.
					}
				};
			}
			mountEl.replaceChildren();
		};
	}, [
		appId,
		appSlug,
		isPreview,
		entry,
		css,
		baseUrl,
		appOrgId,
		runtimeContract,
		scope,
		token,
	]);

	if (error) {
		return (
			<div className="flex h-full w-full items-center justify-center p-6">
				<pre className="max-w-xl whitespace-pre-wrap text-sm text-destructive">
					{error}
				</pre>
			</div>
		);
	}

	return (
		<div
			ref={containerRef}
			className="h-full w-full"
			data-testid="solution-v2-app-root"
		/>
	);
}
