import { lazy, type ComponentType } from "react";

import { requestApplicationReload } from "./application-update";

const RELOAD_KEY = "chunk-reload-ts";
const LOOP_GUARD_MS = 10_000;

/**
 * Wrapper around React.lazy that auto-reloads the page once on chunk load failure.
 * After a deploy, old chunk hashes no longer exist. A reload fetches the new index.html
 * with correct chunk references.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function lazyWithReload<T extends ComponentType<any>>(
	importFn: () => Promise<{ default: T }>,
) {
	return lazy(() =>
		importFn().catch((error) => {
			if (requestApplicationReload(RELOAD_KEY, LOOP_GUARD_MS)) {
				// Navigation owns recovery now. Keeping the lazy import pending prevents
				// React from painting its error boundary before the refreshed document.
				return new Promise<never>(() => {});
			}

			// A second failure is a real broken-deploy condition, not a stale tab.
			throw error;
		}),
	);
}
