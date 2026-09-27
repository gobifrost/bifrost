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
	const [states, setStates] = useState<
		Record<string, ApplicationSdkUpdateState>
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
				[appId]: nextState,
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
				next[operation.application_id] = stateFromStatus(
					operation.status,
				);
			}
			return next;
		});
	}, []);

	const getUpdateState = useCallback(
		(appId: string): ApplicationSdkUpdateState => states[appId] ?? "idle",
		[states],
	);

	const isAnyUpdating = useCallback(
		(appIds: string[]): boolean =>
			appIds.some(
				(appId) =>
					states[appId] === "queued" || states[appId] === "updating",
			),
		[states],
	);

	const hasUpdateState = useCallback(
		(appId: string): boolean => appId in states,
		[states],
	);

	return { getUpdateState, hasUpdateState, isAnyUpdating, trackAccepted };
}
