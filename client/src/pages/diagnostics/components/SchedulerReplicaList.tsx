import { formatDistanceToNow } from "date-fns";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import type { SchedulerDiagnosticsResponse } from "@/services/schedulerDiagnostics";

type SchedulerReplica = SchedulerDiagnosticsResponse["replicas"][number];

function formatBytes(
	value: number | null | undefined,
	emptyLabel = "Not limited",
) {
	if (value == null) return emptyLabel;
	const units = ["B", "KiB", "MiB", "GiB", "TiB"];
	let amount = value;
	let unit = 0;
	while (amount >= 1024 && unit < units.length - 1) {
		amount /= 1024;
		unit += 1;
	}
	return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function relativeTime(value: string | null | undefined) {
	if (!value) return "Never";
	return formatDistanceToNow(new Date(value), { addSuffix: true });
}

function replicaRole(replica: SchedulerReplica) {
	// Every API control replica reconciles work; only the leader runs triggers.
	if (replica.is_leader) return "Trigger Leader";
	return replica.runs_platform_jobs ? "Job Runner" : "Control";
}

function replicaWorkload(replica: SchedulerReplica) {
	return replica.active_platform_jobs === 0
		? "Idle"
		: `${replica.active_platform_jobs} running`;
}

function replicaPlatformJobs(replica: SchedulerReplica) {
	// API control replicas run none; executors admit them by memory.
	return replica.runs_platform_jobs ? `${replica.active_platform_jobs}` : "—";
}

function replicaMemory(replica: SchedulerReplica) {
	const current = formatBytes(replica.memory_current_bytes, "Unavailable");
	const limit = formatBytes(replica.memory_limit_bytes);
	return `${current} / ${limit}`;
}

function ReplicaStatusBadge({ replica }: { replica: SchedulerReplica }) {
	return (
		<Badge variant={replica.online ? "secondary" : "destructive"}>
			{replica.online ? "Online" : "Stale"}
		</Badge>
	);
}

function ReplicaRoleBadge({ replica }: { replica: SchedulerReplica }) {
	return <Badge variant="outline">{replicaRole(replica)}</Badge>;
}

function ReplicaWorkloadBadge({ replica }: { replica: SchedulerReplica }) {
	return (
		<Badge
			variant={replica.active_platform_jobs > 0 ? "secondary" : "outline"}
		>
			{replicaWorkload(replica)}
		</Badge>
	);
}

function ReplicaCard({ replica }: { replica: SchedulerReplica }) {
	return (
		<Card className="@4xl:hidden">
			<CardContent className="space-y-4 p-[var(--bf-surface-pad)]">
				<div className="flex flex-col gap-3">
					<div className="min-w-0">
						<p className="font-medium leading-6 [overflow-wrap:anywhere]">
							{replica.hostname}
						</p>
						<p
							className="mt-1 [overflow-wrap:anywhere] text-xs text-muted-foreground"
							title={replica.id}
						>
							{replica.id}
						</p>
					</div>
					<div className="flex flex-wrap gap-2">
						<ReplicaRoleBadge replica={replica} />
						<ReplicaStatusBadge replica={replica} />
					</div>
				</div>

				<dl className="grid gap-3 text-sm sm:grid-cols-2">
					<div className="space-y-1">
						<dt className="text-xs font-medium text-muted-foreground">
							Heartbeat
						</dt>
						<dd>{relativeTime(replica.last_heartbeat_at)}</dd>
					</div>
					<div className="space-y-1">
						<dt className="text-xs font-medium text-muted-foreground">
							Memory
						</dt>
						<dd>{replicaMemory(replica)}</dd>
					</div>
					<div className="space-y-1">
						<dt className="text-xs font-medium text-muted-foreground">
							Platform Jobs
						</dt>
						<dd>{replicaPlatformJobs(replica)}</dd>
					</div>
					<div className="space-y-1">
						<dt className="text-xs font-medium text-muted-foreground">
							Workload
						</dt>
						<dd className="flex flex-wrap items-center gap-2">
							<ReplicaWorkloadBadge replica={replica} />
						</dd>
					</div>
				</dl>
			</CardContent>
		</Card>
	);
}

export function SchedulerReplicaList({
	replicas,
}: {
	replicas: SchedulerDiagnosticsResponse["replicas"];
}) {
	if (replicas.length === 0) return null;

	return (
		<div className="@container min-w-0">
			<div className="grid gap-3 @4xl:hidden">
				{replicas.map((replica) => (
					<ReplicaCard key={replica.id} replica={replica} />
				))}
			</div>

			<DataTable className="hidden @4xl:flex">
				<DataTableHeader>
					<DataTableRow>
						<DataTableHead>Replica</DataTableHead>
						<DataTableHead>Role</DataTableHead>
						<DataTableHead>Status</DataTableHead>
						<DataTableHead>Memory</DataTableHead>
						<DataTableHead>Platform Jobs</DataTableHead>
						<DataTableHead>Workload</DataTableHead>
					</DataTableRow>
				</DataTableHeader>
				<DataTableBody>
					{replicas.map((replica) => (
						<DataTableRow key={replica.id}>
							<DataTableCell>
								<div className="font-medium">
									{replica.hostname}
								</div>
								<div
									className="mt-1 [overflow-wrap:anywhere] text-xs text-muted-foreground"
									title={replica.id}
								>
									{replica.id}
								</div>
							</DataTableCell>
							<DataTableCell>
								<ReplicaRoleBadge replica={replica} />
							</DataTableCell>
							<DataTableCell>
								<div className="flex flex-col gap-1">
									<ReplicaStatusBadge replica={replica} />
									<span className="text-xs text-muted-foreground">
										{relativeTime(
											replica.last_heartbeat_at,
										)}
									</span>
								</div>
							</DataTableCell>
							<DataTableCell>
								{replicaMemory(replica)}
							</DataTableCell>
							<DataTableCell>
								{replicaPlatformJobs(replica)}
							</DataTableCell>
							<DataTableCell>
								<ReplicaWorkloadBadge replica={replica} />
							</DataTableCell>
						</DataTableRow>
					))}
				</DataTableBody>
			</DataTable>
		</div>
	);
}
