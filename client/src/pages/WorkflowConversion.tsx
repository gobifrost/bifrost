import { useMemo, useState } from "react";
import {
	Link,
	useLocation,
	useNavigate,
	useSearchParams,
} from "react-router-dom";
import {
	AlertCircle,
	CheckCircle2,
	ChevronLeft,
	ChevronRight,
	Loader2,
	ShieldCheck,
} from "lucide-react";

import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { PageWorkspace } from "@/components/layout/PageWorkspace";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { cancelPlatformJob } from "@/services/platformJobs";
import {
	useWorkflowConversionJob,
	useWorkflowConversionOperation,
} from "@/hooks/useWorkflowConversionJob";
import {
	analyzeWorkflowConversions,
	applyWorkflowConversion,
	cutOverWorkflowConversions,
	getWorkflowConversionReport,
	type WorkflowConversionReport,
} from "@/services/workflowConversions";

type Item = WorkflowConversionReport["items"][number];
type IdentityStrategy = "dedicated" | "keep_current";

const PAGE_SIZE = 25;
const ACTIVE = new Set(["queued", "running", "waiting", "cancel_requested"]);
const reviewable = new Set(["ready", "needs_review"]);

function workflowIdsFromNavigationState(state: unknown): string[] | null {
	if (typeof state !== "object" || state === null) return null;
	const workflowIds = (state as Record<string, unknown>).workflowIds;
	return Array.isArray(workflowIds) &&
		workflowIds.every((workflowId) => typeof workflowId === "string")
		? workflowIds
		: null;
}

function statusLabel(value: string) {
	return value
		.replaceAll("_", " ")
		.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function StepIndicator({
	stage,
	platformIntent,
}: {
	stage: "scope" | "analyze" | "review" | "apply";
	platformIntent: boolean;
}) {
	const steps = ["Scope", "Analyze", "Review", "Apply & verify"];
	const current = ["scope", "analyze", "review", "apply"].indexOf(stage);
	return (
		<ol
			aria-label="Conversion steps"
			className="flex flex-wrap gap-x-5 gap-y-2 text-sm"
		>
			{steps.map((label, index) => (
				<li
					key={label}
					className={
						index === current
							? "font-semibold text-foreground"
							: index < current
								? "text-primary"
								: "text-muted-foreground"
					}
					aria-current={index === current ? "step" : undefined}
				>
					{index + 1}. {label}
				</li>
			))}
			{platformIntent && (
				<li className="text-muted-foreground">5. Platform cutover</li>
			)}
		</ol>
	);
}

function Evidence({ item }: { item: Item }) {
	const evidenceLines = (item.evidence ?? []).map((entry) => {
		if (typeof entry !== "object" || entry === null) return String(entry);
		const detail = entry as {
			path: string;
			line?: number | null;
			call: string;
			via: string[];
		};
		const location = `${detail.path}${detail.line == null ? "" : `:${detail.line}`}`;
		return `${location} → ${[...detail.via, detail.call].join(" → ")}`;
	});
	return (
		<Accordion type="single" collapsible>
			<AccordionItem value="evidence">
				<AccordionTrigger>Evidence and uncertainty</AccordionTrigger>
				<AccordionContent className="space-y-3 text-muted-foreground">
					<p>
						Source requirements and observed operations are inputs
						to this proposal. They do not prove unexecuted code
						paths are safe.
					</p>
					{evidenceLines.length > 0 ? (
						<ul className="list-disc space-y-1 pl-5">
							{evidenceLines.map((line, index) => (
								<li
									key={index}
									className="[overflow-wrap:anywhere]"
								>
									{line}
								</li>
							))}
						</ul>
					) : (
						<p>
							No compact evidence was retained for this workflow.
						</p>
					)}
					{(item.unknowns ?? []).length > 0 && (
						<p className="text-[var(--bf-warning)]">
							Unresolved: {(item.unknowns ?? []).join("; ")}
						</p>
					)}
				</AccordionContent>
			</AccordionItem>
		</Accordion>
	);
}

function reviewedIdentityStrategy(item: Item): IdentityStrategy | null {
	const strategy = item.review?.identity_strategy;
	return strategy === "dedicated" || strategy === "keep_current"
		? strategy
		: null;
}

export function WorkflowConversion() {
	const [params] = useSearchParams();
	return <WorkflowConversionWizard key={params.get("job") ?? "scope"} />;
}

function WorkflowConversionWizard() {
	const location = useLocation();
	const navigate = useNavigate();
	const [params, setParams] = useSearchParams();
	const jobId = params.get("job");
	const operationId = params.get("operation");
	const queryWorkflowIds = useMemo(
		() => (params.get("workflows") ?? "").split(",").filter(Boolean),
		[params],
	);
	const isNavigationSelection = params.get("selection") === "true";
	const navigationWorkflowIds = useMemo(
		() => workflowIdsFromNavigationState(location.state),
		[location.state],
	);
	const selectionLost =
		isNavigationSelection &&
		(!navigationWorkflowIds || navigationWorkflowIds.length === 0);
	const workflowIds = isNavigationSelection
		? (navigationWorkflowIds ?? [])
		: queryWorkflowIds;
	const queryPlatformIntent = params.get("platform") === "true";
	const { job, report, error, loading, refresh, setReport } =
		useWorkflowConversionJob(jobId);
	const operation = useWorkflowConversionOperation(operationId);
	const platformIntent =
		queryPlatformIntent || report?.summary.platform_cutover === true;
	const cutoverReady = report?.summary.cutover_ready === true;
	const [starting, setStarting] = useState(false);
	const [mutating, setMutating] = useState(false);
	const [localError, setLocalError] = useState<string | null>(null);
	const [selected, setSelected] = useState<
		Record<
			string,
			{
				source_fingerprint: string;
				identity_strategy: IdentityStrategy;
				acknowledged_unknowns: boolean;
				acknowledged_broad_identity: boolean;
				requires_solution_ack: boolean;
			}
		>
	>({});
	const [acknowledgedSolutionRequests, setAcknowledgedSolutionRequests] =
		useState(false);
	const [acknowledgedExceptions, setAcknowledgedExceptions] = useState(false);

	const stage: "scope" | "analyze" | "review" | "apply" = !jobId
		? "scope"
		: operationId
			? "apply"
			: ACTIVE.has(job?.status ?? "queued") || loading
				? "analyze"
				: job?.status === "succeeded"
					? "review"
					: "analyze";
	const items = report?.items ?? [];
	const selectedEntries = Object.entries(selected);
	const hasSolutionRequests = selectedEntries.some(
		([, selection]) => selection.requires_solution_ack,
	);

	const start = async () => {
		if (selectionLost) return;
		setStarting(true);
		setLocalError(null);
		try {
			const accepted = await analyzeWorkflowConversions({
				...(workflowIds.length
					? { workflow_ids: workflowIds }
					: { scope: "all" }),
				platform_cutover: platformIntent,
			});
			setParams({
				job: accepted.job_id,
				...(platformIntent ? { platform: "true" } : {}),
			});
		} catch (cause) {
			setLocalError(
				cause instanceof Error
					? cause.message
					: "Could not start the analysis.",
			);
		} finally {
			setStarting(false);
		}
	};

	const changePage = async (offset: number) => {
		if (!jobId) return;
		try {
			setReport(
				await getWorkflowConversionReport(jobId, {
					offset,
					limit: PAGE_SIZE,
				}),
			);
		} catch (cause) {
			setLocalError(
				cause instanceof Error
					? cause.message
					: "Could not load that report page.",
			);
		}
	};

	const apply = async () => {
		if (!jobId || selectedEntries.length === 0) return;
		setMutating(true);
		setLocalError(null);
		try {
			const accepted = await applyWorkflowConversion(jobId, {
				selections: selectedEntries.map(([workflow_id, selection]) => ({
					workflow_id,
					source_fingerprint: selection.source_fingerprint,
					identity_strategy: selection.identity_strategy,
					acknowledged_unknowns: selection.acknowledged_unknowns,
					acknowledged_broad_identity:
						selection.acknowledged_broad_identity,
				})),
				acknowledged_solution_requests: acknowledgedSolutionRequests,
			});
			setSelected({});
			setAcknowledgedSolutionRequests(false);
			setParams({
				job: jobId,
				operation: accepted.job_id,
				...(platformIntent ? { platform: "true" } : {}),
			});
		} catch (cause) {
			setLocalError(
				cause instanceof Error
					? cause.message
					: "Could not apply the reviewed changes.",
			);
		} finally {
			setMutating(false);
		}
	};

	const cutover = async () => {
		if (!jobId || !acknowledgedExceptions) return;
		setMutating(true);
		setLocalError(null);
		try {
			const accepted = await cutOverWorkflowConversions(jobId, {
				acknowledged_remaining_exceptions: true,
			});
			setParams({
				job: jobId,
				operation: accepted.job_id,
				platform: "true",
			});
		} catch (cause) {
			setLocalError(
				cause instanceof Error
					? cause.message
					: "Could not change the platform default.",
			);
		} finally {
			setMutating(false);
		}
	};

	return (
		<PageWorkspace className="mx-auto w-full max-w-7xl space-y-6">
			<ListPageHeader
				title="Convert workflow access"
				description="Review a bounded, read-only proposal before changing any workflow permissions or unattended identity."
				actions={
					jobId ? (
						<Button
							variant="outline"
							onClick={() => navigate("/workflows")}
						>
							Leave and reopen later
						</Button>
					) : undefined
				}
			>
				<StepIndicator stage={stage} platformIntent={platformIntent} />
			</ListPageHeader>
			<div aria-live="polite" role="status" className="sr-only">
				{job
					? `Conversion job ${job.status}`
					: "Choose a conversion scope"}
			</div>
			{(error || localError) && (
				<Alert variant="destructive">
					<AlertCircle className="size-4" />
					<AlertTitle>Conversion needs attention</AlertTitle>
					<AlertDescription className="flex flex-wrap items-center gap-3">
						{error || localError}
						<Button
							variant="outline"
							size="sm"
							onClick={() => void refresh()}
						>
							Try again
						</Button>
					</AlertDescription>
				</Alert>
			)}
			{stage === "scope" && (
				<section
					aria-labelledby="conversion-scope"
					className="max-w-3xl space-y-5"
				>
					<div className="space-y-2">
						<h2
							id="conversion-scope"
							className="text-lg font-semibold"
						>
							Scope
						</h2>
						<p className="text-sm text-muted-foreground">
							{selectionLost
								? "This selection is no longer available. Return to Workflows, choose the workflows again, then start a new analysis."
								: workflowIds.length
									? `${workflowIds.length} selected workflow${workflowIds.length === 1 ? "" : "s"} will be frozen into this analysis.`
									: "All workflows you are authorized to review will be analyzed. This is an explicit all-workflows scope, not the workflows currently loaded on another page."}{" "}
							{!selectionLost &&
								(workflowIds.length
									? "This selected-workflow analysis covers only those workflows, using a 30-day observed operation window."
									: "The all-workflows analysis includes active workflows, tools, data providers, and services, using a 30-day observed operation window.")}
						</p>
					</div>
					<Alert>
						<ShieldCheck className="size-4" />
						<AlertDescription>
							Analysis is read-only. Applying later never runs a
							workflow and does not silently add organization
							reach.
						</AlertDescription>
					</Alert>
					<Button
						onClick={() => void start()}
						disabled={starting || selectionLost}
					>
						{starting && (
							<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
						)}
						{workflowIds.length || selectionLost
							? "Analyze selected workflows"
							: "Analyze all workflows"}
					</Button>
				</section>
			)}
			{stage === "analyze" && (
				<section
					aria-labelledby="conversion-analysis"
					className="max-w-3xl space-y-5"
				>
					<div className="space-y-2">
						<h2
							id="conversion-analysis"
							className="text-lg font-semibold"
						>
							Analysis in progress
						</h2>
						<p className="text-sm text-muted-foreground">
							{job?.progress.phase ??
								"Restoring analysis status…"}
						</p>
					</div>
					{job?.progress.percent != null && (
						<Progress
							aria-label="Analysis progress"
							value={job.progress.percent}
						/>
					)}
					{job?.progress.total != null && (
						<p className="text-sm text-muted-foreground">
							{job.progress.current} of {job.progress.total}{" "}
							completed
						</p>
					)}
					{job && ACTIVE.has(job.status) && (
						<Button
							variant="outline"
							disabled={mutating}
							onClick={() => {
								setMutating(true);
								void cancelPlatformJob(job.id)
									.then(refresh)
									.catch((cause) =>
										setLocalError(
											cause instanceof Error
												? cause.message
												: "Could not cancel the analysis.",
										),
									)
									.finally(() => setMutating(false));
							}}
						>
							Cancel analysis
						</Button>
					)}
					{job && !ACTIVE.has(job.status) && (
						<Alert
							variant={
								job.status === "succeeded"
									? "default"
									: "destructive"
							}
						>
							<AlertDescription>
								{job.status === "succeeded"
									? "Analysis is ready to review."
									: `Analysis ${statusLabel(job.status)}. Open the job again after resolving the issue.`}
							</AlertDescription>
						</Alert>
					)}
				</section>
			)}
			{stage === "review" && (
				<section
					aria-labelledby="conversion-review"
					className="space-y-5"
				>
					<div>
						<h2
							id="conversion-review"
							className="text-lg font-semibold"
						>
							Review proposals
						</h2>
						<p className="text-sm text-muted-foreground">
							{report?.summary.observation_window_days} day
							observation window. Selected reviews remain
							available across report pages.
						</p>
					</div>
					<Alert>
						<AlertDescription>
							Changing mode does not reduce a broad existing
							identity. Select only proposals you have reviewed.
						</AlertDescription>
					</Alert>
					<div className="max-h-[min(60vh,42rem)] space-y-3 overflow-auto pr-1">
						<div className="sr-only">
							<div>
								<span>Select</span>
								<span>Workflow</span>
								<span>Current → proposed</span>
								<span>Readiness</span>
								<span>Identity</span>
							</div>
						</div>
						<div className="space-y-3">
							{items.map((item) => {
								const selection = selected[item.workflow_id];
								const selectable = reviewable.has(item.status);
								const appliedIdentityStrategy =
									reviewedIdentityStrategy(item);
								return (
									<article
										key={item.workflow_id}
										className="space-y-4 rounded-[var(--bf-radius-surface)] border border-border bg-card p-4"
									>
										<div className="flex flex-wrap items-center gap-3">
											<Checkbox
												aria-label={`Select ${item.workflow_name}`}
												checked={!!selection}
												disabled={!selectable}
												onCheckedChange={(checked) =>
													setSelected((current) => {
														const next = {
															...current,
														};
														if (checked)
															next[
																item.workflow_id
															] = {
																source_fingerprint:
																	item.source_fingerprint,
																identity_strategy:
																	"dedicated",
																acknowledged_unknowns:
																	(
																		item.unknowns ??
																		[]
																	).length ===
																	0,
																acknowledged_broad_identity: false,
																requires_solution_ack:
																	item.solution_id !=
																		null ||
																	item.solution_permission_request !=
																		null,
															};
														else
															delete next[
																item.workflow_id
															];
														return next;
													})
												}
											/>
											<div className="min-w-0 flex-1">
												<p className="font-medium [overflow-wrap:anywhere]">
													{item.workflow_name}
												</p>
											</div>
											<Badge
												className="shrink-0"
												variant={
													item.status === "ready"
														? "secondary"
														: "outline"
												}
											>
												{statusLabel(item.status)}
											</Badge>
										</div>
										{item.error && (
											<p
												role="alert"
												className="text-sm text-destructive"
											>
												{item.error}
											</p>
										)}
										{(item.status === "stale" ||
											item.status === "blocked") && (
											<Button
												asChild
												variant="outline"
												size="sm"
											>
												<Link
													to={`/workflows/convert?workflows=${encodeURIComponent(item.workflow_id)}`}
												>
													Analyze this workflow again
												</Link>
											</Button>
										)}
										<div className="grid gap-4 sm:grid-cols-2">
											<section className="min-w-0 space-y-1">
												<h3 className="text-sm font-medium">
													Current → proposed
												</h3>
												<p className="text-sm [overflow-wrap:anywhere]">
													{item.current_permission_mode ===
													"full"
														? "Full Access"
														: "Declared Permissions"}{" "}
													→ Declared Permissions (
													{
														(
															item.proposed_permissions ??
															[]
														).length
													}
													)
												</p>
												<p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
													Proposed permissions:{" "}
													{(
														item.proposed_permissions ??
														[]
													).join(", ") || "None"}
												</p>
											</section>
											<section className="min-w-0 space-y-1">
												<h3 className="text-sm font-medium">
													Current identity
												</h3>
												<p className="text-sm [overflow-wrap:anywhere]">
													{item.identity?.name ??
														"Identity unavailable"}
												</p>
												<p className="text-xs text-muted-foreground [overflow-wrap:anywhere]">
													Organization:{" "}
													{item.identity
														? (item.identity
																.organization_id ??
															"Global")
														: "Unavailable"}
												</p>
												<p className="text-xs text-muted-foreground">
													{item.identity
														? item.identity.shared
															? "Shared identity"
															: "Dedicated identity"
														: "Identity type unavailable"}
												</p>
												{item.identity
													?.broad_power_warning && (
													<p className="text-xs text-[var(--bf-warning)]">
														This identity has broad
														authority.
													</p>
												)}
												<p className="text-xs text-muted-foreground">
													Proposed dedicated identity:
													User base role in this
													workflow's organization,
													with no extra roles.
												</p>
												{item.status ===
													"already_declared" &&
													appliedIdentityStrategy && (
														<p className="text-xs text-muted-foreground">
															Applied review:{" "}
															{appliedIdentityStrategy ===
															"dedicated"
																? "Dedicated identity"
																: "Keep current identity"}
														</p>
													)}
											</section>
										</div>
										<div className="grid gap-3 sm:grid-cols-2">
											<p className="min-w-0 text-xs text-muted-foreground [overflow-wrap:anywhere]">
												Source:{" "}
												{(
													item.source_permissions ??
													[]
												).join(", ") || "None"}
											</p>
											<p className="min-w-0 text-xs text-muted-foreground [overflow-wrap:anywhere]">
												Observed:{" "}
												{(
													item.observed_permissions ??
													[]
												).join(", ") || "None"}
											</p>
										</div>
										<Evidence item={item} />
										<div className="space-y-1">
											{(item.warnings ?? []).map(
												(warning) => (
													<p
														key={warning}
														className="text-xs text-[var(--bf-warning)]"
													>
														{warning}
													</p>
												),
											)}
										</div>
										<div>
											{selection && (
												<fieldset className="space-y-1">
													<label className="flex gap-2 text-xs">
														<input
															type="radio"
															checked={
																selection.identity_strategy ===
																"dedicated"
															}
															onChange={() =>
																setSelected(
																	(
																		current,
																	) => ({
																		...current,
																		[item.workflow_id]:
																			{
																				...selection,
																				identity_strategy:
																					"dedicated",
																				acknowledged_broad_identity: false,
																			},
																	}),
																)
															}
														/>
														Dedicated
													</label>
													<label className="flex gap-2 text-xs">
														<input
															type="radio"
															checked={
																selection.identity_strategy ===
																"keep_current"
															}
															onChange={() =>
																setSelected(
																	(
																		current,
																	) => ({
																		...current,
																		[item.workflow_id]:
																			{
																				...selection,
																				identity_strategy:
																					"keep_current",
																			},
																	}),
																)
															}
														/>
														Keep current
													</label>
													{selection.identity_strategy ===
														"keep_current" && (
														<label className="flex gap-2 text-xs">
															<Checkbox
																checked={
																	selection.acknowledged_broad_identity
																}
																onCheckedChange={(
																	value,
																) =>
																	setSelected(
																		(
																			current,
																		) => ({
																			...current,
																			[item.workflow_id]:
																				{
																					...selection,
																					acknowledged_broad_identity:
																						value ===
																						true,
																				},
																		}),
																	)
																}
															/>
															Acknowledge existing
															authority
														</label>
													)}
													{(item.unknowns ?? [])
														.length > 0 && (
														<label className="flex gap-2 text-xs">
															<Checkbox
																checked={
																	selection.acknowledged_unknowns
																}
																onCheckedChange={(
																	value,
																) =>
																	setSelected(
																		(
																			current,
																		) => ({
																			...current,
																			[item.workflow_id]:
																				{
																					...selection,
																					acknowledged_unknowns:
																						value ===
																						true,
																				},
																		}),
																	)
																}
															/>
															Acknowledge
															unresolved code
														</label>
													)}
												</fieldset>
											)}
										</div>
									</article>
								);
							})}
						</div>
					</div>
					{report && report.total > report.limit && (
						<nav
							aria-label="Report pages"
							className="flex justify-end gap-2"
						>
							<Button
								variant="outline"
								disabled={report.offset === 0}
								onClick={() =>
									void changePage(
										Math.max(
											0,
											report.offset - report.limit,
										),
									)
								}
							>
								<ChevronLeft />
								Previous
							</Button>
							<Button
								variant="outline"
								disabled={
									report.offset + report.limit >= report.total
								}
								onClick={() =>
									void changePage(
										report.offset + report.limit,
									)
								}
							>
								Next
								<ChevronRight />
							</Button>
						</nav>
					)}
					<div className="space-y-3 border-t pt-5">
						{hasSolutionRequests && (
							<label className="flex gap-2 text-sm">
								<Checkbox
									checked={acknowledgedSolutionRequests}
									onCheckedChange={(value) =>
										setAcknowledgedSolutionRequests(
											value === true,
										)
									}
								/>
								I reviewed selected Solution declarations.
							</label>
						)}
						<Button
							disabled={
								selectedEntries.length === 0 ||
								selectedEntries.some(
									([, selection]) =>
										!selection.acknowledged_unknowns ||
										(selection.identity_strategy ===
											"keep_current" &&
											!selection.acknowledged_broad_identity),
								) ||
								(hasSolutionRequests &&
									!acknowledgedSolutionRequests) ||
								mutating
							}
							onClick={() => void apply()}
						>
							Apply {selectedEntries.length} reviewed proposal
							{selectedEntries.length === 1 ? "" : "s"}
						</Button>
					</div>
					{platformIntent && (
						<section className="space-y-3 border-t pt-6">
							<h2 className="text-lg font-semibold">
								Platform cutover
							</h2>
							<p className="text-sm text-muted-foreground">
								{report?.summary.ready ?? 0} ready;{" "}
								{report?.summary.needs_review ?? 0} need review;{" "}
								{report?.summary.blocked ?? 0} blocked;{" "}
								{report?.summary.stale ?? 0} stale;{" "}
								{report?.summary.remaining_full ?? 0} Full
								Access or inherited default exceptions remain.
								The backend rechecks this state before the
								change.
							</p>
							<label className="flex gap-2 text-sm">
								<Checkbox
									checked={acknowledgedExceptions}
									onCheckedChange={(value) =>
										setAcknowledgedExceptions(
											value === true,
										)
									}
								/>
								I reviewed remaining exceptions.
							</label>
							<Button
								variant="destructive"
								disabled={
									!cutoverReady ||
									!acknowledgedExceptions ||
									mutating
								}
								onClick={() => void cutover()}
							>
								Change platform default to Declared Permissions
							</Button>
							{!cutoverReady && (
								<p className="text-sm text-muted-foreground">
									Resolve remaining Full Access, review,
									blocked, and stale workflows before cutover.
								</p>
							)}
						</section>
					)}
				</section>
			)}
			{stage === "apply" && (
				<section
					aria-labelledby="conversion-apply"
					className="max-w-3xl space-y-5"
				>
					<div className="space-y-2">
						<h2
							id="conversion-apply"
							className="text-lg font-semibold"
						>
							Apply and verify
						</h2>
						<p className="text-sm text-muted-foreground">
							{operation.job?.progress.phase ??
								"Restoring the requested change…"}
						</p>
					</div>
					{operation.error && (
						<Alert variant="destructive">
							<AlertDescription>
								{operation.error}
							</AlertDescription>
						</Alert>
					)}
					{operation.job?.progress.percent != null && (
						<Progress
							aria-label="Apply progress"
							value={operation.job.progress.percent}
						/>
					)}
					{operation.job?.status === "succeeded" && (
						<Alert>
							<CheckCircle2 className="size-4" />
							<AlertDescription>
								The requested change completed. Review each
								workflow before running it; this conversion did
								not execute a workflow.
							</AlertDescription>
						</Alert>
					)}
					{operation.job &&
						["failed", "cancelled", "requires_action"].includes(
							operation.job.status,
						) && (
							<Alert variant="destructive">
								<AlertDescription>
									The requested change{" "}
									{statusLabel(operation.job.status)}. The
									durable job record keeps any partial
									outcomes for review.
								</AlertDescription>
							</Alert>
						)}
					{operation.job && ACTIVE.has(operation.job.status) && (
						<Button
							variant="outline"
							onClick={() =>
								void cancelPlatformJob(operation.job!.id).then(
									operation.refresh,
								)
							}
						>
							Cancel requested change
						</Button>
					)}
					<Button
						variant="outline"
						onClick={() =>
							setParams({
								job: jobId ?? "",
								...(platformIntent ? { platform: "true" } : {}),
							})
						}
					>
						Return to review
					</Button>
				</section>
			)}
			{job?.status === "succeeded" &&
				stage === "review" &&
				selectedEntries.length === 0 && (
					<p className="text-sm text-muted-foreground">
						<CheckCircle2 className="mr-2 inline size-4 text-[var(--bf-success)]" />
						Choose reviewed workflows above. Successful applies link
						back to each workflow; no workflow execution occurs
						here.
					</p>
				)}
			{job?.status === "succeeded" && (
				<Link
					className="text-sm text-primary underline"
					to="/workflows"
				>
					Back to workflows
				</Link>
			)}
		</PageWorkspace>
	);
}
