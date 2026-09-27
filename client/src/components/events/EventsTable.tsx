import { useState, useMemo } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Inbox, Radio, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { format, subHours, subDays } from "date-fns";
import {
	useEvents,
	type Event,
	type EventSource,
	type EventStatus,
} from "@/services/events";
import { EventDetailDialog } from "./EventDetailDialog";
import { EventStatusBadge } from "./EventStatusBadge";
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
import { getGraphEventType } from "@/lib/graph-source";

interface EventsTableProps {
	sourceId: string;
	source?: EventSource;
	initialEventId?: string;
}
type StatusFilter = "all" | EventStatus;
type DateRangeFilter = "1h" | "24h" | "7d" | "30d" | "all";
interface EventWithMeta extends Event {
	_isNew?: boolean;
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

function EventDeliveryCounts({ event }: { event: Event }) {
	return (
		<span className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
			<span>{event.delivery_count} total</span>
			{event.success_count > 0 && (
				<span className="text-[var(--bf-success)]">
					{event.success_count} ok
				</span>
			)}
			{event.failed_count > 0 && (
				<span className="text-[var(--bf-danger)]">
					{event.failed_count} failed
				</span>
			)}
		</span>
	);
}

function EventRecord({ event, href }: { event: EventWithMeta; href: string }) {
	return (
		<li
			className={`min-w-0 space-y-3 rounded-[var(--bf-radius-surface)] border bg-card p-4 ${event._isNew ? "motion-safe:animate-[highlight_2s_ease-out]" : ""}`}
		>
			<div className="flex flex-wrap items-center justify-between gap-2">
				<EventStatusBadge status={event.status} />
				<span className="text-sm text-muted-foreground">
					{format(new Date(event.received_at), "MMM d, HH:mm:ss")}
				</span>
			</div>
			<Link
				to={href}
				className="flex min-h-11 items-center rounded-[var(--bf-radius-control)] font-medium leading-6 [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
			>
				{event.event_type || "View event"}
			</Link>
			<dl className="space-y-3 text-sm leading-6">
				<div>
					<dt className="text-muted-foreground">Source IP</dt>
					<dd className="font-mono [overflow-wrap:anywhere]">
						{event.source_ip || "—"}
					</dd>
				</div>
				<div>
					<dt className="text-muted-foreground">Deliveries</dt>
					<dd>
						<EventDeliveryCounts event={event} />
					</dd>
				</div>
			</dl>
		</li>
	);
}

export function EventsTable({
	sourceId,
	source,
	initialEventId,
}: EventsTableProps) {
	const navigate = useNavigate();
	const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
	const [dateRange, setDateRange] = useState<DateRangeFilter>("24h");
	const [searchTerm, setSearchTerm] = useState("");
	const filterParams = useMemo(
		() => ({
			...(statusFilter === "all" ? {} : { status: statusFilter }),
			since: getDateRangeFilter(dateRange),
		}),
		[statusFilter, dateRange],
	);
	const { data, isLoading, isError, isFetching, refetch } = useEvents(
		sourceId,
		filterParams,
	);
	const events = useMemo(
		() =>
			((data?.items || []) as EventWithMeta[]).map((event) => ({
				...event,
				event_type: getGraphEventType(source, event),
			})),
		[data?.items, source],
	);
	const { isConnected } = useEventStream(sourceId);
	const filteredEvents = useSearch(events, searchTerm, [
		"event_type",
		"source_ip",
	]);
	const selectedEvent = useMemo(
		() =>
			initialEventId
				? events.find((event) => event.id === initialEventId) || null
				: null,
		[initialEventId, events],
	);
	const hasFilters = Boolean(
		searchTerm || statusFilter !== "all" || dateRange !== "all",
	);
	const eventHref = (event: Event) =>
		`/event-sources/${sourceId}/events/${event.id}`;
	return (
		<div className="@container/events flex min-h-0 min-w-0 flex-1 flex-col gap-4">
			<div className="flex min-w-0 flex-wrap items-center gap-3">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search events..."
					aria-label="Search events"
					className="min-w-0 basis-full @[40rem]/events:basis-64 @[40rem]/events:flex-1 [&_input]:min-h-11"
				/>
				<Select
					value={dateRange}
					onValueChange={(value) =>
						setDateRange(value as DateRangeFilter)
					}
				>
					<SelectTrigger
						aria-label="Date range"
						className="min-h-11 min-w-0 flex-1 @[40rem]/events:w-40 @[40rem]/events:flex-none"
					>
						<SelectValue />
					</SelectTrigger>
					<SelectContent>
						<SelectItem value="1h">Last hour</SelectItem>
						<SelectItem value="24h">Last 24h</SelectItem>
						<SelectItem value="7d">Last 7 days</SelectItem>
						<SelectItem value="30d">Last 30 days</SelectItem>
						<SelectItem value="all">All time</SelectItem>
					</SelectContent>
				</Select>
				<Select
					value={statusFilter}
					onValueChange={(value) =>
						setStatusFilter(value as StatusFilter)
					}
				>
					<SelectTrigger
						aria-label="Event status"
						className="min-h-11 min-w-0 flex-1 @[40rem]/events:w-40 @[40rem]/events:flex-none"
					>
						<SelectValue />
					</SelectTrigger>
					<SelectContent>
						<SelectItem value="all">All statuses</SelectItem>
						<SelectItem value="received">Received</SelectItem>
						<SelectItem value="processing">Processing</SelectItem>
						<SelectItem value="completed">Completed</SelectItem>
						<SelectItem value="failed">Failed</SelectItem>
					</SelectContent>
				</Select>
				<span
					role="status"
					className={`inline-flex basis-full items-center gap-1.5 text-sm @[40rem]/events:basis-auto ${isConnected ? "text-[var(--bf-success)]" : "text-muted-foreground"}`}
					title={
						isConnected
							? "Connected to real-time updates"
							: "Previously loaded events remain visible while updates reconnect"
					}
				>
					<Radio
						aria-hidden="true"
						className={`size-4 ${isConnected ? "motion-safe:animate-pulse" : ""}`}
					/>
					{isConnected ? "Live" : "Connecting to live updates…"}
				</span>
			</div>
			{isError && (
				<div
					role="alert"
					className="space-y-3 rounded-[var(--bf-radius-surface)] border p-4 text-sm leading-6"
				>
					<p>
						Could not load events.{" "}
						{data
							? "Previously loaded events remain visible."
							: "Try again."}
					</p>
					<Button
						type="button"
						variant="outline"
						className="min-h-11"
						disabled={isFetching}
						onClick={() => void refetch()}
					>
						{isFetching && (
							<Loader2 className="size-4 motion-safe:animate-spin" />
						)}
						Retry events
					</Button>
				</div>
			)}
			<div className="min-h-0 min-w-0 flex-1">
				{isLoading && !data ? (
					<div className="space-y-2">
						{Array.from({ length: 5 }, (_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : isError && !data ? null : !filteredEvents.length ? (
					<div className="flex flex-col items-center py-12 text-center">
						<Inbox
							aria-hidden="true"
							className="size-10 text-muted-foreground"
						/>
						<h3 className="mt-4 text-lg font-semibold">
							{hasFilters
								? "No events match your filters"
								: "No Events Yet"}
						</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							{hasFilters
								? "Try adjusting your search, date range or status."
								: "Events will appear here when received."}
						</p>
						{hasFilters && (
							<Button
								type="button"
								variant="outline"
								className="mt-4 min-h-11"
								onClick={() => {
									setSearchTerm("");
									setStatusFilter("all");
									setDateRange("all");
								}}
							>
								Clear filters
							</Button>
						)}
					</div>
				) : (
					<>
						<ul className="space-y-3 @[60rem]/events:hidden">
							{filteredEvents.map((event) => (
								<EventRecord
									key={event.id}
									event={event}
									href={eventHref(event)}
								/>
							))}
						</ul>
						<div className="hidden min-h-0 flex-1 @[60rem]/events:block">
							<DataTable className="max-h-full">
								<DataTableHeader>
									<DataTableRow>
										<DataTableHead>
											Event Type
										</DataTableHead>
										<DataTableHead>Status</DataTableHead>
										<DataTableHead>Source IP</DataTableHead>
										<DataTableHead>
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
											href={eventHref(event)}
											onClick={() =>
												navigate(eventHref(event))
											}
											className={
												event._isNew
													? "motion-safe:animate-[highlight_2s_ease-out]"
													: undefined
											}
										>
											<DataTableCell>
												<Link
													to={eventHref(event)}
													onClick={(e) =>
														e.stopPropagation()
													}
													className="flex min-h-11 items-center rounded-[var(--bf-radius-control)] font-medium [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
												>
													{event.event_type ||
														"View event"}
												</Link>
											</DataTableCell>
											<DataTableCell>
												<EventStatusBadge
													status={event.status}
												/>
											</DataTableCell>
											<DataTableCell className="font-mono text-sm [overflow-wrap:anywhere]">
												{event.source_ip || "—"}
											</DataTableCell>
											<DataTableCell>
												<EventDeliveryCounts
													event={event}
												/>
											</DataTableCell>
											<DataTableCell className="text-sm text-muted-foreground">
												{format(
													new Date(event.received_at),
													"MMM d, HH:mm:ss",
												)}
											</DataTableCell>
										</DataTableRow>
									))}
								</DataTableBody>
							</DataTable>
						</div>
					</>
				)}
			</div>
			<EventDetailDialog
				event={selectedEvent}
				eventId={initialEventId}
				onClose={() => navigate(`/event-sources/${sourceId}`)}
			/>
		</div>
	);
}
