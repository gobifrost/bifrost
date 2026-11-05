import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { ApplicationSdkUpdateState } from "@/components/applications/ApplicationSdkStatusBadge";
import { webSocketService, type PlatformJobUpdate } from "@/services/websocket";
import type { components } from "@/lib/v1";

type AcceptedSdkUpdate = components["schemas"]["ApplicationSdkUpdateAccepted"];

const TERMINAL_STATUSES = new Set([
	"succeeded",
	"failed",
	"cancelled",
	"requires_action",
]);

function isSdkUpdateJob(job: PlatformJobUpdate): boolean {
	return job.job_type === "application.sdk_update";
}

function appIdFromJob(job: PlatformJobUpdate): string | null {
	return job.resource_id ?? job.resource_lock_key?.split(":").at(-1) ?? null;
}

function stateFromStatus(status: string): ApplicationSdkUpdateState {
	if (
		status === "failed" ||
		status === "cancelled" ||
		status === "requires_action"
	)
		return "failed";
	if (status === "succeeded") return "idle";
	if (status === "queued") return "queued";
	return "updating";
}

export function useApplicationSdkUpdateJobs({
	solutionId,
}: { solutionId?: string | null } = {}) {
	const queryClient = useQueryClient();
	// Each App's state remembers the job it came from, so an enqueue response
	// never overwrites a newer update that its own job already delivered.
	const [states, setStates] = useState<
		Record<string, { state: ApplicationSdkUpdateState; jobId: string }>
	>({});

	const invalidateSdkConsumers = useCallback(() => {
		void queryClient.invalidateQueries({
			queryKey: ["get", "/api/applications"],
		});
		void queryClient.invalidateQueries({ queryKey: ["solutions"] });
		if (solutionId) {
			void queryClient.invalidateQueries({
				queryKey: ["solutions", solutionId, "entities"],
			});
			void queryClient.invalidateQueries({
				queryKey: ["solutions", solutionId, "sdk-status"],
			});
		}
	}, [queryClient, solutionId]);

	useEffect(() => {
		return webSocketService.onAnyPlatformJobUpdate((job) => {
			if (!isSdkUpdateJob(job)) return;
			const appId = appIdFromJob(job);
			if (!appId) return;
			const nextState = stateFromStatus(job.status);
			setStates((current) => ({
				...current,
				[appId]: { state: nextState, jobId: job.id },
			}));
			if (TERMINAL_STATUSES.has(job.status)) {
				invalidateSdkConsumers();
			}
		});
	}, [invalidateSdkConsumers]);

	const trackAccepted = useCallback((accepted: AcceptedSdkUpdate[] = []) => {
		if (!accepted.length) return;
		setStates((current) => {
			const next = { ...current };
			for (const operation of accepted) {
				if (
					next[operation.application_id]?.jobId === operation.job_id
				) {
					continue;
				}
				next[operation.application_id] = {
					state: stateFromStatus(operation.status),
					jobId: operation.job_id,
				};
			}
			return next;
		});
	}, []);

	const getUpdateState = useCallback(
		(appId: string): ApplicationSdkUpdateState =>
			states[appId]?.state ?? "idle",
		[states],
	);

	const isAnyUpdating = useCallback(
		(appIds: string[]): boolean =>
			appIds.some(
				(appId) =>
					states[appId]?.state === "queued" ||
					states[appId]?.state === "updating",
			),
		[states],
	);

	const hasUpdateState = useCallback(
		(appId: string): boolean => appId in states,
		[states],
	);

	return { getUpdateState, hasUpdateState, isAnyUpdating, trackAccepted };
}
