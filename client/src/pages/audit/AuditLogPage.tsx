import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { AuditOutcome } from "./AuditOutcome";
import { AuditEventCards } from "./AuditEventCards";
import { AuditPagination } from "./AuditPagination";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { useAuditLog } from "@/hooks/useAuditLog";
import type { AuditLogEntry, GetAuditLogParams } from "@/hooks/useAuditLog";
import { getErrorMessage } from "@/lib/api-error";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AuditFilters } from "./AuditFilters";
import { AuditRetentionBanner } from "./AuditRetentionBanner";
import { AuditExportDialog } from "./AuditExportDialog";
import { AuditEventDrawer } from "@/components/audit/AuditEventDrawer";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { RefreshCw, AlertCircle, Loader2 } from "lucide-react";

function auditContext(entry: AuditLogEntry): string {
	const details = entry.details;
	if (!details) return "-";

	const path = typeof details.path === "string" ? details.path : null;
	const location =
		typeof details.location === "string" ? details.location : null;
	if (path) return location ? `${location} / ${path}` : path;

	const tableName =
		typeof details.table_name === "string" ? details.table_name : null;
	const tableId =
		typeof details.table_id === "string" ? details.table_id : null;
	if (tableName) return tableId ? `${tableName} / ${tableId}` : tableName;
	return tableId ?? "-";
}

export function AuditLogPage() {
	const { isPlatformAdmin } = useAuth();
	const navigate = useNavigate();
	const desktop = useMediaQuery("(min-width: 1024px)");

	const [actionGroup, setActionGroup] = useState("All");
	const [outcome, setOutcome] = useState("All");
	const [searchText, setSearchText] = useState("");
	const [startDate, setStartDate] = useState("");
	const [endDate, setEndDate] = useState("");
	const [continuationTokens, setContinuationTokens] = useState<string[]>([]);
	const [currentPage, setCurrentPage] = useState(0);
	const [exportOpen, setExportOpen] = useState(false);
	const [selected, setSelected] = useState<{
		entry: AuditLogEntry;
		opener: HTMLElement | null;
	}>();
	const [drawerOpen, setDrawerOpen] = useState(false);

	const openEvent = (entry: AuditLogEntry, opener: HTMLElement | null) => {
		setSelected({ entry, opener });
		setDrawerOpen(true);
	};

	const resetPagination = () => {
		setContinuationTokens([]);
		setCurrentPage(0);
	};

	const updateFilter = (setter: (value: string) => void, value: string) => {
		setter(value);
		resetPagination();
	};

	const queryParams = useMemo(() => {
		const params: GetAuditLogParams = { limit: 50 };
		if (actionGroup !== "All") params.action = actionGroup;
		if (outcome !== "All") params.outcome = outcome;
		if (searchText) params.search = searchText;
		if (startDate) params.start_date = startDate;
		if (endDate) params.end_date = endDate;
		if (continuationTokens[currentPage])
			params.continuation_token = continuationTokens[currentPage];
		return params;
	}, [
		actionGroup,
		outcome,
		searchText,
		startDate,
		endDate,
		currentPage,
		continuationTokens,
	]);

	const invalidDateRange = Boolean(
		startDate && endDate && startDate > endDate,
	);
	const { data, isLoading, isFetching, error, refetch } = useAuditLog(
		queryParams,
		!invalidDateRange,
		{ preservePageData: true },
	);

	const entries = data?.entries ?? [];
	const hasActiveFilters =
		actionGroup !== "All" ||
		outcome !== "All" ||
		Boolean(searchText || startDate || endDate);

	const clearFilters = () => {
		setActionGroup("All");
		setOutcome("All");
		setSearchText("");
		setStartDate("");
		setEndDate("");
		resetPagination();
	};

	const handleNextPage = () => {
		if (data?.continuation_token) {
			const newTokens = [...continuationTokens];
			newTokens[currentPage + 1] = data.continuation_token;
			setContinuationTokens(newTokens);
			setCurrentPage(currentPage + 1);
		}
	};

	const handlePreviousPage = () => {
		if (currentPage > 0) setCurrentPage(currentPage - 1);
	};

	if (!isPlatformAdmin) {
		return (
			<div className="container mx-auto py-8">
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						You do not have permission to view the audit log.
						Platform administrator access is required.
					</AlertDescription>
				</Alert>
				<Button onClick={() => navigate("/")} className="mt-4">
					Go to Home
				</Button>
			</div>
		);
	}

	return (
		<PageWorkspace className="mx-auto flex w-full min-w-0 max-w-[1400px] flex-col gap-5">
			{/* Header */}
			<div className="shrink-0 space-y-6">
				<div className="flex items-start justify-between gap-4">
					<div>
						<h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
							Audit Log
						</h1>
						<p className="mt-2 text-sm text-muted-foreground">
							Trace security decisions and administrative activity
							across the platform
						</p>
					</div>
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						disabled={isFetching || invalidDateRange}
						aria-label="Refresh audit log"
						className="size-11 shrink-0"
					>
						<RefreshCw
							className={`h-4 w-4 ${isFetching ? "animate-spin motion-reduce:animate-none" : ""}`}
						/>
					</Button>
				</div>

				<AuditRetentionBanner
					retention={data?.retention}
					onExport={() => setExportOpen(true)}
				/>

				<AuditFilters
					searchText={searchText}
					actionGroup={actionGroup}
					outcome={outcome}
					startDate={startDate}
					endDate={endDate}
					hasActiveFilters={hasActiveFilters}
					onClear={clearFilters}
					onChange={(key, value) =>
						updateFilter(
							{
								searchText: setSearchText,
								actionGroup: setActionGroup,
								outcome: setOutcome,
								startDate: setStartDate,
								endDate: setEndDate,
							}[key],
							value,
						)
					}
				/>
			</div>

			{/* Content */}
			<PageScrollArea className="lg:overflow-hidden">
				<div
					className="flex min-w-0 flex-col lg:h-full lg:min-h-0"
					hidden={invalidDateRange}
				>
					{error && (
						<Alert variant="destructive" className="mb-4">
							<AlertCircle className="h-4 w-4" />
							<AlertDescription>
								<p>
									Could not {data ? "refresh" : "load"} the
									audit log.{" "}
									{data
										? "Previously loaded records are still shown."
										: "Try again to load these events."}
								</p>
								<p className="mt-2 [overflow-wrap:anywhere]">
									{getErrorMessage(error, "Unknown error")}
								</p>
								<Button
									variant="outline"
									className="mt-3 min-h-11"
									disabled={isFetching || invalidDateRange}
									onClick={() => {
										void refetch();
									}}
								>
									Retry audit log
								</Button>
							</AlertDescription>
						</Alert>
					)}

					{error && !entries.length ? null : isLoading &&
					  !entries.length ? (
						<div
							role="status"
							aria-label="Loading audit log"
							className="flex items-center justify-center py-12"
						>
							<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
						</div>
					) : entries.length > 0 ? (
						<>
							{desktop ? (
								<DataTable
									aria-busy={isFetching ? "true" : undefined}
								>
									<DataTableHeader>
										<DataTableRow>
											<DataTableHead>
												Timestamp
											</DataTableHead>
											<DataTableHead>
												Action
											</DataTableHead>
											<DataTableHead>
												Outcome
											</DataTableHead>
											<DataTableHead>Actor</DataTableHead>
											<DataTableHead>
												Resource
											</DataTableHead>
											<DataTableHead>
												Context
											</DataTableHead>
											<DataTableHead>IP</DataTableHead>
										</DataTableRow>
									</DataTableHeader>
									<DataTableBody>
										{entries.map((entry: AuditLogEntry) => (
											<DataTableRow
												key={entry.id}
												clickable
												onClick={(event) =>
													openEvent(
														entry,
														event.currentTarget.querySelector(
															"button",
														),
													)
												}
											>
												<DataTableCell className="font-mono text-xs whitespace-nowrap">
													{new Date(
														entry.timestamp,
													).toLocaleString()}
												</DataTableCell>
												<DataTableCell>
													<button
														type="button"
														className="rounded-[var(--bf-radius-control)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
														onClick={(event) =>
															openEvent(
																entry,
																event.currentTarget,
															)
														}
													>
														<Badge variant="secondary">
															{entry.action}
														</Badge>
													</button>
												</DataTableCell>
												<DataTableCell>
													<AuditOutcome
														outcome={entry.outcome}
													/>
												</DataTableCell>
												<DataTableCell className="text-sm">
													{entry.actor.user_email ||
														entry.actor.user_name ||
														(entry.source !== "http"
															? `(${entry.source})`
															: "(unauthenticated)")}
												</DataTableCell>
												<DataTableCell className="text-sm text-muted-foreground">
													<div className="whitespace-normal [overflow-wrap:anywhere]">
														{entry.resource_type
															? `${entry.resource_type}${entry.resource_id ? ` / ${entry.resource_id}` : ""}`
															: "-"}
													</div>
												</DataTableCell>
												<DataTableCell className="max-w-80 text-sm text-muted-foreground">
													<div className="whitespace-normal [overflow-wrap:anywhere]">
														{auditContext(entry)}
													</div>
												</DataTableCell>
												<DataTableCell className="text-xs font-mono text-muted-foreground">
													{entry.ip_address || "-"}
												</DataTableCell>
											</DataTableRow>
										))}
									</DataTableBody>
								</DataTable>
							) : (
								<AuditEventCards
									entries={entries}
									context={auditContext}
									onOpen={openEvent}
								/>
							)}
							<AuditPagination
								count={entries.length}
								page={currentPage}
								hasNext={!!data?.continuation_token}
								pending={isFetching}
								onPrevious={handlePreviousPage}
								onNext={handleNextPage}
							/>
						</>
					) : (
						<Card>
							<CardContent className="flex flex-col items-center justify-center py-12 text-center">
								<h3 className="text-lg font-semibold">
									{hasActiveFilters
										? "No matching audit events"
										: "No audit events yet"}
								</h3>
								<p className="mt-2 text-sm text-muted-foreground">
									{hasActiveFilters
										? "Try adjusting your filters or date range."
										: "Administrative activity and security decisions will appear here."}
								</p>
							</CardContent>
						</Card>
					)}
				</div>
			</PageScrollArea>
			<AuditEventDrawer
				entry={selected?.entry}
				open={drawerOpen}
				onOpenChange={setDrawerOpen}
				returnFocus={selected?.opener}
			/>
			{exportOpen && (
				<AuditExportDialog
					defaultAction={actionGroup === "All" ? "" : actionGroup}
					defaultStartDate={startDate}
					defaultEndDate={endDate}
					onClose={() => setExportOpen(false)}
				/>
			)}
		</PageWorkspace>
	);
}

export default AuditLogPage;
