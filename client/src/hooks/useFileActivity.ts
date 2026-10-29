import { useCallback, useEffect } from "react";
import { authFetch } from "@/lib/api-client";
import { GLOBAL_TARGET } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";
import { webSocketService } from "@/services/websocket";
import { useFileActivityStore } from "@/stores/fileActivityStore";

interface WatcherData {
	user_id: string;
	user_name: string;
	prefix: string;
	started_at: string;
}

/**
 * Maintains Global file activity from its server-authorized WebSocket stream.
 *
 * ``platform.read`` controls the stream. The watcher snapshot is separately
 * gated by the existing Global ``repository.read`` operation, so an observer
 * who can receive activity but cannot inspect current watchers never makes a
 * request the server will refuse.
 */
export function useFileActivity() {
	const authorization = useAuthorization();
	const addEvent = useFileActivityStore((state) => state.addEvent);
	const setWatchers = useFileActivityStore((state) => state.setWatchers);
	const canReadActivity = authorization.canAt("platform.read", GLOBAL_TARGET);
	const canListWatchers = authorization.canAt(
		"repository.read",
		GLOBAL_TARGET,
	);

	const clearActivity = useCallback(() => {
		useFileActivityStore.setState({ recentPushes: [], activeWatchers: [] });
	}, []);

	const fetchWatchers = useCallback(async (isCurrent: () => boolean) => {
		if (!canListWatchers) {
			if (isCurrent()) setWatchers([]);
			return;
		}
		try {
			const response = await authFetch("/api/files/watchers");
			if (!response.ok) return;
			const data = (await response.json()) as { watchers: WatcherData[] };
			if (!isCurrent()) return;
			setWatchers(
				data.watchers.map((watcher) => ({
					type: "watch_start" as const,
					user_id: watcher.user_id,
					user_name: watcher.user_name,
					prefix: watcher.prefix,
					file_count: 0,
					is_watch: true,
					timestamp: watcher.started_at,
				})),
			);
		} catch {
			// Live events remain authoritative while a best-effort snapshot fails.
		}
	}, [canListWatchers, setWatchers]);

	useEffect(() => {
		if (!canReadActivity) {
			clearActivity();
			return;
		}

		let active = true;
		const synchronize = async () => {
			if (!webSocketService.isConnected()) return;
			await webSocketService.subscribe("file-activity");
			if (active) await fetchWatchers(() => active);
		};
		const unsubscribeEvents = webSocketService.onFileActivity(addEvent);
		const unsubscribeConnection = webSocketService.onConnectionStatusChange(
			(connected) => {
				if (!connected) {
					setWatchers([]);
					return;
				}
				void synchronize();
			},
		);
		void synchronize();

		return () => {
			active = false;
			unsubscribeEvents();
			unsubscribeConnection();
			void webSocketService.unsubscribe("file-activity");
			clearActivity();
		};
	}, [addEvent, canReadActivity, clearActivity, fetchWatchers, setWatchers]);
}
