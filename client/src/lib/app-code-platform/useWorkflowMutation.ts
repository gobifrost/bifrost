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
	webSocketService,
	type ExecutionLog,
} from "@/services/websocket";
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
	unsubUpdate: () => void;
	unsubLog: () => void;
	channel: string;
	timeout: ReturnType<typeof setTimeout>;
}

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
			sub.unsubUpdate();
			sub.unsubLog();
			clearTimeout(sub.timeout);
			webSocketService.unsubscribe(sub.channel);
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
					sub.unsubUpdate();
					sub.unsubLog();
					clearTimeout(sub.timeout);
					webSocketService.unsubscribe(sub.channel);
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
					responseError !== null &&
					"detail" in responseError
						? String(
								(responseError as { detail: unknown }).detail,
							)
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
						responseData.error ??
						`Workflow ${responseData.status}`;
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

			// Connect WebSocket and subscribe
			const channel = `execution:${execId}`;
			try {
				await webSocketService.connect([channel]);
			} catch (err) {
				cleanupExecution(execId);
				const errorMessage =
					err instanceof Error
						? err.message
						: "WebSocket connection failed";
				if (mountedRef.current) {
					setError(errorMessage);
					setIsLoading(false);
				}
				throw new Error(errorMessage, { cause: err });
			}

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

			// Subscribe to execution updates
			const unsubUpdate = webSocketService.onExecutionUpdate(
				execId,
				async (update) => {
					const currentStore = useExecutionStreamStore.getState();

					if (update.status) {
						currentStore.updateStatus(
							execId,
							update.status as ExecutionStatus,
						);
					}

					if (update.isComplete) {
						currentStore.completeExecution(
							execId,
							undefined,
							update.status as ExecutionStatus,
						);

						const pending = deferredMapRef.current.get(execId);
						if (pending) {
							try {
								const execution = await getExecution(execId);
								if (execution.status === "Success") {
									const result = execution.result as T;
									if (mountedRef.current) {
										setData(result);
										setIsLoading(false);
									}
									pending.resolve(result);
								} else {
									const errMsg =
										execution.error_message ||
										`Workflow ${update.status}`;
									if (mountedRef.current) {
										setError(errMsg);
										setIsLoading(false);
									}
									pending.reject(new Error(errMsg));
								}
							} catch (fetchErr) {
								const errMsg =
									fetchErr instanceof Error
										? fetchErr.message
										: "Failed to fetch result";
								if (mountedRef.current) {
									setError(errMsg);
									setIsLoading(false);
								}
								pending.reject(new Error(errMsg));
							}
							cleanupExecution(execId);
						}
					}
				},
			);

			// Subscribe to execution logs
			const unsubLog = webSocketService.onExecutionLog(
				execId,
				(log: ExecutionLog) => {
					const currentStore = useExecutionStreamStore.getState();
					const streamingLog: StreamingLog = {
						level: log.level,
						message: log.message,
						timestamp: log.timestamp,
					};
					if (log.sequence !== undefined) {
						streamingLog.sequence = log.sequence;
					}
					currentStore.appendLogs(execId, [streamingLog]);
				},
			);

			// Store subscription references
			subscriptionsRef.current.set(execId, {
				unsubUpdate,
				unsubLog,
				channel,
				timeout,
			});

			// Fast workflows can complete before the WebSocket callback is attached.
			// Check once immediately after subscribing so the hook does not stay
			// unresolved when the terminal event was missed.
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
				// Ignore immediate status fetch failures; the normal WebSocket path
				// still handles ordinary execution flow.
			}

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
