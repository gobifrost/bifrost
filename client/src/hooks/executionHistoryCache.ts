import type { HistoryUpdate } from "@/services/websocket";

export interface HistoryExecutionRecord extends Record<string, unknown> {
	execution_id: string;
	started_at?: string | null;
	scheduled_at?: string | null;
	completed_at?: string | null;
	created_at?: string | null;
}

export interface ExecutionHistoryPage {
	executions: HistoryExecutionRecord[];
	continuation_token: string | null;
}

function encodeHistoryCursor(execution: HistoryExecutionRecord): string {
	const timelineAt =
		execution.started_at ??
		execution.scheduled_at ??
		execution.completed_at ??
		execution.created_at;
	const payload = JSON.stringify({
		v: 1,
		s: timelineAt,
		i: execution.execution_id,
	});
	return globalThis
		.btoa(payload)
		.replace(/\+/g, "-")
		.replace(/\//g, "_");
}

/** Apply one History WebSocket event without corrupting the server page boundary. */
export function mergeExecutionHistoryPage(
	page: ExecutionHistoryPage,
	update: HistoryUpdate,
	allowInsert: boolean,
): ExecutionHistoryPage {
	const existingIndex = page.executions.findIndex(
		(execution) => execution.execution_id === update.execution_id,
	);

	if (existingIndex >= 0) {
		const executions = [...page.executions];
		executions[existingIndex] = {
			...executions[existingIndex],
			status: update.status,
			...(update.started_at && { started_at: update.started_at }),
			completed_at: update.completed_at,
			duration_ms: update.duration_ms,
		};
		return { ...page, executions };
	}

	if (!allowInsert) return page;

	const inserted: HistoryExecutionRecord = {
		execution_id: update.execution_id,
		workflow_name: update.workflow_name,
		status: update.status,
		executed_by: update.executed_by,
		executed_by_name: update.executed_by_name,
		org_id: update.org_id,
		started_at: update.started_at,
		completed_at: update.completed_at,
		created_at: update.timestamp,
		duration_ms: update.duration_ms,
	};

	if (page.continuation_token === null) {
		return { ...page, executions: [inserted, ...page.executions] };
	}

	const executions = [inserted, ...page.executions].slice(
		0,
		page.executions.length,
	);
	return {
		...page,
		executions,
		continuation_token: encodeHistoryCursor(executions[executions.length - 1]),
	};
}

