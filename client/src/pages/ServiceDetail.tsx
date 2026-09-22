import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Radio, RefreshCw } from "lucide-react";
import type { DateRange } from "react-day-picker";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { ListLoadError } from "@/components/layout/ListLoadError";
import { ServiceDetailView } from "@/components/services/ServiceDetailView";
import {
	ServiceActionDialog,
	type PendingServiceAction,
} from "@/components/services/ServiceActionDialog";
import {
	CONFIRM_ACTIONS,
	useServiceAction,
} from "@/components/services/useServiceAction";
import {
	useServiceAttempts,
	useServiceDetail,
	useServiceLogs,
	serviceLogDateFilters,
} from "@/components/services/useServiceQueries";
import { useServiceStream } from "@/components/services/useServiceStream";
import type {
	ServiceAction,
	ServiceListItem,
} from "@/services/services";
import { fileService } from "@/services/fileService";
import { useEditorStore } from "@/stores/editorStore";
import { useAuth } from "@/contexts/AuthContext";
import { getErrorMessage } from "@/lib/api-error";
import { toast } from "sonner";

/**
 * Service detail route (live).
 *
 * Definition + embedded attempt summary from `GET /api/services/{id}`;
 * full attempt history from `GET .../attempts` (rendered as system rows
 * in the unified timeline). Refresh converges through action
 * invalidation, window-focus refetch, and the Refresh button. No
 * browser polling loops. Log output arrives in 2.3.
 */
export function ServiceDetail() {
	const { serviceId } = useParams<{ serviceId: string }>();
	const navigate = useNavigate();
	const { isPlatformAdmin } = useAuth();
	const openFileInTab = useEditorStore((state) => state.openFileInTab);
	const openEditor = useEditorStore((state) => state.openEditor);
	const setSidebarPanel = useEditorStore((state) => state.setSidebarPanel);

	const {
		data: service,
		isLoading,
		isError,
		isFetching: isDetailFetching,
		refetch: refetchDetail,
	} = useServiceDetail(serviceId);
	const {
		data: attemptsData,
		isError: isAttemptsError,
		isFetching: isAttemptsFetching,
		refetch: refetchAttempts,
	} = useServiceAttempts(serviceId);

	// The date window filters server-side (logs query) AND client-side
	// (panel) under the same predicate — harmless duplication that keeps
	// the visible timeline exact while pages converge.
	const [dateRange, setDateRange] = useState<DateRange | undefined>(
		undefined,
	);
	const {
		data: logsData,
		isError: isLogsError,
		isFetching: isLogsFetching,
		refetch: refetchLogs,
		fetchNextPage: fetchOlderLogs,
		hasNextPage: hasOlderLogs,
		isFetchingNextPage: isLoadingOlderLogs,
	} = useServiceLogs(serviceId, serviceLogDateFilters(dateRange));

	const logEntries = useMemo(
		() => logsData?.pages.flatMap((page) => page.items) ?? [],
		[logsData],
	);
	const logsTotal = logsData?.pages[0]?.total ?? 0;

	// Live tail over service:{id} (platform admins only); merged with
	// persisted rows in the view. No polling.
	const { streamingLogs, isConnected: isStreamConnected } =
		useServiceStream({ serviceId });

	const { runAction, isActionPending } = useServiceAction();
	const [pendingConfirm, setPendingConfirm] =
		useState<PendingServiceAction | null>(null);
	const [confirmError, setConfirmError] = useState<string | null>(null);

	const handleAction = (svc: ServiceListItem, action: ServiceAction) => {
		if (CONFIRM_ACTIONS.has(action)) {
			setConfirmError(null);
			setPendingConfirm({
				service: svc,
				action: action as PendingServiceAction["action"],
			});
			return;
		}
		void runAction({ service: svc, action }).catch(() => {
			// Failure already surfaced via getErrorMessage toast in the hook.
		});
	};

	const handleConfirmAction = () => {
		if (!pendingConfirm || isActionPending) return;
		void runAction(pendingConfirm)
			.then(() => {
				setPendingConfirm(null);
				setConfirmError(null);
			})
			.catch((error: unknown) => {
				setConfirmError(
					getErrorMessage(
						error,
						`Could not ${pendingConfirm.action} the service.`,
					),
				);
			});
	};

	const handleRefresh = () => {
		void refetchDetail();
		void refetchAttempts();
		void refetchLogs();
	};

	const handleOpenInEditor = async (svc: ServiceListItem) => {
		const relativeFilePath = svc.workflow_path;
		try {
			const fileResponse = await fileService.readFile(relativeFilePath);
			const fileName =
				relativeFilePath.split("/").pop() || relativeFilePath;
			const extension = fileName.includes(".")
				? fileName.split(".").pop()!
				: null;
			openEditor();
			openFileInTab(
				{
					name: fileName,
					path: relativeFilePath,
					type: "file" as const,
					size: 0,
					extension,
					modified: new Date().toISOString(),
					entity_type: null,
					entity_id: null,
				},
				fileResponse.content,
				fileResponse.encoding as "utf-8" | "base64",
				fileResponse.etag,
			);
			setSidebarPanel("run");
		} catch {
			toast.error("Failed to open service file in editor");
		}
	};

	if (isLoading) {
		return (
			<PageWorkspace className="mx-auto w-full max-w-7xl">
				<p role="status" className="sr-only">
					Loading service…
				</p>
			</PageWorkspace>
		);
	}

	if (isError || !service) {
		return (
			<PageWorkspace className="mx-auto w-full max-w-7xl">
				<Card className="mx-auto max-w-md">
					<CardContent className="flex flex-col items-center gap-3 py-10 text-center">
						<span className="inline-grid size-12 place-items-center overflow-hidden rounded-[var(--bf-radius-control)] border border-primary/15 bg-primary/10 text-primary">
							<Radio aria-hidden="true" className="size-6" />
						</span>
						<h2 className="text-lg font-semibold">
							Service not found
						</h2>
						<p className="text-sm text-muted-foreground">
							This service may have been removed, or the
							link you followed is stale.
						</p>
						<div className="mt-1 flex flex-wrap justify-center gap-2">
							<Button
								variant="outline"
								onClick={() => navigate("/services")}
							>
								<ArrowLeft className="mr-1 h-4 w-4" />
								Back to Services
							</Button>
							<Button
								variant="outline"
								disabled={isDetailFetching}
								onClick={() => void refetchDetail()}
							>
								<RefreshCw
									aria-hidden="true"
									className="mr-1 h-4 w-4"
								/>
								{isDetailFetching
									? "Retrying…"
									: "Try again"}
							</Button>
						</div>
					</CardContent>
				</Card>
			</PageWorkspace>
		);
	}

	const isFetching = isDetailFetching || isAttemptsFetching || isLogsFetching;

	return (
		<PageWorkspace className="mx-auto w-full max-w-7xl pb-1">
			{isAttemptsError && (
				<ListLoadError
					resource="service attempts"
					hasCachedData={(attemptsData?.items.length ?? 0) > 0}
					isRetrying={isFetching}
					onRetry={handleRefresh}
				/>
			)}
			{isLogsError && (
				<ListLoadError
					resource="service logs"
					hasCachedData={logEntries.length > 0}
					isRetrying={isFetching}
					onRetry={handleRefresh}
				/>
			)}
			<PageScrollArea aria-label="Service detail">
				<ServiceDetailView
					service={service}
					attempts={attemptsData?.items ?? []}
					logEntries={logEntries}
					logsTotal={logsTotal}
					hasOlderLogs={hasOlderLogs ?? false}
					isLoadingOlderLogs={isLoadingOlderLogs}
					onLoadOlderLogs={() => void fetchOlderLogs()}
					isPlatformAdmin={isPlatformAdmin}
					onAction={handleAction}
					onOpenInEditor={handleOpenInEditor}
				onBack={() => navigate("/services")}
				dateRange={dateRange}
				onDateRangeChange={setDateRange}
				streamingLogs={streamingLogs}
				isStreamConnected={isStreamConnected}
				onRefresh={handleRefresh}
				isRefreshing={isFetching}
			/>
			</PageScrollArea>
			<ServiceActionDialog
				pending={pendingConfirm}
				actionPending={isActionPending}
				actionError={confirmError}
				onConfirm={handleConfirmAction}
				onOpenChange={(open) => {
					if (!open) {
						setPendingConfirm(null);
						setConfirmError(null);
					}
				}}
			/>
		</PageWorkspace>
	);
}

/** Deep link to a service detail page from list surfaces. */
export function serviceDetailHref(service: ServiceListItem): string {
	return `/services/${encodeURIComponent(service.id)}`;
}
