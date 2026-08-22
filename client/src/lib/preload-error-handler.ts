import { requestApplicationReload } from "./application-update";

const RELOAD_KEY = "bifrost:last-preload-reload";
const LOOP_GUARD_MS = 5_000;

/**
 * Handle a vite:preloadError by reloading, with a sessionStorage loop guard
 * so a chronically broken deploy can't trap us in a reload tornado.
 *
 * Exported as a named function (not bound directly to addEventListener)
 * so tests can call it with mocked sessionStorage / location.
 */
export function handleVitePreloadError(event?: Event): void {
	if (!requestApplicationReload(RELOAD_KEY, LOOP_GUARD_MS)) {
		// Already reloaded within the last 5s — don't loop. The version banner
		// will surface the version mismatch on the next poll cycle.
		console.error("[bifrost] preload error after recent reload, suppressing");
		return;
	}

	// Vite recommends cancelling the event when the application handles the
	// stale import itself. This keeps the error boundary from flashing first.
	event?.preventDefault();
}
