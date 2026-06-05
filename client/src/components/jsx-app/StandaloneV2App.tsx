/**
 * StandaloneV2App — mounts a standalone_v2 Solution app in the SAME document at
 * /apps/{slug} (NOT an iframe).
 *
 * Why not an iframe: an iframe at /api/.../dist/index.html keeps the OUTER
 * browser URL pinned to /apps/{slug} while the app navigates internally, so the
 * app's routes never reach the address bar — refresh, deep-links and bookmarks
 * all break (Codex P1-b/G7). Mounting in the same document means the real URL is
 * /apps/{slug}/whatever, so the app's <BrowserRouter basename="/apps/{slug}">
 * matches window.location and deep-links work.
 *
 * The app is a normal Vite build: its bundled entry is side-effecting — on
 * import it runs its OWN createRoot into the element we hand it via
 * `window.__BIFROST_APP__`, with its OWN React (vite-bundled). That's an
 * independent root, which is correct for v2: a v2 app brings its own
 * React/Router/Provider and does NOT inherit host context (unlike the v1 inline
 * path). The host supplies auth + basename through the injected config because
 * the per-viewer token can't be baked into a shared build, and the slug is
 * runtime-only (an app can be renamed).
 */
import { useEffect, useRef, useState } from "react";

import { clearAuthTokens, getActiveToken } from "@/lib/auth-token";
import { useOrgScope } from "@/hooks/useOrgScope";

/**
 * The runtime contract a v2 app's `main.tsx` reads. The platform sets this on
 * `window` BEFORE the entry module is imported; the app uses it to wire its
 * createRoot mount node, router basename, and `<BifrostProvider>` auth.
 */
export interface BifrostAppBootstrap {
	/** The element the app should `createRoot()` into. */
	mountEl: HTMLElement;
	/** Router basename so the app's URLs are `/apps/{slug}/...` (or /preview). */
	basename: string;
	/** Absolute API base (same-origin host) for BifrostProvider. */
	baseUrl: string;
	/** The current viewer's bearer token. */
	token: string;
	/** Active org scope (UUID) or null for the caller's default. */
	orgScope: string | null;
	/** Ask the platform to log out (the app may expose a logout affordance). */
	onLogout: () => void;
	/**
	 * The app MUST call this right after `createRoot(...)` with a teardown fn
	 * (`() => root.unmount()`). The shell invokes it when the user navigates
	 * away, so the app's React root, effects, timers, and websocket
	 * subscriptions are actually torn down — replacing DOM nodes alone leaks
	 * them (Codex R4). A well-behaved scaffold always registers this.
	 */
	registerUnmount: (teardown: () => void) => void;
}

declare global {
	interface Window {
		__BIFROST_APP__?: BifrostAppBootstrap;
	}
}

interface StandaloneV2AppProps {
	appId: string;
	appSlug: string;
	isPreview: boolean;
	/** Hashed entry chunk (relative to the dist base), from the bundle manifest. */
	entry: string;
	/** Hashed CSS file (relative to the dist base), if any. */
	css: string | null;
	/** The dist base URL, e.g. `/api/applications/{id}/dist`. */
	baseUrl: string;
	/** App org scope from the manifest (null for global apps). */
	appOrgId: string | null;
}

export function StandaloneV2App({
	appId,
	appSlug,
	isPreview,
	entry,
	css,
	baseUrl,
	appOrgId,
}: StandaloneV2AppProps) {
	const containerRef = useRef<HTMLDivElement>(null);
	const [loadError, setLoadError] = useState<string | null>(null);
	const { scope } = useOrgScope();

	// Read synchronously at render so the unauthenticated case is a derived
	// render state, not an effect side effect.
	const token = getActiveToken();
	const error = token ? loadError : "Not authenticated — cannot mount the application.";

	useEffect(() => {
		const mountEl = containerRef.current;
		if (!mountEl || !token) return;

		// The app routes under /apps/{slug} (and /apps/{slug}/preview in preview),
		// so its basename matches the real URL and deep-links resolve.
		const basename = isPreview
			? `/apps/${appSlug}/preview`
			: `/apps/${appSlug}`;
		// Prefer the app's own org (org-scoped apps), else the active platform
		// scope, else the caller's default.
		const orgScope =
			appOrgId ?? (scope.type === "organization" ? scope.orgId : null);

		const mode = isPreview ? "draft" : "live";
		const entryUrl = `${baseUrl}/${entry}?mode=${mode}`;
		let cssEl: HTMLLinkElement | null = null;
		if (css) {
			cssEl = document.createElement("link");
			cssEl.rel = "stylesheet";
			cssEl.href = `${baseUrl}/${css}?mode=${mode}`;
			document.head.appendChild(cssEl);
		}

		// The app calls registerUnmount(() => root.unmount()) after createRoot; we
		// invoke it on cleanup so the app's React root (effects/timers/sockets) is
		// actually torn down, not just detached from the DOM.
		let appTeardown: (() => void) | null = null;

		window.__BIFROST_APP__ = {
			mountEl,
			basename,
			baseUrl: window.location.origin,
			token,
			orgScope,
			onLogout: () => {
				clearAuthTokens();
				window.location.assign("/login");
			},
			registerUnmount: (teardown: () => void) => {
				appTeardown = teardown;
			},
		};

		let cancelled = false;
		// Side-effecting import: the app's entry runs its own createRoot(mountEl).
		import(/* @vite-ignore */ entryUrl).catch((e: unknown) => {
			if (!cancelled) {
				setLoadError(
					e instanceof Error ? e.message : "Failed to load the application.",
				);
			}
		});

		return () => {
			cancelled = true;
			cssEl?.remove();
			// Unmount the app's own React root (best-effort — a well-behaved app
			// registers it; falls back to detaching the DOM).
			try {
				appTeardown?.();
			} catch {
				// app teardown threw — still detach below; nothing else to do.
			}
			// Clear the bootstrap so a later mount can't read a stale element.
			if (window.__BIFROST_APP__?.mountEl === mountEl) {
				delete window.__BIFROST_APP__;
			}
			if (mountEl) mountEl.replaceChildren();
		};
		// appId is stable per mount; re-run only if the served entry changes.
	}, [appId, appSlug, isPreview, entry, css, baseUrl, appOrgId, scope, token]);

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
