import { EventSourceActions } from "@/components/events/EventSourceActions";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
	Plus,
	RefreshCw,
	Webhook,
	TriangleAlert,
	Building2,
	Globe,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SearchBox } from "@/components/search/SearchBox";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useSearch } from "@/hooks/useSearch";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { ListLoadError } from "@/components/layout/ListLoadError";
import { PageWorkspace } from "@/components/layout/PageWorkspace";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useAuth } from "@/contexts/AuthContext";
import {
	useDeleteEventSource,
	useEventSources,
	useUpdateEventSource,
	type EventSource,
} from "@/services/events";
import { EventSourceDetail } from "@/components/events/EventSourceDetail";
import { CreateEventSourceDialog } from "@/components/events/CreateEventSourceDialog";
import { EditEventSourceDialog } from "@/components/events/EditEventSourceDialog";
import { getGraphSourceSummary } from "@/lib/graph-source";
import { formatDistanceToNow } from "date-fns";
import { getErrorMessage } from "@/lib/api-error";
import { isMicrosoftGraphSource } from "@/lib/graph-source";
import { EventSourceCard } from "./events/EventSourceCard";

type StatusFilter = "all" | "active" | "inactive";

export function Events() {
	const { isPlatformAdmin } = useAuth();
	const { sourceId } = useParams<{ sourceId?: string }>();
	const navigate = useNavigate();
	const compactLayout = useMediaQuery("(max-width: 1023px)");
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [searchTerm, setSearchTerm] = useState("");
	const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
	const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [sourceToEdit, setSourceToEdit] = useState<EventSource | null>(null);
	const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
	const [sourceToDelete, setSourceToDelete] = useState<EventSource | null>(
		null,
	);
	const [deleteError, setDeleteError] = useState<string | null>(null);
	const [isDeleting, setIsDeleting] = useState(false);

	const updateMutation = useUpdateEventSource();
	const deleteMutation = useDeleteEventSource();

	const { data, isLoading, isError, isFetching, refetch } = useEventSources(
		isPlatformAdmin
			? {
					scope: filterOrgId === null ? "global" : undefined,
					organizationId:
						typeof filterOrgId === "string"
							? filterOrgId
							: undefined,
				}
			: undefined,
	);

	const sources = useMemo(() => data?.items || [], [data?.items]);
	const searchFilteredSources = useSearch(sources, searchTerm, [
		"name",
		"organization_name",
	]);
	const filteredSources = useMemo(() => {
		if (statusFilter === "all") return searchFilteredSources;
		if (statusFilter === "active")
			return searchFilteredSources.filter((s) => s.is_active);
		return searchFilteredSources.filter((s) => !s.is_active);
	}, [searchFilteredSources, statusFilter]);
	const stats = useMemo(() => {
		const total = sources.length;
		const active = sources.filter((s) => s.is_active).length;
		return { total, active, inactive: total - active };
	}, [sources]);
	const hasCachedSources = data !== undefined;

	const handleToggleActive = async (
		source: EventSource,
		e: React.MouseEvent,
	) => {
		e.stopPropagation();
		try {
			await updateMutation.mutateAsync({
				params: { path: { source_id: source.id } },
				body: { is_active: !source.is_active },
			});
			toast.success(
				source.is_active
					? "Event source deactivated"
					: "Event source activated",
			);
		} catch {
			toast.error("Failed to update event source");
		}
	};

	const handleEdit = (source: EventSource, e: React.MouseEvent) => {
		e.stopPropagation();
		setSourceToEdit(source);
		setEditDialogOpen(true);
	};

	const handleEditClose = () => {
		setEditDialogOpen(false);
		setSourceToEdit(null);
	};

	const handleDelete = (source: EventSource, e: React.MouseEvent) => {
		e.stopPropagation();
		setSourceToDelete(source);
		setDeleteError(null);
		setDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!sourceToDelete || isDeleting) return;
		setIsDeleting(true);
		setDeleteError(null);
		try {
			await deleteMutation.mutateAsync({
				params: { path: { source_id: sourceToDelete.id } },
			});
			toast.success("Event source deleted");
			refetch();
			setDeleteDialogOpen(false);
			setSourceToDelete(null);
		} catch (error) {
			const message = getErrorMessage(
				error,
				"Failed to delete event source",
			);
			setDeleteError(message);
			toast.error(message);
		} finally {
			setIsDeleting(false);
		}
	};

	const handleCreateSuccess = () => {
		setIsCreateDialogOpen(false);
		refetch();
	};

	const handleCloseDetail = () => {
		navigate("/event-sources");
		refetch();
	};

	if (sourceId) {
		return (
			<PageWorkspace className="max-w-7xl mx-auto">
				<EventSourceDetail
					sourceId={sourceId}
					onClose={handleCloseDetail}
				/>
			</PageWorkspace>
		);
	}

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			<ListPageHeader
				title="Event Sources"
				description="Manage webhook endpoints and event triggers for your workflows"
				actions={
					<>
						<Button
							variant="outline"
							className="min-h-11 gap-2 px-4"
							onClick={() => refetch()}
						>
							<RefreshCw
								className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`}
							/>
							<span>Refresh</span>
						</Button>
						{isPlatformAdmin && (
							<Button
								variant="outline"
								className="min-h-11 gap-2 px-4"
								onClick={() => setIsCreateDialogOpen(true)}
							>
								<Plus className="h-4 w-4" />
								<span>Create event source</span>
							</Button>
						)}
					</>
				}
			/>

			<ListToolbar>
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search event sources..."
					className="w-full sm:flex-1"
				/>
				{isPlatformAdmin && (
					<div className="w-full sm:w-64">
						<OrganizationSelect
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={true}
							placeholder="All organizations"
						/>
					</div>
				)}
			</ListToolbar>

			<Tabs
				value={statusFilter}
				onValueChange={(v) => setStatusFilter(v as StatusFilter)}
			>
				<TabsList>
					<TabsTrigger value="all">All ({stats.total})</TabsTrigger>
					<TabsTrigger value="active">
						Active ({stats.active})
					</TabsTrigger>
					<TabsTrigger value="inactive">
						Inactive ({stats.inactive})
					</TabsTrigger>
				</TabsList>
			</Tabs>

			{isError && (
				<ListLoadError
					resource="event sources"
					hasCachedData={hasCachedSources}
					isRetrying={isFetching}
					onRetry={() => void refetch()}
				/>
			)}

			{isLoading && !hasCachedSources ? (
				<div className="space-y-2">
					{[...Array(5)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : !isError || hasCachedSources ? (
				<div className="flex-1 min-h-0">
					{filteredSources.length === 0 ? (
						<Card>
							<CardContent className="flex flex-col items-center justify-center py-12 text-center">
								<Webhook className="h-12 w-12 text-muted-foreground" />
								<h3 className="mt-4 text-lg font-semibold">
									{searchTerm || statusFilter !== "all"
										? "No event sources match your filters"
										: "No Event Sources"}
								</h3>
								<p className="mt-2 text-sm text-muted-foreground">
									{searchTerm || statusFilter !== "all"
										? "Try adjusting your search term or filter"
										: "Create your first event source to start receiving webhooks."}
								</p>
								{isPlatformAdmin &&
									!searchTerm &&
									statusFilter === "all" && (
										<Button
											variant="outline"
											className="mt-4 min-h-11 gap-2 px-4"
											onClick={() =>
												setIsCreateDialogOpen(true)
											}
										>
											<Plus className="h-4 w-4" />
											<span>Create event source</span>
										</Button>
									)}
							</CardContent>
						</Card>
					) : compactLayout ? (
						<div className="space-y-3">
							{filteredSources.map((source) => (
								<EventSourceCard
									key={source.id}
									source={source}
									isPlatformAdmin={isPlatformAdmin}
									onToggleActive={handleToggleActive}
									onEdit={handleEdit}
									onDelete={handleDelete}
									updatePending={updateMutation.isPending}
								/>
							))}
						</div>
					) : (
						<DataTable className="max-h-full">
							<DataTableHeader>
								<DataTableRow>
									{isPlatformAdmin && (
										<DataTableHead className="w-0 whitespace-nowrap">
											Organization
										</DataTableHead>
									)}
									<DataTableHead>Name</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap">
										Type
									</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap text-right">
										Events (24h)
									</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap text-right">
										Rate limited (24h)
									</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap">
										Created
									</DataTableHead>
									{isPlatformAdmin && (
										<>
											<DataTableHead className="w-0 whitespace-nowrap text-right">
												Status
											</DataTableHead>
											<DataTableHead className="w-0 whitespace-nowrap text-right" />
										</>
									)}
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{filteredSources.map((source) => {
									const graphSummary =
										getGraphSourceSummary(source);
									return (
										<DataTableRow
											key={source.id}
											clickable
											href={`/event-sources/${source.id}`}
											onClick={() =>
												navigate(
													`/event-sources/${source.id}`,
												)
											}
										>
											{isPlatformAdmin && (
												<DataTableCell className="w-0 whitespace-nowrap">
													{source.organization_id ? (
														<Badge
															variant="outline"
															className="text-xs"
														>
															<Building2 className="mr-1 h-3 w-3" />
															{source.organization_name ||
																"Organization"}
														</Badge>
													) : (
														<Badge
															variant="default"
															className="text-xs"
														>
															<Globe className="mr-1 h-3 w-3" />
															Global
														</Badge>
													)}
												</DataTableCell>
											)}
											<DataTableCell className="font-medium">
												<div className="flex items-center gap-2">
													<div className="min-w-0 flex flex-col">
														<Link
															to={`/event-sources/${source.id}`}
															onClick={(event) =>
																event.stopPropagation()
															}
															className="inline-flex min-h-11 items-center rounded-[var(--bf-radius-control)] [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
														>
															{source.name}
														</Link>
														{graphSummary && (
															<span className="flex min-w-0 items-center gap-1.5 text-xs font-normal text-muted-foreground">
																<span className="truncate">
																	{
																		graphSummary.userLabel
																	}{" "}
																	·{" "}
																	{
																		graphSummary.resourceLabel
																	}{" "}
																	·{" "}
																	{
																		graphSummary.changeLabel
																	}
																</span>
																{graphSummary.health !==
																	"connected" && (
																	<span className="inline-flex shrink-0 items-center gap-1 text-[var(--bf-warning)]">
																		<TriangleAlert className="h-3 w-3" />
																		Needs
																		attention
																	</span>
																)}
															</span>
														)}
														{source.source_type ===
															"topic" &&
															source.event_type && (
																<span className="font-mono text-xs text-muted-foreground">
																	{
																		source.event_type
																	}
																</span>
															)}
													</div>
												</div>
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap">
												{isMicrosoftGraphSource(source)
													? "Microsoft Graph"
													: source.source_type ===
														  "topic"
														? "Topic"
														: source.source_type ===
															  "schedule"
															? "Schedule"
															: "Webhook"}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-right">
												{source.event_count_24h || 0}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-right">
												{source.webhook &&
												source.webhook
													.rate_limited_count_24h >
													0 ? (
													<Badge
														variant="destructive"
														className="text-xs"
														title="Webhooks rejected by per-source rate limit in the last 24h"
													>
														{
															source.webhook
																.rate_limited_count_24h
														}
													</Badge>
												) : (
													<span className="text-muted-foreground">
														—
													</span>
												)}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-muted-foreground">
												{formatDistanceToNow(
													new Date(source.created_at),
													{ addSuffix: true },
												)}
											</DataTableCell>
											{isPlatformAdmin && (
												<>
													<DataTableCell className="w-0 whitespace-nowrap text-right">
														<Switch
															checked={
																source.is_active
															}
															onCheckedChange={() => {}}
															onClick={(e) =>
																handleToggleActive(
																	source,
																	e,
																)
															}
															disabled={
																updateMutation.isPending
															}
															aria-label={`Toggle ${source.name} active state`}
														/>
													</DataTableCell>
													<DataTableCell className="w-0 whitespace-nowrap text-right">
														<EventSourceActions
															source={source}
															onEdit={handleEdit}
															onDelete={
																handleDelete
															}
														/>
													</DataTableCell>
												</>
											)}
										</DataTableRow>
									);
								})}
							</DataTableBody>
						</DataTable>
					)}
				</div>
			) : null}

			<CreateEventSourceDialog
				open={isCreateDialogOpen}
				onOpenChange={setIsCreateDialogOpen}
				onSuccess={handleCreateSuccess}
			/>
			<EditEventSourceDialog
				source={sourceToEdit}
				open={editDialogOpen}
				onOpenChange={handleEditClose}
			/>

			<AlertDialog
				open={deleteDialogOpen}
				onOpenChange={(open) => {
					if (isDeleting && !open) return;
					setDeleteDialogOpen(open);
					if (!open) {
						setSourceToDelete(null);
						setDeleteError(null);
					}
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Event Source</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete "
							{sourceToDelete?.name}"? This will also remove all
							subscriptions and event history. Provider-managed
							sources are removed from the provider first; if that
							fails, the Bifrost source is retained so you can
							retry. This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					{deleteError && (
						<div
							role="alert"
							className="rounded-[var(--bf-radius-surface)] border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
						>
							{deleteError}
						</div>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel disabled={isDeleting}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={(e) => {
								e.preventDefault();
								void handleConfirmDelete();
							}}
							disabled={isDeleting}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{isDeleting ? "Deleting…" : "Delete"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
