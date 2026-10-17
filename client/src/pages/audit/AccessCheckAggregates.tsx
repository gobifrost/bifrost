import { ChevronRight } from "lucide-react";

import { RunUserLabel } from "@/components/audit/RunUserLabel";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import type { AuditLogEntry, AuditLogGroup } from "@/hooks/useAuditLog";
import { checkResourceTitle } from "@/lib/access-trace";
import { formatRelativeTime } from "@/lib/utils";

function Organization({ entry }: { entry: AuditLogEntry }) {
	return (
		<span className="[overflow-wrap:anywhere]">
			{entry.actor.organization_name ??
				(entry.actor.organization_id ? "—" : "Global")}
		</span>
	);
}

function CheckSummary({
	entry,
	showWorkflow,
}: {
	entry: AuditLogEntry;
	showWorkflow: boolean;
}) {
	return (
		<div className="min-w-0 space-y-1 [overflow-wrap:anywhere]">
			<p>
				{checkResourceTitle(entry.resource_type ?? "", entry.details)}
			</p>
			{showWorkflow && entry.workflow_name && (
				<p className="text-xs text-muted-foreground">
					{entry.workflow_name}
				</p>
			)}
			{entry.operation_id && (
				<p className="font-mono text-xs text-muted-foreground">
					{entry.operation_id}
				</p>
			)}
		</div>
	);
}

function LastSeen({ group }: { group: AuditLogGroup }) {
	return (
		<div className="space-y-1">
			<time
				dateTime={group.last_seen}
				title={new Date(group.last_seen).toLocaleString()}
			>
				{formatRelativeTime(group.last_seen)}
			</time>
			{group.first_seen && group.first_seen !== group.last_seen && (
				<p className="text-xs text-muted-foreground">
					First seen {new Date(group.first_seen).toLocaleString()}
				</p>
			)}
		</div>
	);
}

/** A time bucket is a summary; opening it reads the original, paginated checks. */
export function AccessCheckAggregates({
	groups,
	desktop,
	onOpen,
	showWorkflow = true,
}: {
	groups: AuditLogGroup[];
	desktop: boolean;
	onOpen: (group: AuditLogGroup, opener: HTMLElement | null) => void;
	showWorkflow?: boolean;
}) {
	const control = (group: AuditLogGroup) => {
		return (
			<button
				type="button"
				aria-haspopup="dialog"
				aria-label={`Open ${group.count} Recorded ${group.count === 1 ? "Check" : "Checks"}`}
				onClick={(event) => {
					event.stopPropagation();
					onOpen(group, event.currentTarget);
				}}
				className="inline-flex min-h-11 min-w-11 items-center justify-end gap-2 rounded-[var(--bf-radius-control)] px-2 font-mono tabular-nums hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
			>
				<span>{group.count}</span>
				<ChevronRight aria-hidden="true" className="size-4" />
			</button>
		);
	};

	if (!desktop)
		return (
			<ol aria-label="Check Summaries" className="space-y-3">
				{groups.map((group) => (
					<li
						key={group.key!}
						className="rounded-[var(--bf-radius-surface)] border bg-card text-sm"
					>
						<dl className="grid grid-cols-2 gap-3 p-4">
							<div>
								<dt className="text-xs text-muted-foreground">
									Organization
								</dt>
								<dd className="mt-1">
									<Organization entry={group.sample} />
								</dd>
							</div>
							<div className="text-right">
								<dt className="text-xs text-muted-foreground">
									# Checks
								</dt>
								<dd>{control(group)}</dd>
							</div>
							<div className="col-span-2">
								<dt className="text-xs text-muted-foreground">
									Run User
								</dt>
								<dd className="mt-1">
									<RunUserLabel entry={group.sample} />
								</dd>
							</div>
							<div>
								<dt className="text-xs text-muted-foreground">
									Resource Type
								</dt>
								<dd className="mt-1">
									<CheckSummary
										entry={group.sample}
										showWorkflow={showWorkflow}
									/>
								</dd>
							</div>
							<div>
								<dt className="text-xs text-muted-foreground">
									Last Seen
								</dt>
								<dd className="mt-1">
									<LastSeen group={group} />
								</dd>
							</div>
						</dl>
					</li>
				))}
			</ol>
		);

	return (
		<DataTable>
			<DataTableHeader>
				<DataTableRow>
					<DataTableHead>Organization</DataTableHead>
					<DataTableHead>Last Seen</DataTableHead>
					<DataTableHead>Run User</DataTableHead>
					<DataTableHead>Resource Type</DataTableHead>
					<DataTableHead className="text-right">
						# Checks
					</DataTableHead>
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{groups.map((group) => (
					<DataTableRow
						key={group.key!}
						clickable
						onClick={(event) =>
							onOpen(
								group,
								event.currentTarget.querySelector("button"),
							)
						}
					>
						<DataTableCell>
							<Organization entry={group.sample} />
						</DataTableCell>
						<DataTableCell>
							<LastSeen group={group} />
						</DataTableCell>
						<DataTableCell>
							<RunUserLabel entry={group.sample} />
						</DataTableCell>
						<DataTableCell>
							<CheckSummary
								entry={group.sample}
								showWorkflow={showWorkflow}
							/>
						</DataTableCell>
						<DataTableCell className="text-right">
							{control(group)}
						</DataTableCell>
					</DataTableRow>
				))}
			</DataTableBody>
		</DataTable>
	);
}
