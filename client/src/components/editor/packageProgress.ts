import type { PackageProgress } from "@/services/websocket";

export interface ProgressState {
	lastLine: string;
	completionHandled: boolean;
}

export interface ProgressStore {
	appendLog: (
		id: string,
		log: { level: string; message: string; timestamp: string },
	) => void;
	completeExecution: (
		id: string,
		variables: Record<string, unknown> | undefined,
		status: "Success" | "Failed",
	) => void;
}

/**
 * Handle a single aggregated package-install progress event.
 *
 * Rules:
 * - Collapse consecutive identical lines (compare to state.lastLine).
 * - Derive completion when recycled + failed >= total && total > 0.
 * - Guard completion with state.completionHandled so only the first
 *   qualifying event triggers it.
 *
 * Mutates `state` in place (mirrors ref semantics in the component).
 * Returns true if loadPackages should be called (completion was triggered).
 */
export function handleProgressEvent(
	p: PackageProgress,
	state: ProgressState,
	store: ProgressStore,
	id: string,
): boolean {
	// Collapse: skip if this line is identical to the last one we appended.
	if (p.line !== state.lastLine) {
		state.lastLine = p.line;
		store.appendLog(id, {
			level: p.failed > 0 ? "WARNING" : "INFO",
			message: p.line,
			timestamp: new Date().toISOString(),
		});
	}

	// Derive completion.
	if (
		p.total > 0 &&
		p.recycled + p.failed >= p.total &&
		!state.completionHandled
	) {
		state.completionHandled = true;
		store.completeExecution(id, undefined, p.failed > 0 ? "Failed" : "Success");
		return true;
	}

	return false;
}
