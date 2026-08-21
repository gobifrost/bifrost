import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";
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
];

const OUTCOMES = [
	{ value: "All", label: "All outcomes" },
	{ value: "success", label: "Success" },
	{ value: "failure", label: "Failure" },
];

export function AuditLogPage() {
	const { hasSelectedCapability, selectedTarget } = useAuthorizationBoundary();
	const canReadAudit =
		hasSelectedCapability("audit.read") && selectedTarget?.kind === "platform";
	const navigate = useNavigate();

	const [actionGroup, setActionGroup] = useState("All");
	const [outcome, setOutcome] = useState("All");
	const [searchText, setSearchText] = useState("");
	const [startDate, setStartDate] = useState("");
	const [endDate, setEndDate] = useState("");
	const [continuationTokens, setContinuationTokens] = useState<string[]>([]);
	const [currentPage, setCurrentPage] = useState(0);

	const queryParams = useMemo(() => {
		const params: GetAuditLogParams = { limit: 50 };
		if (actionGroup !== "All") params.action = actionGroup;
		if (outcome !== "All") params.outcome = outcome;
		if (startDate) params.start_date = startDate;
		if (endDate) params.end_date = endDate;
		if (continuationTokens[currentPage])
			params.continuation_token = continuationTokens[currentPage];
		return params;
	}, [actionGroup, outcome, startDate, endDate, currentPage, continuationTokens]);

	const { data, isLoading, error, refetch } = useAuditLog(queryParams);

	const filteredEntries = useMemo(() => {
		if (!data?.entries) return [];
		if (!searchText) return data.entries;
		const q = searchText.toLowerCase();
		return data.entries.filter((entry: AuditLogEntry) => {
			return (
				entry.action.toLowerCase().includes(q) ||
				(entry.actor.user_email?.toLowerCase().includes(q) ?? false) ||
				(entry.actor.user_name?.toLowerCase().includes(q) ?? false) ||
				(entry.resource_type?.toLowerCase().includes(q) ?? false) ||
				(entry.ip_address?.toLowerCase().includes(q) ?? false)
			);
		});
	}, [data, searchText]);

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

	if (!canReadAudit) {
		return (
			<div className="container mx-auto py-8">
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						Select Global from Working in to view the platform audit log.
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
			<div>
				<h1 className="text-4xl font-extrabold tracking-tight">Audit Log</h1>
				<p className="mt-2 text-muted-foreground">
					Review authentication, user, role, and organization events
				</p>
			</div>

			{/* Filters */}
			<div className="flex items-center gap-4 flex-wrap">
				<Select value={actionGroup} onValueChange={setActionGroup}>
					<SelectTrigger className="w-[200px]">
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

				<Select value={outcome} onValueChange={setOutcome}>
					<SelectTrigger className="w-[160px]">
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

				<Input
					placeholder="Search action, actor, IP…"
					value={searchText}
					onChange={(e) => setSearchText(e.target.value)}
					className="flex-1 max-w-md"
				/>

				<Input
					type="date"
					value={startDate}
					onChange={(e) => setStartDate(e.target.value)}
					className="w-[160px]"
				/>
				<Input
					type="date"
					value={endDate}
					onChange={(e) => setEndDate(e.target.value)}
					className="w-[160px]"
				/>

				<Button
					variant="outline"
					size="icon"
					onClick={() => refetch()}
					disabled={isLoading}
					title="Refresh"
				>
					<RefreshCw
						className={`h-4 w-4 ${isLoading ? "animate-spin" : ""}`}
					/>
				</Button>
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

				{isLoading && !filteredEntries.length ? (
					<div className="flex items-center justify-center py-12">
						<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
					</div>
				) : filteredEntries.length > 0 ? (
					<DataTable>
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead>Timestamp</DataTableHead>
								<DataTableHead>Action</DataTableHead>
								<DataTableHead>Outcome</DataTableHead>
								<DataTableHead>Actor</DataTableHead>
								<DataTableHead>Resource</DataTableHead>
								<DataTableHead>IP</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{filteredEntries.map((entry: AuditLogEntry) => (
								<DataTableRow key={entry.id}>
									<DataTableCell className="font-mono text-xs whitespace-nowrap">
										{new Date(entry.timestamp).toLocaleString()}
									</DataTableCell>
									<DataTableCell>
										<Badge variant="secondary">{entry.action}</Badge>
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
									<DataTableCell className="text-xs font-mono text-muted-foreground">
										{entry.ip_address || "-"}
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
						<DataTableFooter>
							<DataTableRow>
								<DataTableCell colSpan={3} className="text-sm text-muted-foreground">
									{filteredEntries.length} event
									{filteredEntries.length !== 1 ? "s" : ""} on this page
								</DataTableCell>
								<DataTableCell colSpan={3} className="text-right">
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
							<h3 className="text-lg font-semibold">No events found</h3>
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
