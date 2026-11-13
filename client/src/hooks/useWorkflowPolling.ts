/**
 * React hook for polling multiple workflow execution statuses
 * Automatically polls executions and updates their status until completion
 */

import { useEffect, useRef, useState } from "react";
import { getExecution } from "@/hooks/useExecutions";
import type { components } from "@/lib/v1";

type WorkflowExecution = components["schemas"]["WorkflowExecution"];
type ExecutionStatus = components["schemas"]["ExecutionStatus"];

export interface WorkflowPollingOptions {
	/** List of execution IDs to monitor */
	executionIds: string[];
	/** Polling interval in milliseconds (default: 5000 = 5 seconds) */
	intervalMs?: number;
	/** Callback when an execution completes */
	onExecutionComplete?: (execution: WorkflowExecution) => void;
	/** Callback when an execution fails */
	onExecutionFail?: (execution: WorkflowExecution) => void;
	/** Enable/disable polling */
	enabled?: boolean;
}

interface PollingState {
	/** Map of execution ID to execution data */
	executions: Map<string, WorkflowExecution>;
	/** Whether polling is active */
	isPolling: boolean;
	/** Error if polling failed */
	error: Error | null;
}

/**
 * Hook to poll multiple workflow executions
 * Automatically stops polling when all executions are complete or failed
 */
export function useWorkflowPolling(options: WorkflowPollingOptions) {
	const {
		executionIds,
		intervalMs = 5000,
		onExecutionComplete,
		onExecutionFail,
		enabled = true,
	} = options;

	const [state, setState] = useState<PollingState>({
		executions: new Map(),
		isPolling: false,
		error: null,
	});

	const intervalRef = useRef<NodeJS.Timeout | null>(null);
	const callbacksRef = useRef({ onExecutionComplete, onExecutionFail });
	const executionIdsKey = executionIds.join(","); // Stable key for dependency array

	// Update callbacks ref when they change
	useEffect(() => {
		callbacksRef.current = { onExecutionComplete, onExecutionFail };
	}, [onExecutionComplete, onExecutionFail]);

	useEffect(() => {
		// Clear any existing interval
		if (intervalRef.current) {
			clearInterval(intervalRef.current);
			intervalRef.current = null;
		}

		// Don't start polling if disabled or no IDs
		if (!enabled || executionIds.length === 0) {
			setState((prev) => ({ ...prev, isPolling: false }));
			return;
		}

		// Initialize executions map with pending state
		setState((prev) => ({
			...prev,
			isPolling: true,
			error: null,
		}));

		// Poll function
		const pollExecutions = async () => {
			try {
				// Fetch all executions in parallel
				const results = await Promise.allSettled(
					executionIds.map((id) => getExecution(id)),
				);

				const newExecutions = new Map<string, WorkflowExecution>();
				let hasRunningExecutions = false;

				results.forEach((result) => {
					if (result.status === "fulfilled" && result.value) {
						// Cast to WorkflowExecution since we know the API returns this type
						const execution = result.value as WorkflowExecution;

						// Ensure we have required fields (TypeScript narrowing)
						const execId = execution.execution_id;
						const execStatus = execution.status;
						if (!execId || !execStatus) {
							return;
						}

						newExecutions.set(execId, execution);

						// Check if execution is still running
						const status: ExecutionStatus = execution.status;
						if (status === "Pending" || status === "Running") {
							hasRunningExecutions = true;
						}

						// Get previous execution state to detect status changes
						setState((prev) => {
							const prevExecution = prev.executions.get(execId);
							const prevStatus = prevExecution?.status;

							// Trigger callbacks only on status change
							if (prevStatus !== execStatus) {
								if (
									(execStatus === "Success" ||
										execStatus === "CompletedWithErrors") &&
									callbacksRef.current.onExecutionComplete
								) {
									callbacksRef.current.onExecutionComplete(
										execution,
									);
								} else if (
									execStatus === "Failed" &&
									callbacksRef.current.onExecutionFail
								) {
									callbacksRef.current.onExecutionFail(
										execution,
									);
								}
							}

							return prev;
						});
					}
				});

				// Update state with new executions
				setState((prev) => ({
					...prev,
					executions: newExecutions,
					isPolling: hasRunningExecutions,
				}));

				// Stop polling if no executions are running
				if (!hasRunningExecutions && intervalRef.current) {
					clearInterval(intervalRef.current);
					intervalRef.current = null;
				}
			} catch (error) {
				setState((prev) => ({
					...prev,
					error:
						error instanceof Error
							? error
							: new Error("Polling failed"),
					isPolling: false,
				}));

				if (intervalRef.current) {
					clearInterval(intervalRef.current);
					intervalRef.current = null;
				}
			}
		};

		// Start initial poll immediately
		void pollExecutions();

		// Set up interval for continuous polling
		intervalRef.current = setInterval(pollExecutions, intervalMs);

		// Cleanup on unmount or when dependencies change
		return () => {
			if (intervalRef.current) {
				clearInterval(intervalRef.current);
				intervalRef.current = null;
			}
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [executionIdsKey, intervalMs, enabled]); // executionIds tracked via executionIdsKey

	return {
		/** Map of execution ID to execution data */
		executions: state.executions,
		/** Array of executions (for convenience) */
		executionsList: Array.from(state.executions.values()),
		/** Whether polling is currently active */
		isPolling: state.isPolling,
		/** Error if polling failed */
		error: state.error,
		/** Get a specific execution by ID */
		getExecution: (id: string) => state.executions.get(id),
	};
}
