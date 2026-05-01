const RELOAD_KEY = "bifrost:last-preload-reload";
const LOOP_GUARD_MS = 5_000;

/**
 * Handle a vite:preloadError by reloading, with a sessionStorage loop guard
 * so a chronically broken deploy can't trap us in a reload tornado.
 *
 * Exported as a named function (not bound directly to addEventListener)
 * so tests can call it with mocked sessionStorage / location.
 */
export function handleVitePreloadError(): void {
	const lastReload = sessionStorage.getItem(RELOAD_KEY);
	const now = Date.now();
	if (lastReload && now - Number(lastReload) < LOOP_GUARD_MS) {
		// Already reloaded within the last 5s — don't loop. The version banner
		// will surface the version mismatch on the next poll cycle.
		console.error("[bifrost] preload error after recent reload, suppressing");
		return;
	}
	sessionStorage.setItem(RELOAD_KEY, String(now));
	window.location.reload();
}
