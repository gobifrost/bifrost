import { useState, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
	Plus,
	RefreshCw,
	Webhook,
	Calendar,
	Zap,
	Globe,
	Building2,
	Pencil,
	Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { useAuth } from "@/contexts/AuthContext";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import {
	useEventSources,
	useUpdateEventSource,
	useDeleteEventSource,
	type EventSource,
	type EventSourceType,
} from "@/services/events";
import { formatDistanceToNow } from "date-fns";
import { EventSourceDetail } from "@/components/events/EventSourceDetail";
import { CreateEventSourceDialog } from "@/components/events/CreateEventSourceDialog";
import { EditEventSourceDialog } from "@/components/events/EditEventSourceDialog";

function getSourceTypeIcon(type: EventSourceType) {
	switch (type) {
		case "webhook":
			return <Webhook className="h-4 w-4" />;
		case "schedule":
			return <Calendar className="h-4 w-4" />;
		case "topic":
			return <Zap className="h-4 w-4" />;
	}
}

function getSourceTypeLabel(type: EventSourceType) {
	switch (type) {
		case "webhook":
			return "Webhook";
		case "schedule":
			return "Schedule";
		case "topic":
			return "Topic";
	}
}

type StatusFilter = "all" | "active" | "inactive";

export function Events() {
	const { isPlatformAdmin } = useAuth();
	const { sourceId } = useParams<{ sourceId?: string }>();
	const navigate = useNavigate();
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

	const updateMutation = useUpdateEventSource();
	const deleteMutation = useDeleteEventSource();

	// Toggle active status for a source
	const handleToggleActive = async (
		source: EventSource,
		e: React.MouseEvent,
	) => {
		e.stopPropagation(); // Prevent row click
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

	// Edit event source
	const handleEdit = (source: EventSource, e: React.MouseEvent) => {
		e.stopPropagation();
		setSourceToEdit(source);
		setEditDialogOpen(true);
	};

	const handleEditClose = () => {
		setEditDialogOpen(false);
		setSourceToEdit(null);
	};

	// Delete event source
	const handleDelete = (source: EventSource, e: React.MouseEvent) => {
		e.stopPropagation();
		setSourceToDelete(source);
		setDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!sourceToDelete) return;

		try {
			await deleteMutation.mutateAsync({
				params: { path: { source_id: sourceToDelete.id } },
			});
			toast.success("Event source deleted");
			refetch();
		} catch {
			toast.error("Failed to delete event source");
		} finally {
			setDeleteDialogOpen(false);
			setSourceToDelete(null);
		}
	};

	// filterOrgId: undefined = all, null = global only, string = org UUID
	const { data, isLoading, refetch } = useEventSources(
		isPlatformAdmin
			? {
					scope: filterOrgId === null ? "global" : undefined,
					organizationId: typeof filterOrgId === "string" ? filterOrgId : undefined,
				}
			: undefined,
	);
	const sources = useMemo(() => data?.items || [], [data?.items]);

	// Apply search filter
	const searchFilteredSources = useSearch(sources, searchTerm, [
		"name",
		"organization_name",
	]);

	// Apply status filter
	const filteredSources = useMemo(() => {
		if (statusFilter === "all") return searchFilteredSources;
		if (statusFilter === "active")
			return searchFilteredSources.filter((s) => s.is_active);
		return searchFilteredSources.filter((s) => !s.is_active);
	}, [searchFilteredSources, statusFilter]);

	// Calculate stats for display
	const stats = useMemo(() => {
		const total = sources.length;
		const active = sources.filter((s) => s.is_active).length;
		const inactive = total - active;
		return { total, active, inactive };
	}, [sources]);

	const handleCreateSuccess = () => {
		setIsCreateDialogOpen(false);
		refetch();
	};

	const handleSourceClick = (source: EventSource) => {
		navigate(`/event-sources/${source.id}`);
	};

	const handleCloseDetail = () => {
		navigate("/event-sources");
		refetch();
	};

	// If we have a sourceId in the URL, show the detail view
	if (sourceId) {
		return (
			<EventSourceDetail
				sourceId={sourceId}
				onClose={handleCloseDetail}
			/>
		);
	}

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">
						Event Sources
					</h1>
					<p className="mt-2 text-muted-foreground">
						Manage webhook endpoints and event triggers for your
						workflows
					</p>
				</div>
				<div className="flex gap-2">
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						title="Refresh"
					>
						<RefreshCw
							className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`}
						/>
					</Button>
					{isPlatformAdmin && (
						<Button
							variant="outline"
							size="icon"
							onClick={() => setIsCreateDialogOpen(true)}
							title="Create Event Source"
						>
							<Plus className="h-4 w-4" />
						</Button>
					)}
				</div>
			</div>

			{/* Search and Filters */}
			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search event sources..."
					className="flex-1"
				/>
				{isPlatformAdmin && (
					<div className="w-64">
						<OrganizationSelect
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={true}
							placeholder="All organizations"
						/>
					</div>
				)}
			</div>

			{/* Status Tabs */}
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

			{/* Content */}
			{isLoading ? (
				<div className="space-y-2">
					{[...Array(5)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : filteredSources.length === 0 ? (
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
									size="icon"
									className="mt-4"
									onClick={() => setIsCreateDialogOpen(true)}
									title="Create Event Source"
								>
									<Plus className="h-4 w-4" />
								</Button>
							)}
					</CardContent>
				</Card>
			) : (
				<div className="flex-1 min-h-0">
					<DataTable className="max-h-full">
						<DataTableHeader>
							<DataTableRow>
								{isPlatformAdmin && (
									<DataTableHead className="w-0 whitespace-nowrap">Organization</DataTableHead>
								)}
								<DataTableHead>Name</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">Type</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right">
									Events (24h)
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right">
									Rate limited (24h)
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">Created</DataTableHead>
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
							{filteredSources.map((source) => (
								<DataTableRow
									key={source.id}
									clickable
									onClick={() => handleSourceClick(source)}
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
											{getSourceTypeIcon(
												source.source_type,
											)}
											<div className="flex flex-col">
												<span>{source.name}</span>
												{source.source_type === "topic" && source.event_type && (
													<span className="text-xs text-muted-foreground font-mono">
														{source.event_type}
													</span>
												)}
											</div>
										</div>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										{getSourceTypeLabel(source.source_type)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right">
										{source.event_count_24h || 0}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right">
										{source.webhook && source.webhook.rate_limited_count_24h > 0 ? (
											<Badge
												variant="destructive"
												className="text-xs"
												title="Webhooks rejected by per-source rate limit in the last 24h"
											>
												{source.webhook.rate_limited_count_24h}
											</Badge>
										) : (
											<span className="text-muted-foreground">—</span>
										)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-muted-foreground">
										{formatDistanceToNow(
											new Date(source.created_at),
											{
												addSuffix: true,
											},
										)}
									</DataTableCell>
									{isPlatformAdmin && (
										<>
											<DataTableCell className="w-0 whitespace-nowrap text-right">
												<Switch
													checked={source.is_active}
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
												/>
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-right">
												<div className="flex items-center justify-end gap-1">
													<Button
														variant="ghost"
														size="icon"
														onClick={(e) =>
															handleEdit(
																source,
																e,
															)
														}
														title="Edit event source"
													>
														<Pencil className="h-4 w-4" />
													</Button>
													<Button
														variant="ghost"
														size="icon"
														onClick={(e) =>
															handleDelete(
																source,
																e,
															)
														}
														title="Delete event source"
													>
														<Trash2 className="h-4 w-4" />
													</Button>
												</div>
											</DataTableCell>
										</>
									)}
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				</div>
			)}

			{/* Create Dialog */}
			<CreateEventSourceDialog
				open={isCreateDialogOpen}
				onOpenChange={setIsCreateDialogOpen}
				onSuccess={handleCreateSuccess}
			/>

			{/* Edit Dialog */}
			<EditEventSourceDialog
				source={sourceToEdit}
				open={editDialogOpen}
				onOpenChange={handleEditClose}
			/>

			{/* Delete Confirmation */}
			<AlertDialog
				open={deleteDialogOpen}
				onOpenChange={setDeleteDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Event Source</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete "
							{sourceToDelete?.name}"? This will also remove all
							subscriptions and event history. This action cannot
							be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
