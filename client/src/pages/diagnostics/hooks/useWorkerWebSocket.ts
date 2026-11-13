/**
 * WebSocket hook for real-time pool updates
 *
 * Subscribes to the platform_workers channel and handles:
 * - worker_heartbeat: Full pool state updates
 * - worker_online: New pool registration
 * - worker_offline: Pool disconnection
 * - process_state_changed: Process state transitions
 * - pool_config_changed: Pool configuration updates
 * - pool_scaling: Scaling events
 * - pool_progress: Real-time progress during scaling operations
 */

import { useEffect, useState, useCallback } from "react";
import { webSocketService, type PoolMessage } from "@/services/websocket";
import type { PoolDetail, QueueItem, ProcessInfo } from "@/services/workers";

export interface ScalingState {
	worker_id: string;
	action: "scale_up" | "scale_down" | "recycle_all";
	processes_affected: number;
	timestamp: number;
}

export interface ProgressState {
	worker_id: string;
	action: "scale_up" | "scale_down" | "recycle_all";
	current: number;
	total: number;
	message: string;
	timestamp: number;
}

interface UseWorkerWebSocketReturn {
	pools: PoolDetail[];
	queue: QueueItem[];
	isConnected: boolean;
	scalingStates: Map<string, ScalingState>;
	progressStates: Map<string, ProgressState>;
}

/**
 * Hook to subscribe to real-time pool updates via WebSocket
 */
export function useWorkerWebSocket(): UseWorkerWebSocketReturn {
	const [pools, setPools] = useState<PoolDetail[]>([]);
	const [queue] = useState<QueueItem[]>([]);
	const [isConnected, setIsConnected] = useState(false);
	const [scalingStates, setScalingStates] = useState<
		Map<string, ScalingState>
	>(new Map());
	const [progressStates, setProgressStates] = useState<
		Map<string, ProgressState>
	>(new Map());

	const handleMessage = useCallback((message: PoolMessage) => {
		switch (message.type) {
			case "worker_heartbeat": {
				// Update or add pool
				setPools((prev) => {
					const idx = prev.findIndex(
						(p) => p.worker_id === message.worker_id,
					);

					// Convert heartbeat processes to ProcessInfo format
					const processes: ProcessInfo[] = (
						message.processes || []
					).map((p) => ({
						process_id: p.process_id,
						pid: p.pid,
						state: p.state,
						current_execution_id: p.execution?.execution_id || null,
						executions_completed: p.executions_completed,
						started_at: null,
						uptime_seconds: p.uptime_seconds,
						memory_mb: p.memory_mb,
						is_alive: true,
						pending_recycle: p.pending_recycle,
					}));

					const updatedPool: PoolDetail = {
						worker_id: message.worker_id,
						hostname: message.hostname || null,
						status: message.status || null,
						started_at: message.started_at || null,
						last_heartbeat: message.timestamp || null,
						processes,
						requirements_installed:
							message.requirements_installed ?? null,
						requirements_total: message.requirements_total ?? null,
						memory_current_bytes:
							message.memory_current_bytes ?? -1,
						memory_max_bytes: message.memory_max_bytes ?? -1,
					};

					if (idx >= 0) {
						const updated = [...prev];
						updated[idx] = updatedPool;
						return updated;
					}
					return [...prev, updatedPool];
				});
				break;
			}

			case "worker_online": {
				// Add new pool
				setPools((prev) => {
					// Check if already exists
					if (prev.some((p) => p.worker_id === message.worker_id)) {
						return prev;
					}
					return [
						...prev,
						{
							worker_id: message.worker_id,
							hostname: message.hostname || null,
							status: "online",
							started_at: message.started_at || null,
							last_heartbeat: null,
							processes: [],
							requirements_installed: null,
							requirements_total: null,
						},
					];
				});
				break;
			}

			case "worker_offline": {
				// Remove pool
				setPools((prev) =>
					prev.filter((p) => p.worker_id !== message.worker_id),
				);
				break;
			}

			case "process_state_changed": {
				// Update process state within pool
				setPools((prev) => {
					const idx = prev.findIndex(
						(p) => p.worker_id === message.worker_id,
					);
					if (idx < 0) return prev;

					const updated = [...prev];
					const pool = { ...updated[idx] };
					pool.processes = pool.processes.map((proc) =>
						proc.process_id === message.process_id
							? { ...proc, state: message.new_state }
							: proc,
					);
					updated[idx] = pool;
					return updated;
				});
				break;
			}

			case "pool_config_changed": {
				// No-op: pool config is no longer runtime-configurable
				break;
			}

			case "pool_scaling": {
				// Track scaling state for UI indicators
				setScalingStates((prev) => {
					const next = new Map(prev);
					next.set(message.worker_id, {
						worker_id: message.worker_id,
						action: message.action,
						processes_affected: message.processes_affected,
						timestamp: Date.now(),
					});
					return next;
				});

				// Clear scaling state after 5 seconds
				setTimeout(() => {
					setScalingStates((prev) => {
						const next = new Map(prev);
						next.delete(message.worker_id);
						return next;
					});
				}, 5000);
				break;
			}

			case "pool_progress": {
				// Track real-time progress for UI display
				setProgressStates((prev) => {
					const next = new Map(prev);
					next.set(message.worker_id, {
						worker_id: message.worker_id,
						action: message.action,
						current: message.current,
						total: message.total,
						message: message.message,
						timestamp: Date.now(),
					});
					return next;
				});

				// Clear progress state after 3 seconds of no updates
				// (the final progress will be cleared when operation completes)
				setTimeout(() => {
					setProgressStates((prev) => {
						const current = prev.get(message.worker_id);
						// Only clear if this is still the same progress message
						if (
							current &&
							current.current === message.current &&
							current.total === message.total
						) {
							const next = new Map(prev);
							next.delete(message.worker_id);
							return next;
						}
						return prev;
					});
				}, 3000);
				break;
			}
		}
	}, []);

	useEffect(() => {
		let mounted = true;
		const unsubscribeConnection = webSocketService.onConnectionStatusChange(
			(connected) => {
				if (mounted) setIsConnected(connected);
			},
		);
		let unsubscribePoolMessages: (() => void) | null = null;

		const connect = async () => {
			try {
				// Subscribe to platform workers channel
				await webSocketService.connect(["platform_workers"]);

				if (!mounted) return;

				if (webSocketService.isConnected()) {
					setIsConnected(true);
				}

				// Register callback for pool messages
				unsubscribePoolMessages =
					webSocketService.onPoolMessage(handleMessage);
			} catch (error) {
				console.error("[useWorkerWebSocket] Failed to connect:", error);
				if (mounted) {
					setIsConnected(false);
				}
			}
		};

		void connect();

		return () => {
			mounted = false;
			unsubscribeConnection();
			if (unsubscribePoolMessages) {
				unsubscribePoolMessages();
			}
			void webSocketService.unsubscribe("platform_workers");
		};
	}, [handleMessage]);

	return {
		pools,
		queue,
		isConnected,
		scalingStates,
		progressStates,
	};
}
