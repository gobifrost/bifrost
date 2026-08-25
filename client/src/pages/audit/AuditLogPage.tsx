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
	DataTableFooter,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import {
	RefreshCw,
	ChevronLeft,
	ChevronRight,
	AlertCircle,
	Loader2,
} from "lucide-react";

const ACTION_GROUPS = [
	{ value: "All", label: "All actions" },
	{ value: "auth.", label: "Authentication" },
	{ value: "user.", label: "Users" },
	{ value: "role.", label: "Roles" },
	{ value: "organization.", label: "Organizations" },
	{ value: "policy.deny", label: "Policy denials" },
];

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

const OUTCOMES = [
	{ value: "All", label: "All outcomes" },
	{ value: "success", label: "Success" },
	{ value: "failure", label: "Failure" },
];

export function AuditLogPage() {
	const { isPlatformAdmin } = useAuth();
	const navigate = useNavigate();

	const [actionGroup, setActionGroup] = useState("All");
	const [outcome, setOutcome] = useState("All");
	const [searchText, setSearchText] = useState("");
	const [startDate, setStartDate] = useState("");
	const [endDate, setEndDate] = useState("");
	const [continuationTokens, setContinuationTokens] = useState<string[]>([]);
	const [currentPage, setCurrentPage] = useState(0);

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

	const { data, isLoading, error, refetch } = useAuditLog(queryParams);

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
					Return to Dashboard
				</Button>
			</div>
		);
	}

	return (
		<div className="h-[calc(100vh-8rem)] flex flex-col space-y-6">
			{/* Header */}
			<div className="flex items-start justify-between gap-4">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">
						Audit Log
					</h1>
					<p className="mt-2 text-muted-foreground">
						Trace security decisions and administrative activity
						across the platform
					</p>
				</div>
				<Button
					variant="outline"
					size="icon"
					onClick={() => refetch()}
					disabled={isLoading}
					aria-label="Refresh audit log"
					className="shrink-0"
				>
					<RefreshCw
						className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`}
					/>
				</Button>
			</div>

			{/* Filters */}
			<div className="space-y-3">
				<Input
					type="search"
					aria-label="Search audit events"
					placeholder="Search path, table, action, or IP…"
					value={searchText}
					onChange={(e) =>
						updateFilter(setSearchText, e.target.value)
					}
					className="w-full"
				/>

				<div className="grid gap-3 sm:grid-cols-2 lg:flex lg:items-center">
					<Select
						value={actionGroup}
						onValueChange={(value) =>
							updateFilter(setActionGroup, value)
						}
					>
						<SelectTrigger
							className="w-full lg:w-[200px]"
							aria-label="Action filter"
						>
							<SelectValue placeholder="Action" />
						</SelectTrigger>
						<SelectContent>
							{ACTION_GROUPS.map((g) => (
								<SelectItem key={g.value} value={g.value}>
									{g.label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>

					<Select
						value={outcome}
						onValueChange={(value) =>
							updateFilter(setOutcome, value)
						}
					>
						<SelectTrigger
							className="w-full lg:w-[160px]"
							aria-label="Outcome filter"
						>
							<SelectValue placeholder="Outcome" />
						</SelectTrigger>
						<SelectContent>
							{OUTCOMES.map((o) => (
								<SelectItem key={o.value} value={o.value}>
									{o.label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>

					<div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 gap-y-2 sm:col-span-2 lg:ml-auto lg:flex">
						<span className="text-sm text-muted-foreground">
							From
						</span>
						<Input
							type="date"
							aria-label="Start date"
							value={startDate}
							onChange={(e) =>
								updateFilter(setStartDate, e.target.value)
							}
							className="min-w-0 lg:w-[150px]"
						/>
						<span className="text-sm text-muted-foreground">
							To
						</span>
						<Input
							type="date"
							aria-label="End date"
							value={endDate}
							onChange={(e) =>
								updateFilter(setEndDate, e.target.value)
							}
							className="min-w-0 lg:w-[150px]"
						/>
					</div>

					{hasActiveFilters && (
						<Button
							variant="ghost"
							onClick={clearFilters}
							className="justify-self-start lg:shrink-0"
						>
							Clear filters
						</Button>
					)}
				</div>
			</div>

			{/* Content */}
			<div className="flex-1 overflow-hidden flex flex-col min-h-0">
				{error && (
					<Alert variant="destructive" className="mb-4">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							Failed to load audit log:{" "}
							{getErrorMessage(error, "Unknown error")}
						</AlertDescription>
					</Alert>
				)}

				{isLoading && !entries.length ? (
					<div className="flex items-center justify-center py-12">
						<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
					</div>
				) : entries.length > 0 ? (
					<DataTable>
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead>Timestamp</DataTableHead>
								<DataTableHead>Action</DataTableHead>
								<DataTableHead>Outcome</DataTableHead>
								<DataTableHead>Actor</DataTableHead>
								<DataTableHead>Resource</DataTableHead>
								<DataTableHead>Context</DataTableHead>
								<DataTableHead>IP</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{entries.map((entry: AuditLogEntry) => (
								<DataTableRow key={entry.id}>
									<DataTableCell className="font-mono text-xs whitespace-nowrap">
										{new Date(
											entry.timestamp,
										).toLocaleString()}
									</DataTableCell>
									<DataTableCell>
										<Badge variant="secondary">
											{entry.action}
										</Badge>
									</DataTableCell>
									<DataTableCell>
										<Badge
											variant={
												entry.outcome === "failure"
													? "destructive"
													: "default"
											}
											className="capitalize"
										>
											{entry.outcome}
										</Badge>
									</DataTableCell>
									<DataTableCell className="text-sm">
										{entry.actor.user_email ||
											entry.actor.user_name ||
											(entry.source !== "http"
												? `(${entry.source})`
												: "(unauthenticated)")}
									</DataTableCell>
									<DataTableCell className="text-sm text-muted-foreground">
										{entry.resource_type
											? `${entry.resource_type}${entry.resource_id ? ` / ${entry.resource_id.slice(0, 8)}` : ""}`
											: "-"}
									</DataTableCell>
									<DataTableCell
										className="max-w-80 truncate text-sm text-muted-foreground"
										title={auditContext(entry)}
									>
										{auditContext(entry)}
									</DataTableCell>
									<DataTableCell className="text-xs font-mono text-muted-foreground">
										{entry.ip_address || "-"}
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
						<DataTableFooter>
							<DataTableRow>
								<DataTableCell
									colSpan={4}
									className="text-sm text-muted-foreground"
								>
									{entries.length} event
									{entries.length !== 1 ? "s" : ""} on this
									page
								</DataTableCell>
								<DataTableCell
									colSpan={3}
									className="text-right"
								>
									<div className="flex gap-2 justify-end">
										<Button
											variant="outline"
											size="sm"
											onClick={handlePreviousPage}
											disabled={currentPage === 0}
										>
											<ChevronLeft className="h-4 w-4 mr-2" />
											Previous
										</Button>
										<Button
											variant="outline"
											size="sm"
											onClick={handleNextPage}
											disabled={!data?.continuation_token}
										>
											Next
											<ChevronRight className="h-4 w-4 ml-2" />
										</Button>
									</div>
								</DataTableCell>
							</DataTableRow>
						</DataTableFooter>
					</DataTable>
				) : (
					<Card>
						<CardContent className="flex flex-col items-center justify-center py-12 text-center">
							<h3 className="text-lg font-semibold">
								No events found
							</h3>
							<p className="mt-2 text-sm text-muted-foreground">
								Try adjusting your filters or date range
							</p>
						</CardContent>
					</Card>
				)}
			</div>
		</div>
	);
}

export default AuditLogPage;
