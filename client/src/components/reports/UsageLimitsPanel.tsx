import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Gauge, Info, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuthorizationBoundary } from "@/contexts/AuthorizationBoundaryContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useUsersFiltered } from "@/hooks/useUsers";
import { listBuilderSolutions } from "@/services/builder";
import {
	deleteUsageLimit,
	getEffectiveUsageLimits,
	listUsageLimits,
	saveUsageLimit,
	type UsageLimitCeilings,
	type UsageLimitEffectiveResponse,
	type UsageLimitPolicy,
	type UsageLimitScope,
} from "@/services/usageLimits";

type EditableDimension =
	| "model_requests"
	| "total_tokens"
	| "runner_duration_ms"
	| "sandbox_compute_ms"
	| "input_tokens"
	| "output_tokens"
	| "cache_read_tokens"
	| "cache_write_tokens";

const COMMON_DIMENSIONS: EditableDimension[] = [
	"model_requests",
	"total_tokens",
	"runner_duration_ms",
	"sandbox_compute_ms",
];

const DETAIL_DIMENSIONS: EditableDimension[] = [
	"input_tokens",
	"output_tokens",
	"cache_read_tokens",
	"cache_write_tokens",
];

const DIMENSION_LABELS: Record<EditableDimension, string> = {
	model_requests: "Model requests",
	total_tokens: "Total tokens",
	runner_duration_ms: "Runner time",
	sandbox_compute_ms: "Sandbox compute",
	input_tokens: "Input tokens",
	output_tokens: "Output tokens",
	cache_read_tokens: "Cache read tokens",
	cache_write_tokens: "Cache write tokens",
};

interface TargetOption {
	scope: UsageLimitScope;
	targetId: string;
	label: string;
	description: string;
}

interface DraftState {
	targetKey: string;
	aggregatePeriod: "daily" | "monthly";
	perRun: Record<EditableDimension, string>;
	aggregate: Record<EditableDimension, string>;
	showTokenDetails: boolean;
}

function emptyValues(): Record<EditableDimension, string> {
	return Object.fromEntries(
		[...COMMON_DIMENSIONS, ...DETAIL_DIMENSIONS].map((dimension) => [
			dimension,
			"",
		]),
	) as Record<EditableDimension, string>;
}

function targetKey(scope: UsageLimitScope, targetId: string): string {
	return `${scope}:${targetId}`;
}

function parseTargetKey(value: string): { scope: UsageLimitScope; targetId: string } {
	const [scope, ...rest] = value.split(":");
	return { scope: scope as UsageLimitScope, targetId: rest.join(":") };
}

function initialDraft(option: TargetOption | undefined): DraftState {
	return {
		targetKey: option ? targetKey(option.scope, option.targetId) : "",
		aggregatePeriod: "monthly",
		perRun: emptyValues(),
		aggregate: emptyValues(),
		showTokenDetails: false,
	};
}

function ceilingValue(
	ceilings: UsageLimitCeilings | undefined,
	dimension: EditableDimension,
): string {
	const value = ceilings?.[dimension];
	if (value == null) return "";
	if (dimension.endsWith("_duration_ms") || dimension === "sandbox_compute_ms") {
		return String(Math.round(value / 60_000));
	}
	return String(value);
}

function policyToDraft(policy: UsageLimitPolicy, option: TargetOption): DraftState {
	const perRun = emptyValues();
	const aggregate = emptyValues();
	for (const dimension of [...COMMON_DIMENSIONS, ...DETAIL_DIMENSIONS]) {
		perRun[dimension] = ceilingValue(policy.per_run, dimension);
		aggregate[dimension] = ceilingValue(policy.aggregate, dimension);
	}
	return {
		targetKey: targetKey(option.scope, option.targetId),
		aggregatePeriod: policy.aggregate_period,
		perRun,
		aggregate,
		showTokenDetails: DETAIL_DIMENSIONS.some(
			(dimension) => perRun[dimension] || aggregate[dimension],
		),
	};
}

function valuesToCeilings(
	values: Record<EditableDimension, string>,
): UsageLimitCeilings {
	const ceilings: UsageLimitCeilings = {};
	for (const [dimension, raw] of Object.entries(values) as [
		EditableDimension,
		string,
	][]) {
		if (!raw.trim()) continue;
		const parsed = Number(raw);
		if (!Number.isFinite(parsed) || parsed < 0) continue;
		ceilings[dimension] =
			dimension.endsWith("_duration_ms") || dimension === "sandbox_compute_ms"
				? Math.round(parsed * 60_000)
				: Math.round(parsed);
	}
	return ceilings;
}

function hasAnyCeiling(ceilings: UsageLimitCeilings): boolean {
	return Object.values(ceilings).some((value) => value != null);
}

function invalidFields(values: Record<EditableDimension, string>): string[] {
	return Object.entries(values)
		.filter(([, raw]) => raw.trim() && (!Number.isFinite(Number(raw)) || Number(raw) < 0))
		.map(([dimension]) => DIMENSION_LABELS[dimension as EditableDimension]);
}

function formatNumber(value: number | null | undefined): string {
	return new Intl.NumberFormat().format(value ?? 0);
}

function formatDimensionValue(dimension: string, value: number): string {
	if (dimension === "model_requests") {
		return `${formatNumber(value)} ${value === 1 ? "request" : "requests"}`;
	}
	if (dimension.endsWith("_duration_ms") || dimension === "sandbox_compute_ms") {
		if (value < 60_000) return `${formatNumber(Math.round(value / 1000))} sec`;
		const minutes = Math.round(value / 60_000);
		return `${formatNumber(minutes)} min`;
	}
	return formatNumber(value);
}

function dimensionLabel(dimension: string): string {
	return (
		DIMENSION_LABELS[dimension as EditableDimension] ??
		dimension.replaceAll("_", " ")
	);
}

function scopeLabel(scope: string): string {
	return {
		platform: "Platform",
		organization: "Organization",
		user: "User",
		solution: "Solution",
	}[scope] ?? scope;
}

function boundaryOrganizationId(boundary: string | undefined): string | null {
	return boundary?.startsWith("organization:")
		? boundary.slice("organization:".length)
		: null;
}

function policyTargetKey(policy: UsageLimitPolicy): string {
	if (policy.scope === "platform") return targetKey("platform", "platform");
	if (policy.scope === "organization" && policy.organization_id) {
		return targetKey("organization", policy.organization_id);
	}
	if (policy.scope === "user" && policy.user_id) {
		return targetKey("user", policy.user_id);
	}
	if (policy.scope === "solution" && policy.solution_id) {
		return targetKey("solution", policy.solution_id);
	}
	return targetKey(policy.scope, policy.scope_key);
}

export function UsageLimitsPanel() {
	const queryClient = useQueryClient();
	const { selectedBoundary, selectedTarget, hasSelectedCapability, isLoading } =
		useAuthorizationBoundary();
	const canRead = hasSelectedCapability("metrics.read");
	const canWrite = hasSelectedCapability("metrics.readwrite");
	const canReadUsers = hasSelectedCapability("users.read");
	const canReadSolutions =
		hasSelectedCapability("solutions.read") &&
		hasSelectedCapability("builder.read");
	const organizationId = boundaryOrganizationId(selectedBoundary);
	const exactBoundary =
		selectedBoundary === "platform" || selectedBoundary?.startsWith("organization:");
	const listEnabled = Boolean(selectedBoundary && exactBoundary && canRead);

	const { data: organizations = [] } = useOrganizations({
		enabled: selectedBoundary === "platform",
		boundary: selectedBoundary,
	});
	const { data: users = [] } = useUsersFiltered(
		organizationId ?? undefined,
		false,
		selectedBoundary,
		Boolean(organizationId && canReadUsers),
	);
	const { data: builderSolutions } = useQuery({
		queryKey: ["usage-limit-builder-solutions", selectedBoundary, organizationId],
		queryFn: ({ signal }) =>
			listBuilderSolutions({
				boundary: selectedBoundary as "platform" | `organization:${string}`,
				organizationId: organizationId ?? undefined,
				view: organizationId ? "all" : "mine",
				limit: 100,
				signal,
			}),
		enabled: Boolean(selectedBoundary && organizationId && canReadSolutions),
	});

	const policyQuery = useQuery({
		queryKey: ["usage-limits", selectedBoundary],
		queryFn: () => listUsageLimits({ boundary: selectedBoundary }),
		enabled: listEnabled,
	});
	const policies = useMemo(
		() => policyQuery.data?.policies ?? [],
		[policyQuery.data?.policies],
	);

	const targetOptions = useMemo<TargetOption[]>(() => {
		if (selectedBoundary === "platform") {
			return [
				{
					scope: "platform",
					targetId: "platform",
					label: "Platform default",
					description: "Baseline for every Builder and agent run",
				},
			];
		}
		if (!organizationId) return [];
		const orgName =
			selectedTarget?.label ??
			organizations.find((org) => org.id === organizationId)?.name ??
			"Selected organization";
		const options: TargetOption[] = [
			{
				scope: "organization",
				targetId: organizationId,
				label: orgName,
				description: "Default for users and Solutions in this organization",
			},
		];
		for (const user of users ?? []) {
			options.push({
				scope: "user",
				targetId: user.id,
				label: user.name || user.email || "Unnamed user",
				description: user.email ?? "User-specific override",
			});
		}
		for (const solution of builderSolutions?.solutions ?? []) {
			options.push({
				scope: "solution",
				targetId: solution.id,
				label: solution.name || solution.slug || "Untitled Solution",
				description: "Solution-specific override",
			});
		}
		return options;
	}, [
		builderSolutions?.solutions,
		organizationId,
		organizations,
		selectedBoundary,
		selectedTarget?.label,
		users,
	]);

	const [draft, setDraft] = useState<DraftState>(() =>
		initialDraft(targetOptions[0]),
	);
	const [deleteTargetKey, setDeleteTargetKey] = useState<string | null>(null);

	const resetDraftForTarget = (key: string) => {
		const option = targetOptions.find(
			(target) => targetKey(target.scope, target.targetId) === key,
		);
		const policy = policies.find((candidate) => policyTargetKey(candidate) === key);
		setDraft((current) =>
			policy && option
				? policyToDraft(policy, option)
				: {
						...initialDraft(option),
						targetKey: key,
						showTokenDetails: current.showTokenDetails,
					},
		);
	};

	const activeTargetKey =
		draft.targetKey &&
		targetOptions.some(
			(option) => targetKey(option.scope, option.targetId) === draft.targetKey,
		)
			? draft.targetKey
			: targetOptions[0]
				? targetKey(targetOptions[0].scope, targetOptions[0].targetId)
				: "";

	const selectedOption = targetOptions.find(
		(option) => targetKey(option.scope, option.targetId) === activeTargetKey,
	);

	const activeDraft = useMemo(() => {
		if (draft.targetKey === activeTargetKey) return draft;
		const option = targetOptions.find(
			(target) => targetKey(target.scope, target.targetId) === activeTargetKey,
		);
		const policy = policies.find(
			(candidate) => policyTargetKey(candidate) === activeTargetKey,
		);
		return policy && option
			? {
					...policyToDraft(policy, option),
					showTokenDetails: draft.showTokenDetails,
				}
			: {
					...initialDraft(option),
					targetKey: activeTargetKey,
					showTokenDetails: draft.showTokenDetails,
				};
	}, [activeTargetKey, draft, policies, targetOptions]);

	const updateActiveDraft = (updater: (current: DraftState) => DraftState) => {
		setDraft(updater(activeDraft));
	};

	const parsedActiveTarget = activeTargetKey
		? parseTargetKey(activeTargetKey)
		: null;

	const effectiveTarget = parsedActiveTarget ?? {
		scope: "platform" as const,
		targetId: "platform",
	};

	const activePerRun = valuesToCeilings(activeDraft.perRun);
	const activeAggregate = valuesToCeilings(activeDraft.aggregate);
	const validationErrors = [
		...invalidFields(activeDraft.perRun),
		...invalidFields(activeDraft.aggregate),
	];
	const canSave =
		canWrite &&
		Boolean(parsedActiveTarget) &&
		validationErrors.length === 0 &&
		(hasAnyCeiling(activePerRun) || hasAnyCeiling(activeAggregate));

	const parsedSaveTarget = parsedActiveTarget;

	const savePolicy = () => {
		if (!parsedSaveTarget) {
			throw new Error("Choose a target before saving a limit.");
		}
		return saveUsageLimit(
			parsedSaveTarget,
			{
				per_run: activePerRun,
				aggregate: activeAggregate,
				aggregate_period: activeDraft.aggregatePeriod,
			},
			{ boundary: selectedBoundary },
		);
	};

	const effectiveEnabled = Boolean(listEnabled && parsedActiveTarget);

	const effectiveQuery = useQuery({
		queryKey: ["usage-limits-effective", selectedBoundary, activeTargetKey],
		queryFn: () =>
			getEffectiveUsageLimits(effectiveTarget, {
				boundary: selectedBoundary,
			}),
		enabled: effectiveEnabled,
	});

	const saveMutation = useMutation({
		mutationFn: savePolicy,
		onSuccess: () => {
			toast.success("Usage limit saved");
			void queryClient.invalidateQueries({ queryKey: ["usage-limits"] });
			void queryClient.invalidateQueries({ queryKey: ["usage-limits-effective"] });
		},
		onError: (error) => {
			toast.error("Could not save usage limit", {
				description: error instanceof Error ? error.message : "Try again.",
			});
		},
	});

	const deleteMutation = useMutation({
		mutationFn: (key: string) =>
			deleteUsageLimit(parseTargetKey(key), { boundary: selectedBoundary }),
		onSuccess: () => {
			toast.success("Usage limit deleted");
			setDeleteTargetKey(null);
			void queryClient.invalidateQueries({ queryKey: ["usage-limits"] });
			void queryClient.invalidateQueries({ queryKey: ["usage-limits-effective"] });
		},
		onError: (error) => {
			toast.error("Could not delete usage limit", {
				description: error instanceof Error ? error.message : "Try again.",
			});
		},
	});

	if (isLoading) {
		return <UsageLimitsSkeleton />;
	}

	if (selectedTarget?.kind === "managed_organizations") {
		return (
			<Alert>
				<Info className="h-4 w-4" />
				<AlertDescription>
					Managed organizations is a navigation view, not a quota target.
					Choose Global or one exact organization from Working in to review
					and edit limits.
				</AlertDescription>
			</Alert>
		);
	}

	if (!canRead) {
		return (
			<Alert variant="destructive">
				<AlertCircle className="h-4 w-4" />
				<AlertDescription>
					You need metrics.read in this working context to view usage limits.
				</AlertDescription>
			</Alert>
		);
	}

	if (!exactBoundary) {
		return (
			<Alert>
				<Info className="h-4 w-4" />
				<AlertDescription>
					Choose Global or an exact organization to view portable limits.
				</AlertDescription>
			</Alert>
		);
	}
	return (
		<div className="space-y-5">
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Gauge className="h-5 w-5 text-primary" />
						Portable usage limits
					</CardTitle>
					<CardDescription>
						Per-run limits use the most specific configured rule:
						Solution, then User, then Organization, then Platform.
						Daily and monthly aggregate limits are cumulative across every
						configured level.
					</CardDescription>
				</CardHeader>
				<CardContent className="grid gap-3 md:grid-cols-4">
					{["Platform", "Organization", "User", "Solution"].map((label, index) => (
						<div
							key={label}
							className="rounded-2xl border bg-muted/30 p-3"
						>
							<div className="text-xs text-muted-foreground">
								{index === 0 ? "Default" : `Override ${index}`}
							</div>
							<div className="mt-1 font-medium">{label}</div>
						</div>
					))}
				</CardContent>
			</Card>

			<div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
				<div className="space-y-4">
					<EffectiveSummary
						effective={effectiveQuery.data}
						isLoading={effectiveQuery.isLoading}
					/>
					<PolicyList
						policies={policies}
						targetOptions={targetOptions}
						isLoading={policyQuery.isLoading}
						canWrite={canWrite}
						onEdit={(policy, option) => setDraft(policyToDraft(policy, option))}
						onDelete={(key) => setDeleteTargetKey(key)}
						deleteTargetKey={deleteTargetKey}
						onCancelDelete={() => setDeleteTargetKey(null)}
						onConfirmDelete={(key) => deleteMutation.mutate(key)}
						isDeleting={deleteMutation.isPending}
					/>
				</div>

				<Card>
					<CardHeader>
						<CardTitle>{canWrite ? "Set a limit" : "Limit editor"}</CardTitle>
						<CardDescription>
							{canWrite
								? "Choose a target, then set per-run or aggregate ceilings."
								: "This context is read-only because metrics.readwrite is not available."}
						</CardDescription>
					</CardHeader>
					<CardContent className="space-y-4">
						<div className="space-y-2">
							<Label htmlFor="usage-limit-target">Target</Label>
							<Select
								value={activeDraft.targetKey}
								onValueChange={resetDraftForTarget}
								disabled={!canWrite || targetOptions.length === 0}
							>
								<SelectTrigger id="usage-limit-target">
									<SelectValue placeholder="Choose target" />
								</SelectTrigger>
								<SelectContent>
									{targetOptions.map((option) => (
										<SelectItem
											key={targetKey(option.scope, option.targetId)}
											value={targetKey(option.scope, option.targetId)}
										>
											{scopeLabel(option.scope)} · {option.label}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
							{selectedOption && (
								<p className="text-xs text-muted-foreground">
									{selectedOption.description}
								</p>
							)}
						</div>

						<CeilingSection
							title="Per run"
							description="Stops a single run from continuing after the winning target reaches a configured ceiling."
							values={activeDraft.perRun}
							onChange={(dimension, value) =>
								updateActiveDraft((current) => ({
									...current,
									perRun: { ...current.perRun, [dimension]: value },
								}))
							}
							showTokenDetails={activeDraft.showTokenDetails}
							disabled={!canWrite}
						/>

						<div className="space-y-2">
							<div className="flex items-center justify-between gap-3">
								<div>
									<Label>Aggregate window</Label>
									<p className="text-xs text-muted-foreground">
										Every configured aggregate level is enforced.
									</p>
								</div>
								<Select
									value={activeDraft.aggregatePeriod}
									onValueChange={(value) =>
										updateActiveDraft((current) => ({
											...current,
											aggregatePeriod: value as "daily" | "monthly",
										}))
									}
									disabled={!canWrite}
								>
									<SelectTrigger className="w-32">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										<SelectItem value="daily">Daily</SelectItem>
										<SelectItem value="monthly">Monthly</SelectItem>
									</SelectContent>
								</Select>
							</div>
							<CeilingSection
								title="Daily or monthly"
								description="Shared allowance consumed by all runs in the target."
								values={activeDraft.aggregate}
								onChange={(dimension, value) =>
									updateActiveDraft((current) => ({
										...current,
										aggregate: { ...current.aggregate, [dimension]: value },
									}))
								}
								showTokenDetails={activeDraft.showTokenDetails}
								disabled={!canWrite}
							/>
						</div>
						<Button
							type="button"
							variant="ghost"
							size="sm"
							onClick={() =>
								updateActiveDraft((current) => ({
									...current,
									showTokenDetails: !current.showTokenDetails,
								}))
							}
						>
							{activeDraft.showTokenDetails
								? "Hide token detail"
								: "Show token detail"}
						</Button>

						{validationErrors.length > 0 && (
							<Alert variant="destructive">
								<AlertCircle className="h-4 w-4" />
								<AlertDescription>
									Use nonnegative numbers for {validationErrors.join(", ")}.
								</AlertDescription>
							</Alert>
						)}
						{!hasAnyCeiling(activePerRun) && !hasAnyCeiling(activeAggregate) && (
							<p className="text-sm text-muted-foreground">
								Leave a lower level blank to inherit. To save a policy, set at
								least one ceiling.
							</p>
						)}

						<Button
							className="w-full"
							disabled={!canSave || saveMutation.isPending}
							onClick={() => saveMutation.mutate()}
						>
							{saveMutation.isPending ? "Saving…" : "Save limit"}
						</Button>
					</CardContent>
				</Card>
			</div>
		</div>
	);
}

function UsageLimitsSkeleton() {
	return (
		<div className="space-y-4" aria-label="Loading usage limits">
			<Skeleton className="h-32" />
			<div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
				<Skeleton className="h-96" />
				<Skeleton className="h-96" />
			</div>
		</div>
	);
}

function EffectiveSummary({
	effective,
	isLoading,
}: {
	effective: UsageLimitEffectiveResponse | undefined;
	isLoading: boolean;
}) {
	if (isLoading) return <Skeleton className="h-40" />;
	return (
		<Card>
			<CardHeader>
				<CardTitle>Effective for selected target</CardTitle>
					<CardDescription>
						{effective?.effective_per_run_scope
							? `${scopeLabel(effective.effective_per_run_scope)} supplies the per-run ceiling.`
						: "No per-run ceiling is configured, so this target inherits no request-level cap yet."}
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-3">
				{effective?.effective_per_run &&
					Object.entries(effective.effective_per_run).some(
						([, value]) => value != null,
					) && (
						<div className="rounded-2xl border bg-muted/30 p-4">
							<div className="text-sm font-medium">Winning per-run ceiling</div>
							<div className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
								{Object.entries(effective.effective_per_run)
									.filter(([, value]) => value != null)
									.map(([dimension, value]) => (
										<div
											key={dimension}
											className="flex justify-between gap-2 rounded-xl bg-background px-3 py-2"
										>
											<span>{dimensionLabel(dimension)}</span>
											<span className="font-medium">
												{formatDimensionValue(dimension, Number(value))}
											</span>
										</div>
									))}
							</div>
						</div>
					)}
				{effective?.aggregate?.length ? (
					effective.aggregate.map((aggregate) => (
						<div
							key={`${aggregate.scope}-${aggregate.aggregate_period}`}
							className="rounded-2xl border p-4"
						>
							<div className="flex flex-wrap items-center justify-between gap-2">
								<div>
									<div className="font-medium">
										{scopeLabel(aggregate.scope)} · {aggregate.aggregate_period}
									</div>
									<div className="text-xs text-muted-foreground">
										Window starts {aggregate.period_start}
									</div>
								</div>
								<Badge variant="outline">Cumulative</Badge>
							</div>
							<div className="mt-3 space-y-3">
								{aggregate.dimensions?.map((dimension) => (
									<div key={dimension.dimension}>
										<div className="mb-1 flex justify-between text-sm">
											<span>{dimensionLabel(dimension.dimension)}</span>
											<span className="text-muted-foreground">
												{formatDimensionValue(
													dimension.dimension,
													dimension.current,
												)}{" "}
												/{" "}
												{formatDimensionValue(
													dimension.dimension,
													dimension.limit,
												)}
											</span>
										</div>
										<Progress
											value={Math.min(100, dimension.percentage)}
											aria-label={`${dimension.dimension} usage`}
										/>
										<div className="mt-1 text-xs text-muted-foreground">
											{formatDimensionValue(
												dimension.dimension,
												dimension.remaining,
											)}{" "}
											remaining · {Math.round(dimension.percentage)}%
										</div>
									</div>
								))}
							</div>
						</div>
					))
				) : (
					<div className="rounded-2xl border border-dashed p-5 text-sm text-muted-foreground">
						No aggregate ceilings are configured for this target hierarchy.
					</div>
				)}
			</CardContent>
		</Card>
	);
}

function PolicyList({
	policies,
	targetOptions,
	isLoading,
	canWrite,
	onEdit,
	onDelete,
	deleteTargetKey,
	onCancelDelete,
	onConfirmDelete,
	isDeleting,
}: {
	policies: UsageLimitPolicy[];
	targetOptions: TargetOption[];
	isLoading: boolean;
	canWrite: boolean;
	onEdit: (policy: UsageLimitPolicy, option: TargetOption) => void;
	onDelete: (key: string) => void;
	deleteTargetKey: string | null;
	onCancelDelete: () => void;
	onConfirmDelete: (key: string) => void;
	isDeleting: boolean;
}) {
	if (isLoading) return <Skeleton className="h-80" />;
	return (
		<Card>
			<CardHeader>
				<CardTitle>Configured limits</CardTitle>
				<CardDescription>
					Blank lower levels inherit. Aggregate ceilings stack with parents.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-3">
				{policies.length === 0 && (
					<div className="rounded-2xl border border-dashed p-5">
						<div className="flex items-center gap-2 font-medium">
							<CheckCircle2 className="h-4 w-4 text-muted-foreground" />
							No explicit policies in this context
						</div>
						<p className="mt-1 text-sm text-muted-foreground">
							Runs inherit from broader levels until you set a ceiling here.
						</p>
					</div>
				)}
				{policies.map((policy) => {
					const key = policyTargetKey(policy);
					const option = targetOptions.find(
						(target) => targetKey(target.scope, target.targetId) === key,
					);
					const label = option?.label ?? `${scopeLabel(policy.scope)} policy`;
					const deleting = deleteTargetKey === key;
					return (
						<div key={policy.id} className="rounded-2xl border p-4">
							<div className="flex flex-wrap items-start justify-between gap-3">
								<div>
									<div className="font-medium">{label}</div>
									<div className="text-sm text-muted-foreground">
										{scopeLabel(policy.scope)} · aggregate {policy.aggregate_period}
									</div>
								</div>
								<div className="flex gap-2">
									<Button
										variant="outline"
										size="sm"
										disabled={!canWrite || !option}
										onClick={() => option && onEdit(policy, option)}
									>
										Edit
									</Button>
									<Button
										variant="destructive"
										size="sm"
										disabled={!canWrite}
										onClick={() => onDelete(key)}
									>
										<Trash2 className="h-4 w-4" />
										Delete
									</Button>
								</div>
							</div>
							<div className="mt-3 grid gap-2 text-sm md:grid-cols-2">
								<PolicyCeilingSummary title="Per run" ceilings={policy.per_run} />
								<PolicyCeilingSummary title="Aggregate" ceilings={policy.aggregate} />
							</div>
							{deleting && (
								<div className="mt-3 rounded-2xl bg-destructive/10 p-3 text-sm">
									<div className="font-medium text-destructive">
										Delete this usage limit?
									</div>
									<p className="mt-1 text-muted-foreground">
										The target will immediately inherit from the next broader
										level.
									</p>
									<div className="mt-3 flex gap-2">
										<Button
											variant="destructive"
											size="sm"
											disabled={isDeleting}
											onClick={() => onConfirmDelete(key)}
										>
											Delete limit
										</Button>
										<Button variant="outline" size="sm" onClick={onCancelDelete}>
											Cancel
										</Button>
									</div>
								</div>
							)}
						</div>
					);
				})}
			</CardContent>
		</Card>
	);
}

function PolicyCeilingSummary({
	title,
	ceilings,
}: {
	title: string;
	ceilings: UsageLimitCeilings;
}) {
	const entries = Object.entries(ceilings).filter(([, value]) => value != null);
	return (
		<div className="rounded-2xl bg-muted/40 p-3">
			<div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
				{title}
			</div>
			{entries.length === 0 ? (
				<div className="mt-1 text-sm text-muted-foreground">Inherits</div>
			) : (
				<div className="mt-1 space-y-1">
					{entries.map(([dimension, value]) => (
						<div key={dimension} className="flex justify-between gap-2 text-sm">
							<span>{dimensionLabel(dimension)}</span>
							<span className="font-medium">
								{formatDimensionValue(dimension, Number(value))}
							</span>
						</div>
					))}
				</div>
			)}
		</div>
	);
}

function CeilingSection({
	title,
	description,
	values,
	onChange,
	showTokenDetails,
	disabled,
}: {
	title: string;
	description: string;
	values: Record<EditableDimension, string>;
	onChange: (dimension: EditableDimension, value: string) => void;
	showTokenDetails: boolean;
	disabled: boolean;
}) {
	return (
		<div className="space-y-3 rounded-2xl border p-3">
			<div>
				<div className="font-medium">{title}</div>
				<p className="text-xs text-muted-foreground">{description}</p>
			</div>
			<div className="grid gap-3 sm:grid-cols-2">
				{COMMON_DIMENSIONS.map((dimension) => (
					<DimensionInput
						key={dimension}
						dimension={dimension}
						value={values[dimension]}
						onChange={onChange}
						disabled={disabled}
					/>
				))}
			</div>
			{showTokenDetails && (
				<div className="grid gap-3 sm:grid-cols-2">
					{DETAIL_DIMENSIONS.map((dimension) => (
						<DimensionInput
							key={dimension}
							dimension={dimension}
							value={values[dimension]}
							onChange={onChange}
							disabled={disabled}
						/>
					))}
				</div>
			)}
		</div>
	);
}

function DimensionInput({
	dimension,
	value,
	onChange,
	disabled,
}: {
	dimension: EditableDimension;
	value: string;
	onChange: (dimension: EditableDimension, value: string) => void;
	disabled: boolean;
}) {
	const isDuration =
		dimension.endsWith("_duration_ms") || dimension === "sandbox_compute_ms";
	return (
		<div className="space-y-1">
			<Label htmlFor={`usage-${dimension}`} className="text-xs">
				{DIMENSION_LABELS[dimension]}
			</Label>
			<div className="relative">
				<Input
					id={`usage-${dimension}`}
					inputMode="numeric"
					min={0}
					type="number"
					value={value}
					placeholder="Inherit"
					disabled={disabled}
					onChange={(event) => onChange(dimension, event.target.value)}
					className={isDuration ? "pr-12" : undefined}
				/>
				{isDuration && (
					<span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
						min
					</span>
				)}
			</div>
		</div>
	);
}
