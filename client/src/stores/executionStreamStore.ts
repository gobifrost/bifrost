import { create } from "zustand";
import type { WaitReason } from "@/services/websocket";

export interface StreamingLog {
	id?: number; // Unique log ID for deduplication with API logs
	level: string;
	message: string;
	timestamp: string;
	sequence?: number; // Sequence number for client-side reordering
}

export type ExecutionStatus =
	| "Pending"
	| "Running"
	| "Success"
	| "Failed"
	| "CompletedWithErrors"
	| "Timeout"
	| "Cancelled"
	| "Cancelling";

export interface ExecutionStreamState {
	executionId: string;
	status: ExecutionStatus;
	streamingLogs: StreamingLog[];
	pendingLogs: StreamingLog[]; // Buffer for out-of-order logs
	expectedSequence: number; // Next expected sequence (starts at 1)
	isComplete: boolean;
	isConnected: boolean;
	hasReceivedUpdate: boolean; // True once we've received any status update
	variables?: Record<string, unknown>;
	error?: string;
	// Queue visibility fields
	queuePosition?: number;
	waitReason?: WaitReason;
	availableMemoryMb?: number;
	requiredMemoryMb?: number;
}

// Queue update data passed to updateStatus
export interface QueueUpdateData {
	queuePosition?: number;
	waitReason?: WaitReason;
	availableMemoryMb?: number;
	requiredMemoryMb?: number;
}

export interface QueuePositionUpdate {
	queuePosition: number;
	waitReason: WaitReason;
}

interface ExecutionStreamStore {
	// Track multiple concurrent execution streams by ID
	streams: Record<string, ExecutionStreamState>;

	// Actions
	startStreaming: (
		executionId: string,
		initialStatus?: ExecutionStatus,
	) => void;
	appendLog: (executionId: string, log: StreamingLog) => void;
	appendLogs: (executionId: string, logs: StreamingLog[]) => void;
	updateStatus: (
		executionId: string,
		status: ExecutionStatus,
		queueData?: QueueUpdateData,
	) => void;
	updateQueuePosition: (
		executionId: string,
		queueUpdate: QueuePositionUpdate,
	) => void;
	setConnectionStatus: (executionId: string, isConnected: boolean) => void;
	completeExecution: (
		executionId: string,
		variables?: Record<string, unknown>,
		finalStatus?: ExecutionStatus,
	) => void;
	clearStream: (executionId: string) => void;
	setError: (executionId: string, error: string) => void;
}

export const useExecutionStreamStore = create<ExecutionStreamStore>((set) => ({
	streams: {},

	startStreaming: (executionId, initialStatus = "Running") => {
		set((state) => {
			// Check if stream already exists
			if (state.streams[executionId]) {
				console.warn(
					`[ExecutionStreamStore] Stream ${executionId} already exists, skipping`,
				);
				return state;
			}

			return {
				streams: {
					...state.streams,
					[executionId]: {
						executionId,
						status: initialStatus,
						streamingLogs: [],
						pendingLogs: [],
						expectedSequence: 1,
						isComplete: false,
						isConnected: false,
						hasReceivedUpdate: false,
					},
				},
			};
		});
	},

	appendLog: (executionId, log) => {
		set((state) => {
			const stream = state.streams[executionId];
			if (!stream) {
				console.warn(
					`[ExecutionStreamStore] Stream ${executionId} not found for appendLog`,
				);
				return state;
			}

			return {
				streams: {
					...state.streams,
					[executionId]: {
						...stream,
						streamingLogs: [...stream.streamingLogs, log],
					},
				},
			};
		});
	},

	appendLogs: (executionId, logs) => {
		set((state) => {
			const stream = state.streams[executionId];
			if (!stream) {
				console.warn(
					`[ExecutionStreamStore] Stream ${executionId} not found for appendLogs`,
				);
				return state;
			}

			// If no sequence numbers, append directly (backwards compatibility)
			if (!logs.some((l) => l.sequence !== undefined)) {
				return {
					streams: {
						...state.streams,
						[executionId]: {
							...stream,
							streamingLogs: [...stream.streamingLogs, ...logs],
						},
					},
				};
			}

			// Add new logs to pending buffer
			const allPending = [...stream.pendingLogs, ...logs];

			// Sort by sequence and extract consecutive logs
			allPending.sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));

			const readyLogs: StreamingLog[] = [];
			let nextExpected = stream.expectedSequence;
			const stillPending: StreamingLog[] = [];

			for (const log of allPending) {
				if (log.sequence === nextExpected) {
					readyLogs.push(log);
					nextExpected++;
				} else if (
					log.sequence !== undefined &&
					log.sequence > nextExpected
				) {
					stillPending.push(log);
				}
				// Duplicates (sequence < expected) are dropped
			}

			return {
				streams: {
					...state.streams,
					[executionId]: {
						...stream,
						streamingLogs: [...stream.streamingLogs, ...readyLogs],
						pendingLogs: stillPending,
						expectedSequence: nextExpected,
					},
				},
			};
		});
	},

	updateStatus: (executionId, status, queueData) => {
		set((state) => {
			const stream = state.streams[executionId];
			if (!stream) {
				console.warn(
					`[ExecutionStreamStore] Stream ${executionId} not found for updateStatus`,
				);
				return state;
			}

			// Clear queue fields when transitioning away from Pending
			const clearQueueFields = status !== "Pending";

			return {
				streams: {
					...state.streams,
					[executionId]: {
						...stream,
						status,
						hasReceivedUpdate: true, // Mark that we've received at least one update
						// Update queue fields if provided, clear if not Pending
						queuePosition: clearQueueFields
							? undefined
							: (queueData?.queuePosition ??
								stream.queuePosition),
						waitReason: clearQueueFields
							? undefined
							: (queueData?.waitReason ?? stream.waitReason),
						availableMemoryMb: clearQueueFields
							? undefined
							: (queueData?.availableMemoryMb ??
								stream.availableMemoryMb),
						requiredMemoryMb: clearQueueFields
							? undefined
							: (queueData?.requiredMemoryMb ??
								stream.requiredMemoryMb),
					},
				},
			};
		});
	},

	updateQueuePosition: (executionId, queueUpdate) => {
		set((state) => {
			const stream = state.streams[executionId];
			if (!stream) {
				console.warn(
					`[ExecutionStreamStore] Stream ${executionId} not found for updateQueuePosition`,
				);
				return state;
			}

			const initializePending =
				!stream.hasReceivedUpdate && stream.status === "Running";
			if (stream.status !== "Pending" && !initializePending) {
				return state;
			}

			return {
				streams: {
					...state.streams,
					[executionId]: {
						...stream,
						...(initializePending ? { status: "Pending" as const } : {}),
						hasReceivedUpdate: true,
						queuePosition: queueUpdate.queuePosition,
						waitReason: queueUpdate.waitReason,
					},
				},
			};
		});
	},

	setConnectionStatus: (executionId, isConnected) => {
		set((state) => {
			const stream = state.streams[executionId];
			if (!stream) {
				console.warn(
					`[ExecutionStreamStore] Stream ${executionId} not found for setConnectionStatus`,
				);
				return state;
			}

			return {
				streams: {
					...state.streams,
					[executionId]: {
						...stream,
						isConnected,
					},
				},
			};
		});
	},

	completeExecution: (executionId, variables, finalStatus) => {
		set((state) => {
			const stream = state.streams[executionId];
			if (!stream) {
				console.warn(
					`[ExecutionStreamStore] Stream ${executionId} not found for completeExecution`,
				);
				return state;
			}

			// Flush remaining pending logs (sorted by sequence)
			const flushedLogs = [...stream.pendingLogs].sort(
				(a, b) => (a.sequence ?? 0) - (b.sequence ?? 0),
			);

			const updatedStream: ExecutionStreamState = {
				...stream,
				isComplete: true,
				status: finalStatus || stream.status,
				streamingLogs: [...stream.streamingLogs, ...flushedLogs],
				pendingLogs: [],
			};

			// Only set variables if provided
			if (variables !== undefined) {
				updatedStream.variables = variables;
			}

			return {
				streams: {
					...state.streams,
					[executionId]: updatedStream,
				},
			};
		});
	},

	clearStream: (executionId) => {
		set((state) => {
			const { [executionId]: _removed, ...remainingStreams } =
				state.streams;
			return {
				streams: remainingStreams,
			};
		});
	},

	setError: (executionId, error) => {
		set((state) => {
			const stream = state.streams[executionId];
			if (!stream) {
				console.warn(
					`[ExecutionStreamStore] Stream ${executionId} not found for setError`,
				);
				return state;
			}

			return {
				streams: {
					...state.streams,
					[executionId]: {
						...stream,
						error,
					},
				},
			};
		});
	},
}));
