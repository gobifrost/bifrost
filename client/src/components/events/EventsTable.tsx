import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
	Inbox,
	Clock,
	CheckCircle2,
	XCircle,
	Loader2,
	Radio,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { format, subHours, subDays } from "date-fns";
import {
	useEvents,
	type Event,
	type EventSource,
	type EventStatus,
} from "@/services/events";
import { EventDetailDialog } from "./EventDetailDialog";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { useEventStream } from "@/hooks/useEventStream";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { getGraphEventType } from "@/lib/graph-source";

interface EventsTableProps {
	sourceId: string;
	source?: EventSource;
	initialEventId?: string;
}

type StatusFilter = "all" | "received" | "processing" | "completed" | "failed";
type DateRangeFilter = "1h" | "24h" | "7d" | "30d" | "all";

function getStatusIcon(status: EventStatus) {
	switch (status) {
		case "received":
			return <Clock className="h-4 w-4 text-muted-foreground" />;
		case "processing":
			return <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />;
		case "completed":
			return <CheckCircle2 className="h-4 w-4 text-green-500" />;
		case "failed":
			return <XCircle className="h-4 w-4 text-destructive" />;
	}
}

function getStatusLabel(status: EventStatus) {
	switch (status) {
		case "received":
			return "Received";
		case "processing":
			return "Processing";
		case "completed":
			return "Completed";
		case "failed":
			return "Failed";
	}
}

function getStatusVariant(
	status: EventStatus,
): "default" | "secondary" | "destructive" | "outline" {
	switch (status) {
		case "completed":
			return "default";
		case "failed":
			return "destructive";
		case "processing":
			return "outline";
		default:
			return "secondary";
	}
}

function getDateRangeFilter(range: DateRangeFilter): string | undefined {
	const now = new Date();
	switch (range) {
		case "1h":
			return subHours(now, 1).toISOString();
		case "24h":
			return subHours(now, 24).toISOString();
		case "7d":
			return subDays(now, 7).toISOString();
		case "30d":
			return subDays(now, 30).toISOString();
		case "all":
			return undefined;
	}
}

// Extended Event type with animation flag
interface EventWithMeta extends Event {
	_isNew?: boolean;
}

export function EventsTable({ sourceId, source, initialEventId }: EventsTableProps) {
	const navigate = useNavigate();
	const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
	const [dateRange, setDateRange] = useState<DateRangeFilter>("24h");
	const [searchTerm, setSearchTerm] = useState("");

	// Build filter params
	const filterParams = useMemo(() => {
		const params: {
			status?: EventStatus;
			since?: string;
		} = {};

		if (statusFilter !== "all") {
			params.status = statusFilter;
		}

		const sinceDate = getDateRangeFilter(dateRange);
		if (sinceDate) {
			params.since = sinceDate;
		}

		return params;
	}, [statusFilter, dateRange]);

	const { data, isLoading } = useEvents(sourceId, filterParams);
	const events = useMemo(() => {
		return ((data?.items || []) as EventWithMeta[]).map((event) => ({
			...event,
			event_type: getGraphEventType(source, event),
		}));
	}, [data?.items, source]);

	// Connect to WebSocket for real-time updates
	const { isConnected } = useEventStream(sourceId);

	// Track events that should animate (new events from WebSocket)
	// Using CSS animations which auto-remove after completion
	const animatingIds = useMemo(() => {
		return new Set(events.filter((e) => e._isNew).map((e) => e.id));
	}, [events]);

	// Apply local search filter (for event type)
	const filteredEvents = useSearch(events, searchTerm, [
		"event_type",
		"source_ip",
	]);

	// Derive selected event from URL param (no useState needed)
	const selectedEvent = useMemo(() => {
		if (!initialEventId) return null;
		return events.find((e) => e.id === initialEventId) || null;
	}, [initialEventId, events]);

	const handleEventClick = (event: Event) => {
		// Navigate to URL-based route for the event
		navigate(`/event-sources/${sourceId}/events/${event.id}`);
	};

	const handleCloseDetail = () => {
		// Navigate back to the source detail (without event)
		navigate(`/event-sources/${sourceId}`);
	};

	return (
		<div className="flex-1 flex flex-col min-h-0">
			{/* Compact filter row - everything on one line */}
			<div className="flex items-center gap-3 mb-3 flex-wrap">
				{/* Search by event type */}
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search events..."
					className="w-48"
				/>

				{/* Date range filter */}
				<Select
					value={dateRange}
					onValueChange={(v) => setDateRange(v as DateRangeFilter)}
				>
					<SelectTrigger className="w-32">
						<SelectValue placeholder="Date range" />
					</SelectTrigger>
					<SelectContent>
						<SelectItem value="1h">Last hour</SelectItem>
						<SelectItem value="24h">Last 24h</SelectItem>
						<SelectItem value="7d">Last 7 days</SelectItem>
						<SelectItem value="30d">Last 30 days</SelectItem>
						<SelectItem value="all">All time</SelectItem>
					</SelectContent>
				</Select>

				{/* Status filter as compact ToggleGroup */}
				<ToggleGroup
					type="single"
					value={statusFilter}
					onValueChange={(v) =>
						v && setStatusFilter(v as StatusFilter)
					}
				>
					<ToggleGroupItem value="all" size="sm">
						All
					</ToggleGroupItem>
					<ToggleGroupItem value="completed" size="sm">
						Completed
					</ToggleGroupItem>
					<ToggleGroupItem value="processing" size="sm">
						Processing
					</ToggleGroupItem>
					<ToggleGroupItem value="failed" size="sm">
						Failed
					</ToggleGroupItem>
				</ToggleGroup>

				{/* Spacer */}
				<div className="flex-1" />

				{/* Live indicator */}
				{isConnected && (
					<Tooltip>
						<TooltipTrigger asChild>
							<div className="flex items-center gap-1 text-xs text-green-600">
								<Radio className="h-3 w-3 animate-pulse" />
								<span>Live</span>
							</div>
						</TooltipTrigger>
						<TooltipContent>
							<p>Connected to real-time updates</p>
						</TooltipContent>
					</Tooltip>
				)}
			</div>

			{/* Table content - takes remaining space */}
			<div className="flex-1 min-h-0">
				{isLoading ? (
					<div className="space-y-2">
						{[...Array(5)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : filteredEvents.length === 0 ? (
					<div className="flex flex-col items-center justify-center py-12 text-center">
						<Inbox className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							{searchTerm || statusFilter !== "all"
								? "No events match your filters"
								: "No Events Yet"}
						</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							{searchTerm || statusFilter !== "all"
								? "Try adjusting your search or filters"
								: "Events will appear here when webhooks are received."}
						</p>
					</div>
				) : (
					<DataTable className="max-h-full">
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead>Event Type</DataTableHead>
								<DataTableHead>Status</DataTableHead>
								<DataTableHead>Source IP</DataTableHead>
								<DataTableHead className="text-right">
									Deliveries
								</DataTableHead>
								<DataTableHead>Received</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{filteredEvents.map((event) => (
								<DataTableRow
									key={event.id}
									clickable
									onClick={() => handleEventClick(event)}
									className={cn(
										animatingIds.has(event.id) &&
											"animate-highlight",
									)}
								>
									<DataTableCell className="font-medium">
										{event.event_type ? (
											<Badge variant="outline">
												{event.event_type}
											</Badge>
										) : (
											<span className="text-muted-foreground">
												—
											</span>
										)}
									</DataTableCell>
									<DataTableCell>
										<div className="flex items-center gap-1.5">
											{getStatusIcon(event.status)}
											<Badge
												variant={getStatusVariant(
													event.status,
												)}
											>
												{getStatusLabel(event.status)}
											</Badge>
										</div>
									</DataTableCell>
									<DataTableCell className="font-mono text-sm">
										{event.source_ip || "—"}
									</DataTableCell>
									<DataTableCell className="text-right">
										<div className="flex items-center justify-end gap-2">
											{event.success_count > 0 && (
												<span className="text-green-600">
													{event.success_count} ok
												</span>
											)}
											{event.failed_count > 0 && (
												<span className="text-destructive">
													{event.failed_count} failed
												</span>
											)}
											{event.delivery_count === 0 && (
												<span className="text-muted-foreground">
													—
												</span>
											)}
										</div>
									</DataTableCell>
									<DataTableCell className="text-muted-foreground">
										{format(
											new Date(event.received_at),
											"MMM d, HH:mm:ss",
										)}
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				)}
			</div>

			{/* Event Detail Dialog */}
			<EventDetailDialog
				event={selectedEvent}
				onClose={handleCloseDetail}
			/>
		</div>
	);
}
