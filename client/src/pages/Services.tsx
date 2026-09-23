import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { LayoutGrid, RefreshCw, Table as TableIcon } from "lucide-react";

import { useIsDesktop } from "@/hooks/useMediaQuery";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useSearch } from "@/hooks/useSearch";
import { SearchBox } from "@/components/search/SearchBox";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { ListLoadError } from "@/components/layout/ListLoadError";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { ServiceListSurface } from "@/components/services/ServiceListSurface";
import {
	ServiceActionDialog,
	type PendingServiceAction,
} from "@/components/services/ServiceActionDialog";
import {
	CONFIRM_ACTIONS,
	useServiceAction,
} from "@/components/services/useServiceAction";
import { useServicesList } from "@/components/services/useServiceQueries";
import type {
	ServiceAction,
	ServiceListItem,
} from "@/services/services";
import { serviceDetailHref } from "@/pages/ServiceDetail";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

type Organization = components["schemas"]["OrganizationPublic"];

/**
 * Supervised services list (live).
 *
 * Rows come from `GET /api/services` with the active-attempt summary,
 * last exit, and memory embedded — no per-row fan-out. Refresh converges
 * through action invalidation, window-focus refetch, and the Refresh
 * button below. No browser polling loops.
 */
export function Services() {
	const navigate = useNavigate();
	const { isPlatformAdmin } = useAuth();
	const isDesktop = useIsDesktop();

	const [searchTerm, setSearchTerm] = useState("");
	const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
	const [stateFilter, setStateFilter] = useState("all");
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);

	const {
		data: servicesData,
		isLoading,
		isError,
		isFetching,
		refetch,
	} = useServicesList();
	const services = useMemo<ServiceListItem[]>(
		() => servicesData?.items ?? [],
		[servicesData],
	);

	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});
	const getOrgName = (orgId: string | null | undefined): string => {
		if (!orgId) return "Global";
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return org?.name || orgId;
	};

	const searchedServices = useSearch(services, searchTerm, [
		"workflow_name",
		"workflow_path",
	]);
	const filteredServices = useMemo(() => {
		const byState =
			stateFilter === "all"
				? searchedServices
				: searchedServices.filter(
						(s) => s.observed_state === stateFilter,
					);
		// undefined = all orgs; null = global (unscoped) services.
		if (filterOrgId === undefined) return byState;
		return byState.filter((s) => s.organization_id === filterOrgId);
	}, [searchedServices, stateFilter, filterOrgId]);

	const activeFilterCount = [
		searchTerm.trim(),
		stateFilter !== "all",
		filterOrgId !== undefined,
	].filter(Boolean).length;

	const { runAction, isActionPending } = useServiceAction();
	const [pendingConfirm, setPendingConfirm] =
		useState<PendingServiceAction | null>(null);
	const [confirmError, setConfirmError] = useState<string | null>(null);

	const handleAction = (
		service: ServiceListItem,
		action: ServiceAction,
	) => {
		if (CONFIRM_ACTIONS.has(action)) {
			setConfirmError(null);
			setPendingConfirm({
				service,
				action: action as PendingServiceAction["action"],
			});
			return;
		}
		void runAction({ service, action }).catch(() => {
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

	return (
		<PageWorkspace className="mx-auto w-full max-w-7xl pb-1 xl:h-full xl:min-h-0">
			<ListPageHeader
				title="Services"
				description="Supervised long-lived services"
				className="flex-row flex-nowrap sm:flex-wrap"
				actionsClassName="shrink-0 self-start"
				actions={
					<div className="flex shrink-0 items-center gap-2 self-start">
						<Button
							type="button"
							variant="outline"
							size="sm"
							disabled={isFetching}
							onClick={() => void refetch()}
							aria-label="Refresh services"
						>
							<RefreshCw
								aria-hidden="true"
								className="h-4 w-4"
							/>
							Refresh
						</Button>
						{isDesktop ? (
							<ToggleGroup
								aria-label="Services layout"
								type="single"
								value={viewMode}
								onValueChange={(value: string) =>
									value &&
									setViewMode(value as "grid" | "table")
								}
							>
								<ToggleGroupItem
									value="grid"
									aria-label="Grid view"
									size="lg"
								>
									<LayoutGrid className="h-4 w-4" />
								</ToggleGroupItem>
								<ToggleGroupItem
									value="table"
									aria-label="Table view"
									size="lg"
								>
									<TableIcon className="h-4 w-4" />
								</ToggleGroupItem>
							</ToggleGroup>
						) : undefined}
					</div>
				}
			/>

			<ListToolbar className="items-stretch">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search by name or source path..."
					className="w-full min-w-0 sm:flex-1"
				/>
				<Select value={stateFilter} onValueChange={setStateFilter}>
					<SelectTrigger
						aria-label="Filter services by state"
						className="w-full min-w-0 sm:w-48"
					>
						<SelectValue placeholder="All states" />
					</SelectTrigger>
					<SelectContent position="popper">
						<SelectItem value="all">All states</SelectItem>
						<SelectItem value="running">Running</SelectItem>
						<SelectItem value="starting">Starting</SelectItem>
						<SelectItem value="restarting">
							Restarting
						</SelectItem>
						<SelectItem value="stopping">Stopping</SelectItem>
						<SelectItem value="stopped">Stopped</SelectItem>
						<SelectItem value="crash_loop">
							Crash loop
						</SelectItem>
					</SelectContent>
				</Select>
				{isPlatformAdmin && (
					<div className="w-full min-w-0 sm:w-64">
						<OrganizationSelect
							aria-label="Service organization scope"
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={true}
							placeholder="All organizations"
						/>
					</div>
				)}
				{activeFilterCount > 0 && (
					<div className="flex w-full items-center justify-between gap-3">
						<p
							className="text-sm text-muted-foreground"
							role="status"
						>
							{activeFilterCount}{" "}
							{activeFilterCount === 1 ? "filter" : "filters"}{" "}
							applied
						</p>
						<Button
							type="button"
							variant="ghost"
							className="min-h-11"
							onClick={() => {
								setSearchTerm("");
								setStateFilter("all");
								setFilterOrgId(undefined);
							}}
						>
							Clear filters
						</Button>
					</div>
				)}
			</ListToolbar>

			<PageScrollArea aria-label="Service list" className="space-y-4">
				{isError && (
					<ListLoadError
						resource="services"
						hasCachedData={services.length > 0}
						isRetrying={isFetching}
						onRetry={() => void refetch()}
					/>
				)}
				{(!isError || services.length > 0) && (
					<ServiceListSurface
						services={filteredServices}
						viewMode={isDesktop ? viewMode : "grid"}
						isLoading={isLoading}
						isPlatformAdmin={isPlatformAdmin}
						getOrgName={getOrgName}
						onSelect={(service) =>
							navigate(serviceDetailHref(service))
						}
						getDetailHref={serviceDetailHref}
						onAction={handleAction}
						emptySearchActive={activeFilterCount > 0}
					/>
				)}
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
