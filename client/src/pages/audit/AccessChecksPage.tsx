import { useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
	AlertCircle,
	ChevronLeft,
	Loader2,
	ShieldCheck,
	TriangleAlert,
} from "lucide-react";

import { AuditEventDrawer } from "@/components/audit/AuditEventDrawer";
import { RunUserLabel } from "@/components/audit/RunUserLabel";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import {
	useAuditGroups,
	useAuditLog,
	type AuditGroupBy,
	type AuditLogEntry,
	type AuditLogGroup,
	type GetAuditLogParams,
} from "@/hooks/useAuditLog";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useOrganizations } from "@/hooks/useOrganizations";
import {
	checkKindTitle,
	checkResourceTitle,
	stepTitle,
	stoppedStep,
	storedTrace,
} from "@/lib/access-trace";
import { getErrorMessage } from "@/lib/api-error";
import { formatRelativeTime } from "@/lib/utils";
import { useAuthorization } from "@/services/authorization";

import { AuditPagination } from "./AuditPagination";

/** Report-only checks that would have been denied. */
const WOULD_DENY = { action: "access.check", outcome: "failure" } as const;
/** The `key` (and filter value) of a group whose entries have no value. */
const NONE = "none";

const GROUPINGS: { value: AuditGroupBy; label: string }[] = [
	{ value: "workflow", label: "Workflow" },
	{ value: "resource_type", label: "Resource Type" },
	{ value: "organization", label: "Organization" },
];

function grouping(value: string | null) {
	return GROUPINGS.find((item) => item.value === value) ?? GROUPINGS[0];
}

/** The list filter that selects one group's entries. */
function groupFilter(groupBy: AuditGroupBy, key: string): GetAuditLogParams {
	if (groupBy === "resource_type") return { resource_type: key };
	if (groupBy === "organization") return { organization_id: key };
	return { workflow_id: key };
}

/** The step a check would stop at, in amber; `labelled` names it where no column header does. */
function StopChip({
	entry,
	labelled = false,
}: {
	entry: AuditLogEntry;
	labelled?: boolean;
}) {
	const trace = storedTrace(entry.details);
	const step = trace && stoppedStep(trace);
	if (!step)
		return labelled ? null : (
			<span className="text-muted-foreground">—</span>
		);
	return (
		<span className="inline-flex items-center gap-1.5 rounded-full bg-[var(--bf-warning-soft)] px-2 py-0.5 text-xs font-medium text-[var(--bf-warning)]">
			<TriangleAlert aria-hidden="true" className="size-3.5 shrink-0" />
			{labelled && "Would Stop Here · "}
			{stepTitle(step.label)}
		</span>
	);
}

function RelativeTime({ value }: { value: string }) {
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<time dateTime={value} className="whitespace-nowrap">
					{formatRelativeTime(value)}
				</time>
			</TooltipTrigger>
			<TooltipContent>{new Date(value).toLocaleString()}</TooltipContent>
		</Tooltip>
	);
}

/** The row's own keyboard target; the row itself opens on click. */
function OpenButton({
	onOpen,
	children,
}: {
	onOpen: (opener: HTMLElement) => void;
	children: ReactNode;
}) {
	return (
		<button
			type="button"
			className="min-w-0 rounded-[var(--bf-radius-control)] text-left font-medium [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
			onClick={(event) => onOpen(event.currentTarget)}
		>
			{children}
		</button>
	);
}

function LoadState({
	loading,
	error,
	refetch,
	label,
}: {
	loading: boolean;
	error: unknown;
	refetch: () => unknown;
	label: string;
}) {
	if (error)
		return (
			<Alert variant="destructive">
				<AlertCircle aria-hidden="true" className="size-4" />
				<AlertDescription className="space-y-3">
					<p className="[overflow-wrap:anywhere]">
						{getErrorMessage(
							error,
							"The access checks could not load.",
						)}
					</p>
					<Button
						variant="outline"
						className="min-h-11"
						onClick={() => void refetch()}
					>
						Try Again
					</Button>
				</AlertDescription>
			</Alert>
		);
	if (loading)
		return (
			<div
				role="status"
				aria-label={label}
				className="flex items-center justify-center py-12"
			>
				<Loader2 className="size-8 animate-spin text-muted-foreground motion-reduce:animate-none" />
			</div>
		);
	return null;
}

function GroupList({
	groups,
	heading,
	name,
	onOpen,
	desktop,
}: {
	groups: AuditLogGroup[];
	heading: string;
	name: (group: AuditLogGroup) => string;
	onOpen: (group: AuditLogGroup) => void;
	desktop: boolean;
}) {
	if (!desktop)
		return (
			<ol aria-label="Groups" className="space-y-3">
				{groups.map((group) => (
					<li
						key={group.key ?? NONE}
						className="relative space-y-2 rounded-[var(--bf-radius-surface)] border bg-card p-4"
					>
						<h2 className="text-sm">
							<button
								type="button"
								className="text-left font-semibold [overflow-wrap:anywhere] after:absolute after:inset-0 after:rounded-[var(--bf-radius-surface)] focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-ring"
								onClick={() => onOpen(group)}
							>
								{name(group)}
							</button>
						</h2>
						<p className="text-sm text-muted-foreground">
							{group.count}{" "}
							{group.count === 1 ? "check" : "checks"} · last seen{" "}
							<RelativeTime value={group.last_seen} />
						</p>
						<StopChip entry={group.sample} labelled />
					</li>
				))}
			</ol>
		);
	return (
		<DataTable>
			<DataTableHeader>
				<DataTableRow>
					<DataTableHead>{heading}</DataTableHead>
					<DataTableHead className="text-right">Checks</DataTableHead>
					<DataTableHead>Last Seen</DataTableHead>
					<DataTableHead>Would Stop Here</DataTableHead>
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{groups.map((group) => (
					<DataTableRow
						key={group.key ?? NONE}
						clickable
						onClick={() => onOpen(group)}
					>
						<DataTableCell>
							<OpenButton onOpen={() => onOpen(group)}>
								{name(group)}
							</OpenButton>
						</DataTableCell>
						<DataTableCell className="text-right font-mono tabular-nums">
							{group.count}
						</DataTableCell>
						<DataTableCell className="text-muted-foreground">
							<RelativeTime value={group.last_seen} />
						</DataTableCell>
						<DataTableCell>
							<StopChip entry={group.sample} />
						</DataTableCell>
					</DataTableRow>
				))}
			</DataTableBody>
		</DataTable>
	);
}

function CheckList({
	entries,
	onOpen,
	desktop,
}: {
	entries: AuditLogEntry[];
	onOpen: (entry: AuditLogEntry, opener: HTMLElement | null) => void;
	desktop: boolean;
}) {
	const organization = (entry: AuditLogEntry) =>
		entry.actor.organization_name ??
		(entry.actor.organization_id ? "—" : "Global");
	if (!desktop)
		return (
			<ol aria-label="Checks" className="space-y-3">
				{entries.map((entry) => (
					<li
						key={entry.id}
						className="relative space-y-3 rounded-[var(--bf-radius-surface)] border bg-card p-4 text-sm"
					>
						<dl className="grid grid-cols-2 gap-3">
							<div>
								<dt className="text-xs text-muted-foreground">
									Organization
								</dt>
								<dd className="mt-1 [overflow-wrap:anywhere]">
									{organization(entry)}
								</dd>
							</div>
							<div>
								<dt className="text-xs text-muted-foreground">
									Time
								</dt>
								<dd className="mt-1">
									<button
										type="button"
										className="text-left font-semibold after:absolute after:inset-0 after:rounded-[var(--bf-radius-surface)] focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-ring"
										onClick={(event) =>
											onOpen(entry, event.currentTarget)
										}
									>
										{new Date(
											entry.timestamp,
										).toLocaleString()}
									</button>
								</dd>
							</div>
							<div className="col-span-2">
								<dt className="text-xs text-muted-foreground">
									Run User
								</dt>
								<dd className="mt-1">
									<RunUserLabel entry={entry} />
								</dd>
							</div>
							<div className="col-span-2">
								<dt className="text-xs text-muted-foreground">
									Resource Type
								</dt>
								<dd className="mt-1">
									{entry.resource_type
										? checkResourceTitle(
												entry.resource_type,
												entry.details,
											)
										: "—"}
								</dd>
							</div>
						</dl>
						<StopChip entry={entry} labelled />
					</li>
				))}
			</ol>
		);
	return (
		<DataTable>
			<DataTableHeader>
				<DataTableRow>
					<DataTableHead>Organization</DataTableHead>
					<DataTableHead>Time</DataTableHead>
					<DataTableHead>Run User</DataTableHead>
					<DataTableHead>Resource Type</DataTableHead>
					<DataTableHead>Would Stop Here</DataTableHead>
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{entries.map((entry) => (
					<DataTableRow
						key={entry.id}
						clickable
						onClick={(event) =>
							onOpen(
								entry,
								event.currentTarget.querySelector("button"),
							)
						}
					>
						<DataTableCell>{organization(entry)}</DataTableCell>
						<DataTableCell className="whitespace-nowrap">
							<OpenButton
								onOpen={(opener) => onOpen(entry, opener)}
							>
								{new Date(entry.timestamp).toLocaleString()}
							</OpenButton>
						</DataTableCell>
						<DataTableCell>
							<RunUserLabel entry={entry} />
						</DataTableCell>
						<DataTableCell>
							{entry.resource_type
								? checkResourceTitle(
										entry.resource_type,
										entry.details,
									)
								: "—"}
						</DataTableCell>
						<DataTableCell>
							<StopChip entry={entry} />
						</DataTableCell>
					</DataTableRow>
				))}
			</DataTableBody>
		</DataTable>
	);
}

/** One group's checks, newest first, a page at a time. */
function GroupChecks({
	groupBy,
	groupKey,
	onOpen,
	desktop,
}: {
	groupBy: AuditGroupBy;
	groupKey: string;
	onOpen: (entry: AuditLogEntry, opener: HTMLElement | null) => void;
	desktop: boolean;
}) {
	const [tokens, setTokens] = useState<string[]>([]);
	const [page, setPage] = useState(0);
	const { data, isLoading, isFetching, error, refetch } = useAuditLog(
		{
			...WOULD_DENY,
			...groupFilter(groupBy, groupKey),
			limit: 50,
			continuation_token: tokens[page],
		},
		true,
		{ preservePageData: true },
	);
	const entries = data?.entries ?? [];

	if (error || (isLoading && !data))
		return (
			<LoadState
				loading={isLoading}
				error={error}
				refetch={refetch}
				label="Loading checks"
			/>
		);
	if (entries.length === 0)
		return (
			<EmptyState
				icon={ShieldCheck}
				title="No Checks in This Group"
				description="None of the recorded would-deny checks are in this group now."
			/>
		);
	return (
		<>
			<CheckList entries={entries} onOpen={onOpen} desktop={desktop} />
			<AuditPagination
				count={entries.length}
				page={page}
				hasNext={!!data?.continuation_token}
				pending={isFetching}
				onPrevious={() => setPage(page - 1)}
				onNext={() => {
					if (!data?.continuation_token) return;
					const next = [...tokens];
					next[page + 1] = data.continuation_token;
					setTokens(next);
					setPage(page + 1);
				}}
			/>
		</>
	);
}

/**
 * Report-only access checks that would have been denied, grouped by
 * workflow, resource type or organization; a group opens its checks and a
 * check opens in the event drawer. The URL keeps the grouping (`group_by`)
 * and the open group (`key`, "none" for entries with no value). The API
 * decides what the caller may read.
 */
export function AccessChecksPage() {
	const [params, setParams] = useSearchParams();
	const current = grouping(params.get("group_by"));
	const groupBy = current.value;
	const groupKey = params.get("key");
	const desktop = useMediaQuery("(min-width: 1024px)");
	const authorization = useAuthorization();

	const groupsQuery = useAuditGroups(groupBy, WOULD_DENY);
	const groups = groupsQuery.data?.groups ?? [];
	const organizations = useOrganizations({
		enabled: authorization.canAnywhere("organizations.read"),
	}).data;
	const organizationName = (id: string) =>
		organizations?.find((organization) => organization.id === id)?.name;

	const [selected, setSelected] = useState<{
		entry: AuditLogEntry;
		opener: HTMLElement | null;
	}>();
	const [drawerOpen, setDrawerOpen] = useState(false);

	const groupName = (key: string | null, sample?: AuditLogEntry): string => {
		if (groupBy === "workflow") {
			if (key === null) return "No Workflow";
			return sample?.workflow_name ?? key;
		}
		if (groupBy === "resource_type")
			return key === null ? "No Resource Type" : checkKindTitle(key);
		if (key === null) return "Global";
		return sample?.actor.organization_name ?? organizationName(key) ?? key;
	};

	const show = (next: Record<string, string>) => {
		const search = new URLSearchParams();
		if (next.group_by !== GROUPINGS[0].value || next.key)
			search.set("group_by", next.group_by);
		if (next.key) search.set("key", next.key);
		setParams(search);
	};

	const openGroup = groupKey
		? groups.find((group) => (group.key ?? NONE) === groupKey)
		: undefined;
	const backSearch =
		groupBy === GROUPINGS[0].value ? "" : `?group_by=${groupBy}`;

	return (
		<PageWorkspace className="mx-auto flex w-full min-w-0 max-w-[1400px] flex-col gap-5">
			<div className="shrink-0 space-y-4">
				<div>
					<h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
						Access Checks
					</h1>
					<p className="mt-2 text-sm text-muted-foreground">
						Checks that would have been denied if enforcement were
						on. Nothing is blocked yet.
					</p>
				</div>
				{groupKey ? (
					<div className="space-y-1">
						<Link
							to={{ search: backSearch }}
							className="inline-flex min-h-11 items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
						>
							<ChevronLeft
								aria-hidden="true"
								className="size-4"
							/>
							Back
						</Link>
						<h2 className="text-lg font-semibold [overflow-wrap:anywhere]">
							{groupName(
								groupKey === NONE ? null : groupKey,
								openGroup?.sample,
							)}
						</h2>
						{openGroup && (
							<p className="text-sm text-muted-foreground">
								{openGroup.count} would-deny{" "}
								{openGroup.count === 1 ? "check" : "checks"}
							</p>
						)}
					</div>
				) : (
					<div className="flex flex-wrap items-center gap-3">
						<span
							id="access-checks-group-by"
							className="text-sm font-medium"
						>
							Group By
						</span>
						<ToggleGroup
							type="single"
							value={groupBy}
							onValueChange={(value: string) => {
								if (value) show({ group_by: value });
							}}
							aria-labelledby="access-checks-group-by"
							className="justify-start"
						>
							{GROUPINGS.map((item) => (
								<ToggleGroupItem
									key={item.value}
									value={item.value}
									className="min-h-11 sm:min-h-9"
								>
									{item.label}
								</ToggleGroupItem>
							))}
						</ToggleGroup>
					</div>
				)}
			</div>

			<PageScrollArea className="lg:overflow-hidden">
				<div className="flex min-w-0 flex-col gap-4 lg:h-full lg:min-h-0">
					{groupKey ? (
						<GroupChecks
							key={`${groupBy}:${groupKey}`}
							groupBy={groupBy}
							groupKey={groupKey}
							onOpen={(entry, opener) => {
								setSelected({ entry, opener });
								setDrawerOpen(true);
							}}
							desktop={desktop}
						/>
					) : groupsQuery.error ||
					  (groupsQuery.isLoading && !groupsQuery.data) ? (
						<LoadState
							loading={groupsQuery.isLoading}
							error={groupsQuery.error}
							refetch={groupsQuery.refetch}
							label="Loading access checks"
						/>
					) : groups.length === 0 ? (
						<EmptyState
							icon={ShieldCheck}
							title="No Would-Deny Checks"
							description="Every recorded check would have been allowed."
						/>
					) : (
						<GroupList
							groups={groups}
							heading={current.label}
							name={(group) =>
								groupName(group.key ?? null, group.sample)
							}
							onOpen={(group) =>
								show({
									group_by: groupBy,
									key: group.key ?? NONE,
								})
							}
							desktop={desktop}
						/>
					)}
				</div>
			</PageScrollArea>
			<AuditEventDrawer
				entry={selected?.entry}
				open={drawerOpen}
				onOpenChange={setDrawerOpen}
				returnFocus={selected?.opener}
			/>
		</PageWorkspace>
	);
}

export default AccessChecksPage;
