/**
 * Platform hook: useWorkflowMutation
 *
 * Imperative workflow execution hook. Does nothing until execute() is called.
 * Returns the workflow result as Promise<T>, enabling simple await-based patterns.
 *
 * Each execute() call is independent — concurrent calls don't interfere with each other.
 */

import { useState, useCallback, useRef, useEffect } from "react";
import { apiClient } from "@/lib/api-client";
import {
	subscribeToExecution,
	type ExecutionStreamEvent,
} from "@/lib/app-sdk/execution-stream";
import {
	useExecutionStreamStore,
	type ExecutionStatus,
	type StreamingLog,
} from "@/stores/executionStreamStore";
import { getExecution } from "@/hooks/useExecutions";

interface Deferred<T> {
	promise: Promise<T>;
	resolve: (value: T) => void;
	reject: (error: Error) => void;
}

function createDeferred<T>(): Deferred<T> {
	let resolve!: (value: T) => void;
	let reject!: (error: Error) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

const EXECUTION_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes
const TERMINAL_STATUSES: ExecutionStatus[] = [
	"Success",
	"Failed",
	"CompletedWithErrors",
	"Timeout",
	"Cancelled",
];

export interface UseWorkflowMutationResult<T> {
	execute: (params?: Record<string, unknown>) => Promise<T>;
	isLoading: boolean;
	isError: boolean;
	/**
	 * Workflow execution error message, or null on success / before completion.
	 * Already a string — do NOT access `.message` on this value.
	 */
	errorMessage: string | null;
	/**
	 * @deprecated Use `errorMessage` — this is a string alias kept for
	 * backward compatibility. Reading `error.message` returns undefined and
	 * is the source of "Unknown error" fallbacks in app code.
	 */
	error: string | null;
	data: T | null;
	logs: StreamingLog[];
	reset: () => void;
	executionId: string | null;
	status: ExecutionStatus | null;
}

interface Subscription {
	unsubscribe: () => void;
	timeout: ReturnType<typeof setTimeout>;
}

const POLL_INTERVAL_MS = 2_000;

export function useWorkflowMutation<T = unknown>(
	workflowId: string,
): UseWorkflowMutationResult<T> {
	const [data, setData] = useState<T | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [isLoading, setIsLoading] = useState(false);
	const [executionId, setExecutionId] = useState<string | null>(null);

	const deferredMapRef = useRef<Map<string, Deferred<T>>>(new Map());
	const subscriptionsRef = useRef<Map<string, Subscription>>(new Map());
	const mountedRef = useRef(true);

	// Cleanup a single execution's resources
	const cleanupExecution = useCallback((execId: string) => {
		const sub = subscriptionsRef.current.get(execId);
		if (sub) {
			sub.unsubscribe();
			clearTimeout(sub.timeout);
			subscriptionsRef.current.delete(execId);
		}
		deferredMapRef.current.delete(execId);
		useExecutionStreamStore.getState().clearStream(execId);
	}, []);

	// Unmount cleanup
	useEffect(() => {
		mountedRef.current = true;
		const deferredMap = deferredMapRef.current;
		const subscriptions = subscriptionsRef.current;
		return () => {
			mountedRef.current = false;
			// Reject all pending deferreds
			for (const [execId, deferred] of deferredMap) {
				deferred.reject(new Error("Component unmounted"));
				const sub = subscriptions.get(execId);
				if (sub) {
					sub.unsubscribe();
					clearTimeout(sub.timeout);
				}
				useExecutionStreamStore.getState().clearStream(execId);
			}
			deferredMap.clear();
			subscriptions.clear();
		};
	}, []);

	const execute = useCallback(
		async (params?: Record<string, unknown>): Promise<T> => {
			// Reset reactive state for this new execution
			setData(null);
			setError(null);
			setIsLoading(true);

			// Call the execute API
			const { data: responseData, error: responseError } =
				await apiClient.POST("/api/workflows/execute", {
					body: {
						workflow_id: workflowId,
						input_data: params ?? {},
						form_id: null,
						transient: false,
						code: null,
						script_name: null,
					},
				});

			if (responseError) {
				const errorMessage =
					typeof responseError === "object" &&
					"detail" in responseError
						? String((responseError as { detail: unknown }).detail)
						: "Workflow execution failed";
				if (mountedRef.current) {
					setError(errorMessage);
					setIsLoading(false);
				}
				throw new Error(errorMessage);
			}

			if (!responseData?.execution_id) {
				const errorMessage = "No execution ID returned";
				if (mountedRef.current) {
					setError(errorMessage);
					setIsLoading(false);
				}
				throw new Error(errorMessage);
			}

			const execId = responseData.execution_id;

			// Short-circuit for transient/sync executions (e.g. data providers)
			// The server already returned the result inline — no WebSocket wait needed
			if (responseData.is_transient && responseData.status) {
				if (mountedRef.current) {
					setExecutionId(execId);
				}
				if (responseData.status === "Success") {
					const result = responseData.result as T;
					if (mountedRef.current) {
						setData(result);
						setIsLoading(false);
					}
					return result;
				} else {
					const errMsg =
						responseData.error ?? `Workflow ${responseData.status}`;
					if (mountedRef.current) {
						setError(errMsg);
						setIsLoading(false);
					}
					throw new Error(errMsg);
				}
			}

			// Create deferred for this execution
			const deferred = createDeferred<T>();
			deferredMapRef.current.set(execId, deferred);

			// Update reactive state
			if (mountedRef.current) {
				setExecutionId(execId);
			}

			// Initialize stream in store
			const store = useExecutionStreamStore.getState();
			store.startStreaming(execId);

			// Set up timeout
			const timeout = setTimeout(() => {
				const pending = deferredMapRef.current.get(execId);
				if (pending) {
					pending.reject(
						new Error("Workflow execution timed out (5 minutes)"),
					);
					if (mountedRef.current) {
						setError("Workflow execution timed out (5 minutes)");
						setIsLoading(false);
					}
					cleanupExecution(execId);
				}
			}, EXECUTION_TIMEOUT_MS);

			let pollTimer: ReturnType<typeof setInterval> | null = null;
			const stopPolling = () => {
				if (pollTimer) clearInterval(pollTimer);
				pollTimer = null;
			};
			const reconcileExecution = async () => {
				if (!deferredMapRef.current.has(execId)) return;
				try {
					const execution = await getExecution(execId);
					if (
						TERMINAL_STATUSES.includes(
							execution.status as ExecutionStatus,
						)
					) {
						const currentStore = useExecutionStreamStore.getState();
						currentStore.updateStatus(
							execId,
							execution.status as ExecutionStatus,
						);
						currentStore.completeExecution(
							execId,
							undefined,
							execution.status as ExecutionStatus,
						);

						const pending = deferredMapRef.current.get(execId);
						if (pending) {
							if (
								execution.status === "Success" ||
								execution.status === "CompletedWithErrors"
							) {
								const result = execution.result as T;
								if (mountedRef.current) {
									setData(result);
									setIsLoading(false);
								}
								pending.resolve(result);
							} else {
								const errMsg =
									execution.error_message ||
									`Workflow ${execution.status}`;
								if (mountedRef.current) {
									setError(errMsg);
									setIsLoading(false);
								}
								pending.reject(new Error(errMsg));
							}
							cleanupExecution(execId);
						}
					}
				} catch {
					// A transient read failure must not strand the execution. The stream,
					// reconnect acknowledgement, or polling fallback will check again.
				}
			};
			const startPolling = () => {
				if (!pollTimer) {
					pollTimer = setInterval(() => {
						void reconcileExecution();
					}, POLL_INTERVAL_MS);
				}
			};

			const unsubscribeStream = subscribeToExecution(
				execId,
				(event: ExecutionStreamEvent) => {
					const currentStore = useExecutionStreamStore.getState();
					if (event.type === "ready") {
						void reconcileExecution();
					} else if (event.type === "status") {
						if (event.status) {
							currentStore.updateStatus(
								execId,
								event.status as ExecutionStatus,
							);
						}
						if (event.isTerminal) {
							startPolling();
							void reconcileExecution();
						}
					} else if (event.type === "log" && event.log) {
						const streamingLog: StreamingLog = { ...event.log };
						currentStore.appendLogs(execId, [streamingLog]);
					}
				},
				startPolling,
			);

			// Keep the exact reconnecting stream used by the v2 SDK. If the socket
			// drops, poll until settlement and reconcile after every subscribe ack.
			subscriptionsRef.current.set(execId, {
				unsubscribe: () => {
					stopPolling();
					unsubscribeStream();
				},
				timeout,
			});

			// Fast workflows can finish before the first subscription acknowledgement.
			void reconcileExecution();

			return deferred.promise;
		},
		[workflowId, cleanupExecution],
	);

	const reset = useCallback(() => {
		setData(null);
		setError(null);
		setExecutionId(null);
		setIsLoading(false);
	}, []);

	// Get reactive logs from store
	const streamState = useExecutionStreamStore((state) =>
		executionId ? state.streams[executionId] : undefined,
	);

	const logs = streamState?.streamingLogs ?? [];
	const status = streamState?.status ?? null;
	const isError = error !== null;

	return {
		execute,
		isLoading,
		isError,
		errorMessage: error,
		error,
		data,
		logs,
		reset,
		executionId,
		status,
	};
}
