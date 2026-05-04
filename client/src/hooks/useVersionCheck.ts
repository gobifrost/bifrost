import { useEffect, useState } from "react";
import { APP_VERSION } from "@/lib/version";

const DEFAULT_INTERVAL_MS = 60_000;

/**
 * Polls /api/version and reports when the deployed server version differs
 * from the version baked into this build. Used to surface a "refresh to update"
 * banner so tabs don't get stuck on stale bundles after a deploy.
 *
 * Skipped entirely in dev (when APP_VERSION === "unknown") so the banner
 * doesn't fire constantly on hot-reload.
 */
export function useVersionCheck(intervalMs = DEFAULT_INTERVAL_MS): boolean {
	const [updateAvailable, setUpdateAvailable] = useState(false);

	useEffect(() => {
		// No version baked in (dev) — nothing to compare against.
		if (APP_VERSION === "unknown") return;

		let cancelled = false;
		let timer: ReturnType<typeof setTimeout> | null = null;

		const check = async () => {
			if (document.visibilityState === "hidden") return;
			try {
				const res = await fetch("/api/version");
				if (!res.ok) return;
				const data = (await res.json()) as { version?: string };
				if (cancelled) return;
				if (typeof data.version === "string" && data.version !== APP_VERSION) {
					setUpdateAvailable(true);
				}
			} catch {
				// Network error / API unavailable — quietly ignore; we'll try again.
			}
		};

		const schedule = () => {
			timer = setTimeout(async () => {
				await check();
				if (!cancelled) schedule();
			}, intervalMs);
		};

		const onVisibility = () => {
			if (document.visibilityState === "visible") {
				void check();
			}
		};

		void check(); // immediate fire on mount
		schedule();
		document.addEventListener("visibilitychange", onVisibility);

		return () => {
			cancelled = true;
			if (timer !== null) clearTimeout(timer);
			document.removeEventListener("visibilitychange", onVisibility);
		};
	}, [intervalMs]);

	return updateAvailable;
}
