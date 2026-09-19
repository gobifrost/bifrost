import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { webSocketService } from "@/services/websocket";

/** Read authoritative snapshots after hints; also close the initial subscription/reconnect gap. */
export function useAgentPlatformUpdates(runId?: string, jobId?: string) {
	const client = useQueryClient();
	useEffect(() => {
		let active = true;
		const refresh = () => {
			if (active) {
				void client.invalidateQueries({ queryKey: ["agent-platform"] });
				if (runId)
					void client.invalidateQueries({
						queryKey: [
							"get",
							"/api/agent-runs/{run_id}",
							{ params: { path: { run_id: runId } } },
						],
					});
			}
		};
		const offRun = webSocketService.onAgentRunUpdate((event) => {
			if (!runId || event.run_id === runId) refresh();
		});
		const offJournal = webSocketService.onAgentJournalAppended((id) => {
			if (!runId || id === runId) refresh();
		});
		const offJob = jobId
			? webSocketService.onPlatformJobUpdate(jobId, refresh)
			: () => {};
		const offConnection = webSocketService.onConnectionStatusChange(
			(connected) => {
				if (connected) refresh();
			},
		);
		void webSocketService
			.connect(runId ? [`agent-run:${runId}`] : ["agent-runs"])
			.then(refresh)
			.catch(() => {});
		return () => {
			active = false;
			offRun();
			offJournal();
			offJob();
			offConnection();
		};
	}, [client, runId, jobId]);
}
