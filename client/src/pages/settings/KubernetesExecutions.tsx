import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SettingsToggleRow } from "@/components/shared/SettingsToggleRow";
import { SettingsLoadError } from "@/components/shared/SettingsLoadError";
import { KubernetesIcon } from "@/components/icons/KubernetesIcon";
import {
	getKubernetesExecution,
	updateKubernetesExecution,
	type KubernetesExecutionJobType,
} from "@/services/kubernetes";

function ConcurrencyInput({
	job,
	disabled,
	onCommit,
}: {
	job: KubernetesExecutionJobType;
	disabled: boolean;
	onCommit: (jobType: string, value: number | null) => void;
}) {
	const [text, setText] = useState<string>(
		job.max_concurrency == null ? "" : String(job.max_concurrency),
	);
	const [lastSeen, setLastSeen] = useState<number | null | undefined>(
		job.max_concurrency,
	);
	if (lastSeen !== job.max_concurrency) {
		setLastSeen(job.max_concurrency);
		setText(
			job.max_concurrency == null ? "" : String(job.max_concurrency),
		);
	}
	const id = `k8s-concurrency-${job.job_type}`;

	const commit = () => {
		const trimmed = text.trim();
		if (trimmed === "") {
			if (job.max_concurrency !== null) onCommit(job.job_type, null);
			return;
		}
		const value = Number(trimmed);
		if (
			!Number.isInteger(value) ||
			value < 1 ||
			value > 32 ||
			value === job.max_concurrency
		) {
			setText(
				job.max_concurrency == null
					? ""
					: String(job.max_concurrency),
			);
			return;
		}
		onCommit(job.job_type, value);
	};

	return (
		<div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-1">
			<Label htmlFor={id} className="text-sm text-muted-foreground">
				Max parallel runs
			</Label>
			<Input
				id={id}
				type="number"
				min={1}
				max={32}
				className="h-9 w-24"
				placeholder={
					job.default_max_concurrency == null
						? "Unlimited"
						: `Default: ${job.default_max_concurrency}`
				}
				value={text}
				disabled={disabled}
				onChange={(event) => setText(event.target.value)}
				onBlur={commit}
				onKeyDown={(event) => {
					if (event.key === "Enter") {
						(event.target as HTMLInputElement).blur();
					}
				}}
			/>
			{job.max_concurrency != null && (
				<Button
					type="button"
					variant="ghost"
					size="sm"
					disabled={disabled}
					onClick={() => onCommit(job.job_type, null)}
				>
					Reset to default
				</Button>
			)}
		</div>
	);
}

export function KubernetesExecutions() {
	const [jobTypes, setJobTypes] = useState<KubernetesExecutionJobType[]>([]);
	const [loading, setLoading] = useState(true);
	const [loadError, setLoadError] = useState(false);
	const [loadAttempt, setLoadAttempt] = useState(0);
	const [saving, setSaving] = useState<string | null>(null);

	useEffect(() => {
		let active = true;
		getKubernetesExecution()
			.then((settings) => {
				if (active) setJobTypes(settings.job_types);
			})
			.catch(() => {
				if (active) setLoadError(true);
			})
			.finally(() => {
				if (active) setLoading(false);
			});
		return () => {
			active = false;
		};
	}, [loadAttempt]);

	const save = async (
		jobType: string,
		update: { enabled: boolean; maxConcurrency?: number | null },
		successMessage: string,
	) => {
		if (loading || saving || loadError) return;
		setSaving(jobType);
		try {
			const settings = await updateKubernetesExecution(
				jobType,
				update,
			);
			setJobTypes(settings.job_types);
			toast.success(successMessage);
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to update execution settings",
			);
		} finally {
			setSaving(null);
		}
	};

	const handleToggle = (job: KubernetesExecutionJobType, next: boolean) =>
		save(
			job.job_type,
			{ enabled: next },
			next
				? `${job.job_type} will run in Kubernetes pods`
				: `${job.job_type} will run in the scheduler`,
		);

	const handleConcurrency = (job: KubernetesExecutionJobType, value: number | null) =>
		save(
			job.job_type,
			{ enabled: job.enabled, maxConcurrency: value },
			value == null
				? `${job.job_type} concurrency reset to default`
				: `${job.job_type} limited to ${value} parallel run${value === 1 ? "" : "s"}`,
		);

	return (
		<Card>
			<CardHeader>
				<div className="flex items-center gap-2">
					<KubernetesIcon className="h-5 w-5" />
					<CardTitle>Executions</CardTitle>
				</div>
				<CardDescription>
					Choose which heavy jobs run in Kubernetes pods instead of
					the scheduler, and how many may overlap. Changes apply to
					newly enqueued jobs; running jobs keep their placement.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				{loadError && (
					<SettingsLoadError
						name="execution settings"
						onRetry={() => {
							setLoading(true);
							setLoadError(false);
							setLoadAttempt((value) => value + 1);
						}}
					/>
				)}
				{loading && jobTypes.length === 0 && !loadError
					? null
					: jobTypes.map((job) => (
							<div key={job.job_type} className="space-y-2">
								<SettingsToggleRow
									id={`k8s-execution-${job.job_type}`}
									label={job.title}
									description={
										job.allowed_by_deployment
											? job.description
											: `${job.description} Currently blocked by the deployment allowlist; enabling here has no effect until an operator allows it.`
									}
									checked={job.enabled}
									disabled={
										loading ||
										saving !== null ||
										loadError
									}
									busy={
										loading
											? "loading"
											: saving === job.job_type
												? "saving"
												: undefined
									}
									onChange={(value) =>
										handleToggle(job, value)
									}
								/>
								<ConcurrencyInput
									job={job}
									disabled={
										loading ||
										saving !== null ||
										loadError
									}
									onCommit={(jobType, value) =>
										handleConcurrency(
											jobTypes.find(
												(candidate) =>
													candidate.job_type ===
													jobType,
											) ?? job,
											value,
										)
									}
								/>
							</div>
						))}
			</CardContent>
		</Card>
	);
}
