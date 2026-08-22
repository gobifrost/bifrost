const listeners = new Set<() => void>();

let applicationUpdateInProgress = false;

export function getApplicationUpdateInProgress(): boolean {
	return applicationUpdateInProgress;
}

export function subscribeToApplicationUpdate(listener: () => void): () => void {
	listeners.add(listener);
	return () => listeners.delete(listener);
}

export function requestApplicationReload(
	storageKey: string,
	loopGuardMs: number,
): boolean {
	const lastReload = sessionStorage.getItem(storageKey);
	const now = Date.now();
	if (lastReload && now - Number(lastReload) < loopGuardMs) return false;

	sessionStorage.setItem(storageKey, String(now));
	applicationUpdateInProgress = true;
	listeners.forEach((listener) => listener());

	// Give React one task to replace the current UI before navigation begins.
	window.setTimeout(() => window.location.reload(), 0);
	return true;
}
