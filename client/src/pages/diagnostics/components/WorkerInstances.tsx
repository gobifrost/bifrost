import { useState } from "react";
import { formatDistanceStrict } from "date-fns";
import { RefreshCw } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { getErrorMessage } from "@/lib/api-error";
import {
	useWorkerInstances,
	type WorkerInstanceCursor,
	type WorkerInstanceQuery,
} from "@/services/worker-instances";

const workerTypes = {
	external: "External",
	job: "Kubernetes Job",
	static: "Static",
};

type FormFilter = NonNullable<WorkerInstanceQuery["form"]> | "all";

const stateLabels: Record<string, string> = {
	reserved: "Reserved",
	starting: "Starting",
	ready: "Ready",
	draining: "Draining",
	lost: "Lost",
	retired: "Retired",
};

const reasonLabels: Record<string, string> = {
	heartbeat_lost: "Heartbeat lost",
	idle: "Idle",
	start_timeout: "Startup timed out",
	job_missing: "Kubernetes job missing",
	provider_resource_stopped: "Provider resource stopped",
};

export function WorkerInstances() {
	const [form, setForm] = useState<FormFilter>("all");
	const [cursors, setCursors] = useState<WorkerInstanceCursor[]>([]);
	const { data, error, isPending, isFetching, refetch } = useWorkerInstances({
		...(form !== "all" ? { form } : {}),
		...cursors.at(-1),
	});
	const next = data?.next_cursor;
	const now = new Date();

	return (
		<section
			aria-labelledby="worker-instances"
			className="min-w-0 space-y-3"
		>
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div>
					<h2 id="worker-instances" className="font-semibold">
						Worker Instances
					</h2>
					<p className="text-sm text-muted-foreground">
						See which workers are running and why earlier instances
						stopped.
					</p>
				</div>
				<div className="flex flex-wrap items-center gap-2">
					<Select
						value={form}
						onValueChange={(value: FormFilter) => {
							setForm(value);
							setCursors([]);
						}}
					>
						<SelectTrigger
							aria-label="Worker Type"
							className="min-h-11 w-40"
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="all">All Types</SelectItem>
							{Object.entries(workerTypes).map(
								([value, label]) => (
									<SelectItem key={value} value={value}>
										{label}
									</SelectItem>
								),
							)}
						</SelectContent>
					</Select>
					<Button
						type="button"
						variant="outline"
						size="icon"
						className="size-11"
						aria-label="Refresh Worker Instances"
						disabled={isFetching}
						onClick={() => void refetch()}
					>
						<RefreshCw
							className={
								isFetching
									? "animate-spin motion-reduce:animate-none"
									: ""
							}
						/>
					</Button>
				</div>
			</div>
			{error && (
				<Alert variant="destructive">
					<AlertDescription>
						{getErrorMessage(
							error,
							"Could not load worker instances.",
						)}
					</AlertDescription>
				</Alert>
			)}
			{isPending && (
				<p role="status" className="py-4 text-sm text-muted-foreground">
					Loading worker instances…
				</p>
			)}
			{data &&
				(data.items.length ? (
					<div className="max-h-[min(28rem,60vh)] overflow-auto rounded-lg border">
						<table
							className="w-full text-left text-sm"
							aria-label="Worker Instances"
						>
							<thead className="sticky top-0 bg-muted text-muted-foreground">
								<tr>
									{["Worker", "State", "Build", "Age"].map(
										(heading) => (
											<th
												key={heading}
												scope="col"
												className="px-3 py-2 font-medium"
											>
												{heading}
											</th>
										),
									)}
								</tr>
							</thead>
							<tbody>
								{data.items.map((worker) => (
									<tr
										key={worker.id}
										className="border-t align-top"
									>
										<td className="px-3 py-2">
											<code className="break-all text-xs">
												{worker.id}
											</code>
											<div className="break-all text-xs text-muted-foreground">
												{workerTypes[worker.form]}
												{worker.capacity_scope &&
													` · ${worker.capacity_scope}`}
											</div>
											{worker.capacity_resource_uid && (
												<details className="mt-1 text-xs">
													<summary className="cursor-pointer">
														Provider Resource
													</summary>
													<code className="break-all">
														{
															worker.capacity_resource_uid
														}
													</code>
												</details>
											)}
										</td>
										<td className="px-3 py-2">
											<Badge
												variant={
													worker.state === "lost"
														? "destructive"
														: worker.state ===
															  "ready"
															? "success"
															: "secondary"
												}
											>
												{stateLabels[worker.state] ??
													worker.state}
											</Badge>
											{worker.end_reason && (
												<div className="mt-1 text-xs text-muted-foreground">
													{reasonLabels[
														worker.end_reason
													] ??
														worker.end_reason.replaceAll(
															"_",
															" ",
														)}
												</div>
											)}
										</td>
										<td className="break-all px-3 py-2 font-mono text-xs">
											{worker.version ?? "—"}
										</td>
										<td className="px-3 py-2">
											<time
												dateTime={worker.created_at}
												title={new Date(
													worker.created_at,
												).toLocaleString()}
											>
												{formatDistanceStrict(
													new Date(worker.created_at),
													worker.ended_at
														? new Date(
																worker.ended_at,
															)
														: now,
												)}
											</time>
											{worker.ended_at && (
												<div className="text-xs text-muted-foreground">
													Ended{" "}
													<time
														dateTime={
															worker.ended_at
														}
														title={new Date(
															worker.ended_at,
														).toLocaleString()}
													>
														{formatDistanceStrict(
															new Date(
																worker.ended_at,
															),
															now,
															{ addSuffix: true },
														)}
													</time>
												</div>
											)}
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				) : (
					<p className="py-4 text-sm text-muted-foreground">
						No worker instances found.
					</p>
				))}
			{(cursors.length > 0 || next) && (
				<div className="flex items-center justify-end gap-2">
					<Button
						variant="outline"
						size="sm"
						disabled={cursors.length === 0 || isFetching}
						onClick={() =>
							setCursors((previous) => previous.slice(0, -1))
						}
					>
						Previous
					</Button>
					<span className="text-sm text-muted-foreground">
						Page {cursors.length + 1}
					</span>
					<Button
						variant="outline"
						size="sm"
						disabled={!next || isFetching}
						onClick={() => {
							if (next)
								setCursors((previous) => [...previous, next]);
						}}
					>
						Next
					</Button>
				</div>
			)}
		</section>
	);
}
