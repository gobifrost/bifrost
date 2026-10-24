import { useEffect, useState, type ReactNode } from "react";
import { AlertTriangle, Boxes, Loader2 } from "lucide-react";
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
import { formatDateShort } from "@/lib/utils";
import {
	KubernetesRequestError,
	getKubernetesExecution,
	updateKubernetesExecution,
	updateKubernetesPolicy,
	type KubernetesExecutionJobType,
	type KubernetesExecutionSettings,
	type KubernetesPolicyUpdate,
	type KubernetesPolicyValues,
} from "@/services/kubernetes";

type ScalingField =
	| "warm_workers"
	| "max_workers"
	| "worker_memory_mib"
	| "largest_worker_mib"
	| "idle_seconds";

const SCALING_FIELDS: { name: ScalingField; label: string; help: string }[] = [
	{
		name: "warm_workers",
		label: "Warm Workers",
		help: "Workers kept running while there is no work, so new runs start right away.",
	},
	{
		name: "max_workers",
		label: "Max Workers",
		help: "The most workers that run at once.",
	},
	{
		name: "worker_memory_mib",
		label: "Worker Memory (MiB)",
		help: "Memory limit of a standard worker. Workers started after a change use it.",
	},
	{
		name: "largest_worker_mib",
		label: "Largest Worker (MiB)",
		help: "The most memory a worker sized for a large run can get. Runs that need more fail instead of waiting.",
	},
	{
		name: "idle_seconds",
		label: "Idle Time (Seconds)",
		help: "How long a worker beyond the warm ones waits for work before it stops.",
	},
];

type ScalingDraft = Record<ScalingField, string>;

/** A policy number the admin edits before saving it. */
type DraftField = ScalingField | "max_isolated_pods";

function scalingDraft(policy: KubernetesPolicyValues): ScalingDraft {
	return {
		warm_workers: String(policy.warm_workers),
		max_workers: String(policy.max_workers),
		worker_memory_mib: String(policy.worker_memory_mib),
		largest_worker_mib: String(policy.largest_worker_mib),
		idle_seconds: String(policy.idle_seconds),
	};
}

function parseWholeNumber(text: string): number | null {
	const trimmed = text.trim();
	return /^\d+$/.test(trimmed) ? Number(trimmed) : null;
}

type Provenance = KubernetesExecutionSettings["imported_from_env"];

function ProvenanceNote({
	provenance,
	field,
}: {
	provenance: Provenance;
	field: string;
}) {
	const entry = provenance[field];
	if (!entry) return null;
	return (
		<p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
			Imported from <code className="font-mono">{entry.variable}</code> on{" "}
			{formatDateShort(entry.imported_at)}
		</p>
	);
}

/** A variable name with a break opportunity after each underscore, so a
 * narrow screen wraps it between words rather than mid-word. */
function VariableName({ name }: { name: string }) {
	const parts = name.split("_");
	return (
		<>
			{parts.map((part, index) => (
				<span key={index}>
					{part}
					{index < parts.length - 1 && (
						<>
							_<wbr />
						</>
					)}
				</span>
			))}
		</>
	);
}

function UnreadVariablesNotice({ variables }: { variables: string[] }) {
	if (variables.length === 0) return null;
	return (
		<div
			role="status"
			className="flex min-w-0 gap-3 rounded-[var(--bf-radius-surface)] border bg-[var(--bf-warning-soft)] p-4 text-sm"
		>
			<AlertTriangle
				aria-hidden="true"
				className="mt-0.5 size-4 shrink-0 text-[var(--bf-warning)]"
			/>
			<div className="min-w-0 space-y-2">
				<p>No longer used; remove from your environment:</p>
				<ul className="flex flex-wrap gap-2">
					{variables.map((variable) => (
						<li key={variable} className="min-w-0">
							<code className="block rounded-[var(--bf-radius-control)] bg-background/70 px-2 py-0.5 font-mono text-xs [overflow-wrap:break-word]">
								<VariableName name={variable} />
							</code>
						</li>
					))}
				</ul>
			</div>
		</div>
	);
}

function NumberField({
	id,
	label,
	help,
	value,
	error,
	disabled,
	provenance,
	field,
	onChange,
	onEnter,
	action,
}: {
	id: string;
	label: string;
	help: string;
	value: string;
	error?: string;
	disabled: boolean;
	provenance: Provenance;
	field: string;
	onChange: (value: string) => void;
	onEnter?: () => void;
	/** A control shown beside the input, such as its own save button. */
	action?: ReactNode;
}) {
	return (
		<div className="min-w-0 space-y-2">
			<Label htmlFor={id}>{label}</Label>
			<div className="flex flex-wrap items-center gap-3">
				<Input
					id={id}
					type="number"
					inputMode="numeric"
					min={0}
					step={1}
					className="min-h-11 w-full sm:w-40"
					value={value}
					disabled={disabled}
					aria-invalid={error ? true : undefined}
					aria-describedby={`${id}-help${error ? ` ${id}-error` : ""}`}
					onChange={(event) => onChange(event.target.value)}
					onKeyDown={(event) => {
						if (event.key === "Enter" && onEnter) {
							event.preventDefault();
							onEnter();
						}
					}}
				/>
				{action}
			</div>
			<p id={`${id}-help`} className="text-sm text-muted-foreground">
				{help}
			</p>
			{error && (
				<p
					id={`${id}-error`}
					role="alert"
					className="text-sm text-destructive [overflow-wrap:anywhere]"
				>
					{error}
				</p>
			)}
			<ProvenanceNote provenance={provenance} field={field} />
		</div>
	);
}

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
		setText(job.max_concurrency == null ? "" : String(job.max_concurrency));
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
				job.max_concurrency == null ? "" : String(job.max_concurrency),
			);
			return;
		}
		onCommit(job.job_type, value);
	};

	return (
		<div className="flex flex-wrap items-center gap-x-3 gap-y-2 pt-1">
			<Label htmlFor={id} className="text-sm text-muted-foreground">
				Max Parallel Runs
			</Label>
			<Input
				id={id}
				type="number"
				min={1}
				max={32}
				className="h-9 w-32"
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
					Reset to Default
				</Button>
			)}
		</div>
	);
}

export function KubernetesSettings() {
	const [settings, setSettings] =
		useState<KubernetesExecutionSettings | null>(null);
	const [draft, setDraft] = useState<ScalingDraft | null>(null);
	const [podsDraft, setPodsDraft] = useState("");
	const [loadError, setLoadError] = useState(false);
	const [loadAttempt, setLoadAttempt] = useState(0);
	const [saving, setSaving] = useState<string | null>(null);
	const [scalingErrors, setScalingErrors] = useState<
		Partial<Record<ScalingField, string>>
	>({});
	const [scalingError, setScalingError] = useState<string | null>(null);
	const [podsError, setPodsError] = useState<string | null>(null);
	// Fields the admin changed and has not saved. Every other draft follows
	// the server, so a save never sends a value another admin replaced.
	const [edited, setEdited] = useState<ReadonlySet<DraftField>>(
		() => new Set(),
	);

	useEffect(() => {
		let active = true;
		getKubernetesExecution()
			.then((loaded) => {
				if (!active) return;
				setSettings(loaded);
				setDraft(scalingDraft(loaded.policy));
				setPodsDraft(String(loaded.policy.max_isolated_pods));
			})
			.catch(() => {
				if (active) setLoadError(true);
			});
		return () => {
			active = false;
		};
	}, [loadAttempt]);

	const loading = settings === null && !loadError;
	const busy = loading || saving !== null || loadError;

	const edit = (field: DraftField) => {
		setEdited((fields) =>
			fields.has(field) ? fields : new Set([...fields, field]),
		);
	};

	/**
	 * Show settings the server returned: drafts the admin has not edited
	 * take its values; ``saved`` fields are no longer pending edits.
	 */
	const applySettings = (
		next: KubernetesExecutionSettings,
		saved: readonly DraftField[],
	) => {
		const pending = new Set(
			[...edited].filter((field) => !saved.includes(field)),
		);
		const fromServer = scalingDraft(next.policy);
		setSettings(next);
		setEdited(pending);
		setDraft((current) => {
			if (!current) return fromServer;
			const merged = { ...fromServer };
			for (const { name } of SCALING_FIELDS) {
				if (pending.has(name)) merged[name] = current[name];
			}
			return merged;
		});
		if (!pending.has("max_isolated_pods")) {
			setPodsDraft(String(next.policy.max_isolated_pods));
		}
	};

	/**
	 * Save policy changes. Returns the saved settings, or the validation
	 * error the API refused the change with; other failures toast.
	 */
	const savePolicy = async (
		scope: string,
		changes: KubernetesPolicyUpdate,
		saved: readonly DraftField[],
		successMessage: string,
	): Promise<KubernetesExecutionSettings | KubernetesRequestError | null> => {
		setSaving(scope);
		try {
			const next = await updateKubernetesPolicy(changes);
			applySettings(next, saved);
			toast.success(successMessage);
			return next;
		} catch (error) {
			if (
				error instanceof KubernetesRequestError &&
				error.status === 422
			) {
				return error;
			}
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to update Kubernetes settings",
			);
			return null;
		} finally {
			setSaving(null);
		}
	};

	const scalingChanges = (): Partial<Record<ScalingField, number>> | null => {
		if (!settings || !draft) return null;
		const changes: Partial<Record<ScalingField, number>> = {};
		const errors: Partial<Record<ScalingField, string>> = {};
		for (const { name } of SCALING_FIELDS) {
			if (draft[name] === String(settings.policy[name])) continue;
			const value = parseWholeNumber(draft[name]);
			if (value === null) errors[name] = "Enter a whole number.";
			else changes[name] = value;
		}
		setScalingErrors(errors);
		return Object.keys(errors).length > 0 ? null : changes;
	};

	const scalingDirty =
		settings !== null &&
		draft !== null &&
		SCALING_FIELDS.some(
			({ name }) => draft[name] !== String(settings.policy[name]),
		);

	const saveScaling = async () => {
		if (busy || !scalingDirty) return;
		setScalingError(null);
		const changes = scalingChanges();
		if (!changes) return;
		const result = await savePolicy(
			"scaling",
			changes,
			SCALING_FIELDS.map(({ name }) => name),
			"Scaling settings saved",
		);
		if (result instanceof KubernetesRequestError) {
			const field = SCALING_FIELDS.find(
				({ name }) => name === result.field,
			);
			if (field) setScalingErrors({ [field.name]: result.message });
			else setScalingError(result.message);
		}
	};

	const podsDirty =
		settings !== null &&
		podsDraft !== String(settings.policy.max_isolated_pods);

	const saveMaxPods = async () => {
		if (busy || !podsDirty) return;
		const value = parseWholeNumber(podsDraft);
		if (value === null) {
			setPodsError("Enter a whole number.");
			return;
		}
		setPodsError(null);
		const result = await savePolicy(
			"max_isolated_pods",
			{ max_isolated_pods: value },
			["max_isolated_pods"],
			`Up to ${value} isolated pod${value === 1 ? "" : "s"} can run at once`,
		);
		if (result instanceof KubernetesRequestError) {
			setPodsError(result.message);
		}
	};

	const toggleIsolation = async (enabled: boolean) => {
		if (busy) return;
		const result = await savePolicy(
			"isolation",
			{ isolated_jobs_enabled: enabled },
			[],
			enabled
				? "Build jobs will run in isolated pods"
				: "Build jobs will run on workers",
		);
		if (result instanceof KubernetesRequestError) {
			toast.error(result.message);
		}
	};

	const saveJobType = async (
		job: KubernetesExecutionJobType,
		update: { enabled: boolean; maxConcurrency?: number | null },
		successMessage: string,
	) => {
		if (busy) return;
		setSaving(job.job_type);
		try {
			applySettings(
				await updateKubernetesExecution(job.job_type, update),
				[],
			);
			toast.success(successMessage);
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Failed to update Kubernetes settings",
			);
		} finally {
			setSaving(null);
		}
	};

	const handleToggle = (job: KubernetesExecutionJobType, next: boolean) =>
		saveJobType(
			job,
			{ enabled: next },
			next
				? `${job.title} will run in isolated pods`
				: `${job.title} will run on workers`,
		);

	const handleConcurrency = (
		job: KubernetesExecutionJobType,
		value: number | null,
	) =>
		saveJobType(
			job,
			{ enabled: job.enabled, maxConcurrency: value },
			value == null
				? `${job.title} parallel runs reset to default`
				: `${job.title} limited to ${value} parallel run${value === 1 ? "" : "s"}`,
		);

	const provenance = settings?.imported_from_env ?? {};
	const retry = () => {
		setLoadError(false);
		setLoadAttempt((value) => value + 1);
	};

	return (
		<div className="min-w-0 w-full space-y-6">
			<div>
				<h2 className="font-display text-2xl font-semibold tracking-tight">
					Scaling & Isolation
				</h2>
				<p className="mt-1 text-sm text-muted-foreground">
					How Bifrost runs work in this Kubernetes cluster. Changes
					apply without a restart.
				</p>
			</div>

			{loadError && (
				<SettingsLoadError name="Kubernetes settings" onRetry={retry} />
			)}

			{settings && (
				<UnreadVariablesNotice variables={settings.unread_variables} />
			)}

			<Card>
				<CardHeader>
					<div className="flex items-center gap-2">
						<KubernetesIcon className="h-5 w-5" />
						<CardTitle>Scaling</CardTitle>
					</div>
					<CardDescription>
						Workers start as work arrives and stop when idle, within
						these limits.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-6">
					{loading && (
						<p
							role="status"
							className="flex items-center gap-2 text-sm text-muted-foreground"
						>
							<Loader2
								aria-hidden="true"
								className="size-4 animate-spin motion-reduce:animate-none"
							/>
							Loading…
						</p>
					)}
					{settings && draft && (
						<>
							<div className="grid gap-6 sm:grid-cols-2">
								{SCALING_FIELDS.map(({ name, label, help }) => (
									<NumberField
										key={name}
										id={`k8s-${name}`}
										label={label}
										help={help}
										value={draft[name]}
										error={scalingErrors[name]}
										disabled={busy}
										provenance={provenance}
										field={name}
										onChange={(value) => {
											setDraft({
												...draft,
												[name]: value,
											});
											edit(name);
											setScalingErrors({});
											setScalingError(null);
										}}
										onEnter={() => void saveScaling()}
									/>
								))}
							</div>
							<div className="flex flex-col gap-3 border-t pt-5 sm:flex-row sm:items-center sm:justify-end">
								{scalingError && (
									<p
										role="alert"
										className="text-sm text-destructive [overflow-wrap:anywhere] sm:mr-auto"
									>
										{scalingError}
									</p>
								)}
								<Button
									type="button"
									className="min-h-11"
									disabled={busy || !scalingDirty}
									onClick={() => void saveScaling()}
								>
									{saving === "scaling" && (
										<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
									)}
									Save Scaling
								</Button>
							</div>
						</>
					)}
				</CardContent>
			</Card>

			<Card>
				<CardHeader>
					<div className="flex items-center gap-2">
						<Boxes aria-hidden="true" className="h-5 w-5" />
						<CardTitle>Isolated Jobs</CardTitle>
					</div>
					<CardDescription>
						Run build jobs in their own pod. Without isolation, they
						run on workers, and the pool scales up to make room.
					</CardDescription>
				</CardHeader>
				{settings && (
					<CardContent className="space-y-6">
						<div className="space-y-2">
							<SettingsToggleRow
								id="k8s-isolated-jobs"
								label="Isolate Build Jobs"
								description="When turned off, queued jobs move to a worker and pods already running finish."
								checked={settings.policy.isolated_jobs_enabled}
								disabled={busy}
								busy={
									saving === "isolation"
										? "saving"
										: undefined
								}
								onChange={(value) =>
									void toggleIsolation(value)
								}
							/>
							<ProvenanceNote
								provenance={provenance}
								field="isolated_jobs_enabled"
							/>
						</div>

						<NumberField
							id="k8s-max_isolated_pods"
							label="Max Isolated Pods"
							help="The most isolated pods that run at once."
							value={podsDraft}
							error={podsError ?? undefined}
							disabled={busy}
							provenance={provenance}
							field="max_isolated_pods"
							onChange={(value) => {
								setPodsDraft(value);
								edit("max_isolated_pods");
								setPodsError(null);
							}}
							onEnter={() => void saveMaxPods()}
							action={
								<Button
									type="button"
									variant="outline"
									className="min-h-11"
									disabled={busy || !podsDirty}
									onClick={() => void saveMaxPods()}
								>
									{saving === "max_isolated_pods" && (
										<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
									)}
									Save
								</Button>
							}
						/>

						<div className="space-y-5 border-t pt-5">
							<div className="space-y-1">
								<h3 className="text-sm font-medium">
									Job Types
								</h3>
								<p className="text-sm text-muted-foreground">
									Which jobs get their own pod while isolation
									is on, and how many of each may run at once.
								</p>
								<ProvenanceNote
									provenance={provenance}
									field="enabled_job_types"
								/>
							</div>
							{settings.job_types.map((job) => (
								<div key={job.job_type} className="space-y-2">
									<SettingsToggleRow
										id={`k8s-execution-${job.job_type}`}
										label={job.title}
										description={job.description}
										checked={job.enabled}
										disabled={busy}
										busy={
											saving === job.job_type
												? "saving"
												: undefined
										}
										onChange={(value) =>
											void handleToggle(job, value)
										}
									/>
									<ConcurrencyInput
										job={job}
										disabled={busy}
										onCommit={(jobType, value) =>
											void handleConcurrency(
												settings.job_types.find(
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
						</div>
					</CardContent>
				)}
			</Card>
		</div>
	);
}
