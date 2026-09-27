import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getErrorMessage } from "@/lib/api-error";
import {
	disableService,
	enableService,
	restartService,
	SERVICES_QUERY_KEY,
	serviceDetailQueryKey,
	startService,
	stopService,
	type Service,
	type ServiceAction,
	type ServiceListItem,
} from "@/services/services";

/** Actions that require an explicit confirm dialog before running. */
export const CONFIRM_ACTIONS: ReadonlySet<ServiceAction> = new Set([
	"stop",
	"restart",
	"disable",
]);

const ACTION_FN: Record<
	ServiceAction,
	(serviceId: string) => Promise<Service>
> = {
	start: startService,
	stop: stopService,
	restart: restartService,
	enable: enableService,
	disable: disableService,
};

const ACTION_PAST_TENSE: Record<ServiceAction, string> = {
	start: "started",
	stop: "stopped",
	restart: "restarted",
	enable: "enabled",
	disable: "disabled",
};

export interface ServiceActionRequest {
	service: ServiceListItem;
	action: ServiceAction;
}

/**
 * Run live service control actions (2.1).
 *
 * Success invalidates the services list + detail queries so the 2.2 live
 * data refreshes; failure surfaces a `getErrorMessage` toast. The hook
 * stays UI-agnostic: pages own the confirm-dialog state and only call
 * `runAction` for confirmed (or direct Start/Enable) intents.
 */
export function useServiceAction() {
	const queryClient = useQueryClient();
	const mutation = useMutation({
		mutationFn: async ({ service, action }: ServiceActionRequest) =>
			ACTION_FN[action](service.id),
		onSuccess: (_data, { service, action }) => {
			void queryClient.invalidateQueries({
				queryKey: [...SERVICES_QUERY_KEY],
			});
			void queryClient.invalidateQueries({
				queryKey: serviceDetailQueryKey(service.id),
			});
			toast.success(
				`${service.workflow_name} ${ACTION_PAST_TENSE[action]}.`,
			);
		},
		onError: (error, { action }) => {
			toast.error(
				getErrorMessage(
					error,
					`Could not ${action} the service.`,
				),
			);
		},
	});
	return {
		runAction: (request: ServiceActionRequest) =>
			mutation.mutateAsync(request),
		isActionPending: mutation.isPending,
	};
}
