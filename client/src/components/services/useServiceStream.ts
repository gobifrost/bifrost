import { useEffect, useState } from "react";
import {
	webSocketService,
	type ServiceLog,
} from "@/services/websocket";
import type { StreamingLog } from "@/stores/executionStreamStore";

interface UseServiceStreamOptions {
	/** Service definition ID to stream logs for. */
	serviceId: string | undefined;
	/** Whether to subscribe (default: true). */
	enabled?: boolean;
}

/**
 * Live service log lines over the standard WebSocket (2.4).
 *
 * Subscribes to `service:{serviceId}` (platform admins only) and
 * accumulates bridged attempt lines as panel-ready `StreamingLog`
 * entries. Buffer resets when the service changes; the detail view
 * merges these with persisted rows (deduped) and passes `isConnected`
 * to the panel. No polling.
 */
export function useServiceStream({
	serviceId,
	enabled = true,
}: UseServiceStreamOptions) {
	const [buffer, setBuffer] = useState<{
		serviceId: string | undefined;
		logs: StreamingLog[];
	}>({ serviceId, logs: [] });
	// Reset the tail when the service changes during render (the
	// React-recommended pattern for derived-state resets — avoids a
	// setState-in-effect cascade).
	if (buffer.serviceId !== serviceId) {
		setBuffer({ serviceId, logs: [] });
	}
	const [isConnected, setIsConnected] = useState(false);

	useEffect(() => {
		if (!enabled || !serviceId) {
			return;
		}

		let unsubscribe: (() => void) | null = null;
		let cancelled = false;

		const init = async () => {
			try {
				await webSocketService.connectToService(serviceId);
				if (cancelled) return;
				if (!webSocketService.isConnected()) {
					setIsConnected(false);
					return;
				}
				setIsConnected(true);
				unsubscribe = webSocketService.onServiceLog(
					serviceId,
					(log: ServiceLog) => {
						setBuffer((prev) => ({
							serviceId,
							logs: [
								...prev.logs,
								{
									level: log.level,
									message: log.message,
									timestamp: log.timestamp,
								},
							],
						}));
					},
				);
			} catch {
				if (!cancelled) setIsConnected(false);
			}
		};

		void init();

		return () => {
			cancelled = true;
			if (unsubscribe) unsubscribe();
			void webSocketService.unsubscribe(`service:${serviceId}`);
		};
	}, [serviceId, enabled]);

	return { streamingLogs: buffer.logs, isConnected };
}
