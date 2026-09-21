import { generateUUID } from "@/lib/uuid";
import { getPlatformJob } from "@/services/platformJobs";
import {
	webSocketService,
	type PlatformJobUpdate,
} from "@/services/websocket";

const TERMINAL_STATUSES = new Set([
	"succeeded",
	"failed",
	"cancelled",
	"requires_action",
]);

export class GitOpError extends Error {
	data: Record<string, unknown> | undefined;

	constructor(message: string, data?: Record<string, unknown>) {
		super(message);
		this.name = "GitOpError";
		this.data = data;
	}
}

export type GitOperationAccepted = {
	job_id: string;
	status: string;
};

function jobFailure(job: PlatformJobUpdate, resultType: string): GitOpError {
	const result = job.result ?? {};
	const resultError = typeof result.error === "string" ? result.error : undefined;
	return new GitOpError(
		job.error?.message ?? resultError ?? `${resultType} failed`,
		result,
	);
}

/**
 * Queue a workspace Git PlatformJob and observe its shared durable state.
 *
 * The update listener is registered before enqueueing. A status read after the
 * accepted response closes the remaining response-to-notification race without
 * introducing a Git-specific polling transport.
 */
export async function runGitOp<T>(
	queueFn: (jobId: string) => Promise<GitOperationAccepted>,
	resultType: string,
	onQueued?: (jobId: string) => void,
	onUpdate?: (job: PlatformJobUpdate) => void,
): Promise<T> {
	const requestedJobId = generateUUID();
	await webSocketService.connect();

	return new Promise<T>((resolve, reject) => {
		let settled = false;
		const unsubscribers: Array<() => void> = [];

		const cleanup = () => {
			for (const unsubscribe of unsubscribers) unsubscribe();
		};
		const settle = (callback: () => void) => {
			if (settled) return;
			settled = true;
			cleanup();
			callback();
		};
		const handleUpdate = (job: PlatformJobUpdate) => {
			onUpdate?.(job);
			if (!TERMINAL_STATUSES.has(job.status)) return;
			if (job.status === "succeeded" || job.status === "requires_action") {
				settle(() => resolve((job.result ?? {}) as T));
				return;
			}
			settle(() => reject(jobFailure(job, resultType)));
		};
		const subscribe = (jobId: string) => {
			unsubscribers.push(webSocketService.onPlatformJobUpdate(jobId, handleUpdate));
		};

		// Subscribe before mutation so a fast local scheduler cannot complete
		// between enqueue and observation.
		subscribe(requestedJobId);
		void queueFn(requestedJobId)
			.then(async (accepted) => {
				onQueued?.(accepted.job_id);
				if (accepted.job_id !== requestedJobId) subscribe(accepted.job_id);
				try {
					handleUpdate(await getPlatformJob(accepted.job_id));
				} catch {
					// The subscribed notification remains authoritative if the
					// one-shot status snapshot is transiently unavailable.
				}
			})
			.catch((error: unknown) => {
				settle(() => reject(error));
			});
	});
}
