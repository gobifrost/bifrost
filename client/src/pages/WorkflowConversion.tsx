import { useMemo, useRef, useState } from "react";
import {
	Link,
	useLocation,
	useNavigate,
	useSearchParams,
} from "react-router-dom";
import {
	AlertCircle,
	CheckCircle2,
	X,
	Loader2,
	ShieldCheck,
} from "lucide-react";

import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { PageWorkspace } from "@/components/layout/PageWorkspace";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { PaginationFooter } from "@/components/pagination/PaginationFooter";
import {
	ConversionWizardFrame,
	type ConversionStage,
} from "@/components/workflows/conversion/ConversionWizardFrame";
import { ConversionJobProgress } from "@/components/workflows/conversion/ConversionJobProgress";
import { cancelPlatformJob } from "@/services/platformJobs";
import {
	useWorkflowConversionJob,
	useWorkflowConversionOperation,
} from "@/hooks/useWorkflowConversionJob";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import {
	analyzeWorkflowConversions,
	applyWorkflowConversion,
	cutOverWorkflowConversions,
	getWorkflowConversionReport,
	type WorkflowConversionReport,
} from "@/services/workflowConversions";

type Item = WorkflowConversionReport["items"][number];
type IdentityStrategy = "dedicated" | "keep_current";
type Selection = {
	workflow_name: string;
	source_fingerprint: string;
	identity_strategy: IdentityStrategy;
	acknowledged_unknowns: boolean;
	acknowledged_broad_identity: boolean;
	requires_solution_ack: boolean;
};
const PAGE_SIZE = 25;
const ACTIVE = new Set(["queued", "running", "waiting", "cancel_requested"]);
const reviewable = new Set(["ready", "needs_review"]);

function statusLabel(value: string) {
	return value
		.replaceAll("_", " ")
		.replace(/\b\w/g, (letter) => letter.toUpperCase());
}
function navigationSelection(state: unknown) {
	if (typeof state !== "object" || state === null) return null;
	const ids = (state as Record<string, unknown>).workflowIds;
	return Array.isArray(ids) && ids.every((id) => typeof id === "string")
		? ids
		: null;
}

function Evidence({ item }: { item: Item }) {
	return (
		<details className="border-t pt-4 text-sm">
			<summary className="cursor-pointer font-medium">
				Evidence and uncertainty
			</summary>
			<div className="mt-3 space-y-2 text-muted-foreground">
				<p>
					Source requirements and observed operations inform this
					proposal; they do not prove unexecuted code paths are safe.
				</p>
				{(item.evidence ?? []).length > 0 && (
					<ul className="list-disc space-y-1 pl-5">
						{(item.evidence ?? []).map((entry, index) => {
							const detail = entry as {
								path?: string;
								line?: number | null;
								call?: string;
								via?: string[];
							};
							const trace =
								typeof entry === "object" &&
								entry !== null &&
								detail.path
									? `${detail.path}${detail.line == null ? "" : `:${detail.line}`} → ${[...(detail.via ?? []), detail.call].filter(Boolean).join(" → ")}`
									: String(entry);
							return (
								<li
									key={index}
									className="[overflow-wrap:anywhere]"
								>
									{trace}
								</li>
							);
						})}
					</ul>
				)}
				{(item.unknowns ?? []).length > 0 && (
					<p className="text-[var(--bf-warning)]">
						Unresolved: {item.unknowns?.join("; ")}
					</p>
				)}
			</div>
		</details>
	);
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
	const queryIds = useMemo(
		() => (params.get("workflows") ?? "").split(",").filter(Boolean),
		[params],
	);
	const stateIds = useMemo(
		() => navigationSelection(location.state),
		[location.state],
	);
	const selectionLost =
		params.get("selection") === "true" &&
		(!stateIds || stateIds.length === 0);
	const workflowIds =
		params.get("selection") === "true" ? (stateIds ?? []) : queryIds;
	const { job, report, error, loading, refresh, setReport } =
		useWorkflowConversionJob(jobId);
	const operation = useWorkflowConversionOperation(operationId);
	const desktop = useMediaQuery("(min-width: 1024px)");
	const reviewTriggerRef = useRef<HTMLButtonElement | null>(null);
	const platform =
		params.get("platform") === "true" ||
		report?.summary.platform_cutover === true;
	const [starting, setStarting] = useState(false);
	const [mutating, setMutating] = useState(false);
	const [localError, setLocalError] = useState<string | null>(null);
	const [selected, setSelected] = useState<Record<string, Selection>>({});
	const [detailId, setDetailId] = useState<string | null>(null);
	const [sheetOpen, setSheetOpen] = useState(false);
	const [confirmApply, setConfirmApply] = useState(false);
	const [confirmCutover, setConfirmCutover] = useState(false);
	const [solutionAck, setSolutionAck] = useState(false);
	const [exceptionsAck, setExceptionsAck] = useState(false);
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
	const detail =
		items.find((item) => item.workflow_id === detailId) ?? items[0] ?? null;
	const entries = Object.entries(selected);
	const solutionRequired = entries.some(
		([, value]) => value.requires_solution_ack,
	);
	const selectionReady =
		entries.length > 0 &&
		!entries.some(
			([, value]) =>
				!value.acknowledged_unknowns ||
				(value.identity_strategy === "keep_current" &&
					!value.acknowledged_broad_identity),
		);
	const select = (item: Item, checked: boolean) =>
		setSelected((current) => {
			const next = { ...current };
			if (checked)
				next[item.workflow_id] = {
					workflow_name: item.workflow_name,
					source_fingerprint: item.source_fingerprint,
					identity_strategy: "dedicated",
					acknowledged_unknowns: (item.unknowns ?? []).length === 0,
					acknowledged_broad_identity: false,
					requires_solution_ack:
						item.solution_id != null ||
						item.solution_permission_request != null,
				};
			else delete next[item.workflow_id];
			return next;
		});
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
	const start = async () => {
		if (selectionLost) return;
		setStarting(true);
		setLocalError(null);
		try {
			const accepted = await analyzeWorkflowConversions({
				...(workflowIds.length
					? { workflow_ids: workflowIds }
					: { scope: "all" }),
				platform_cutover: platform,
			});
			setParams({
				job: accepted.job_id,
				...(platform ? { platform: "true" } : {}),
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
	const apply = async () => {
		if (!jobId || !selectionReady || (solutionRequired && !solutionAck))
			return;
		setMutating(true);
		setLocalError(null);
		try {
			const accepted = await applyWorkflowConversion(jobId, {
				selections: entries.map(([workflow_id, value]) => ({
					workflow_id,
					source_fingerprint: value.source_fingerprint,
					identity_strategy: value.identity_strategy,
					acknowledged_unknowns: value.acknowledged_unknowns,
					acknowledged_broad_identity:
						value.acknowledged_broad_identity,
				})),
				acknowledged_solution_requests: solutionAck,
			});
			setSelected({});
			setSolutionAck(false);
			setParams({
				job: jobId,
				operation: accepted.job_id,
				...(platform ? { platform: "true" } : {}),
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
		if (!jobId || !exceptionsAck) return;
		setMutating(true);
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
	const inspector = detail && (
		<Inspector
			item={detail}
			selection={selected[detail.workflow_id]}
			update={(change) =>
				setSelected((current) => ({
					...current,
					[detail.workflow_id]: {
						...current[detail.workflow_id],
						...change,
					},
				}))
			}
		/>
	);
	const frameStage: ConversionStage =
		confirmCutover ||
		operation.job?.job_type === "workflow.permissions.cutover"
			? "cutover"
			: stage;
	const cancelAnalysis = () => {
		if (!job) return;
		setMutating(true);
		void cancelPlatformJob(job.id)
			.then(refresh)
			.catch((cause) =>
				setLocalError(
					cause instanceof Error
						? cause.message
						: "Could not cancel analysis.",
				),
			)
			.finally(() => setMutating(false));
	};
	const returnToReview = () => {
		void refresh();
		setParams({
			job: jobId ?? "",
			...(platform ? { platform: "true" } : {}),
		});
	};
	return (
		<PageWorkspace className="mx-auto h-full w-full max-w-7xl gap-4">
			<ListPageHeader
				title="Convert workflow access"
				className="flex-row flex-nowrap"
				titleClassName="text-xl sm:text-3xl"
				descriptionClassName="hidden sm:block"
				description="Review proposed permissions and run identities before applying changes."
				actions={
					jobId ? (
						<Button
							variant="outline"
							aria-label="Leave and reopen later"
							onClick={() => navigate("/workflows")}
						>
							<X
								aria-hidden="true"
								className="size-4 sm:hidden"
							/>
							<span className="hidden sm:inline">
								Leave and reopen later
							</span>
						</Button>
					) : undefined
				}
			/>
			<ConversionWizardFrame
				stage={frameStage}
				platformIntent={platform}
				workspace={stage === "review" && (report?.total ?? 0) > 6}
				footer={
					stage === "review" ? (
						<>
							<p className="text-sm text-muted-foreground">
								{entries.length === 0
									? "0 selected"
									: selectionReady
										? `${entries.length} workflow${entries.length === 1 ? "" : "s"} ready to confirm.`
										: "Finish the required acknowledgements in each selected workflow."}
							</p>
							<div className="flex flex-wrap gap-2">
								{platform && (
									<Button
										variant="outline"
										className={
											report?.summary.cutover_ready
												? undefined
												: "hidden sm:inline-flex"
										}
										disabled={
											report?.summary.cutover_ready !==
												true || mutating
										}
										onClick={() => setConfirmCutover(true)}
									>
										Review platform cutover
									</Button>
								)}
								{entries.length > 0 && (
									<Button
										variant="ghost"
										onClick={() => setSelected({})}
									>
										Clear selection
									</Button>
								)}
								<Button
									disabled={!selectionReady || mutating}
									onClick={() => setConfirmApply(true)}
								>
									Review selected →
								</Button>
							</div>
						</>
					) : stage === "scope" ? (
						<>
							<Button asChild variant="outline">
								<Link to="/workflows">Back to workflows</Link>
							</Button>
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
						</>
					) : stage === "analyze" ? (
						<>
							<p className="text-xs text-muted-foreground">
								Progress is saved when you leave.
							</p>
							{job && ACTIVE.has(job.status) ? (
								<Button
									variant="outline"
									disabled={mutating || !job.can_cancel}
									onClick={cancelAnalysis}
								>
									{job.status === "cancel_requested"
										? "Stopping analysis…"
										: "Cancel analysis"}
								</Button>
							) : (
								<Button asChild variant="outline">
									<Link to="/workflows">
										Back to workflows
									</Link>
								</Button>
							)}
						</>
					) : (
						<>
							<p className="text-xs text-muted-foreground">
								Changes and partial outcomes are saved.
							</p>
							<div className="flex flex-wrap gap-2">
								{operation.job &&
									ACTIVE.has(operation.job.status) && (
										<Button
											variant="outline"
											disabled={
												mutating ||
												!operation.job.can_cancel
											}
											onClick={() =>
												void cancelPlatformJob(
													operation.job!.id,
												)
													.then(operation.refresh)
													.catch((cause) =>
														setLocalError(
															cause instanceof
																Error
																? cause.message
																: "Could not cancel changes.",
														),
													)
											}
										>
											{operation.job.status ===
											"cancel_requested"
												? "Stopping changes…"
												: "Cancel requested change"}
										</Button>
									)}
								<Button
									variant="outline"
									onClick={returnToReview}
								>
									Return to review
								</Button>
							</div>
						</>
					)
				}
			>
				<div aria-live="polite" role="status" className="sr-only">
					{job
						? `Conversion job ${job.status}`
						: "Choose a conversion scope"}
				</div>
				{(error || localError) && (
					<Alert variant="destructive" className="mb-4 shrink-0">
						<AlertCircle className="size-4" />
						<AlertTitle>Conversion needs attention</AlertTitle>
						<AlertDescription>
							{error || localError}
						</AlertDescription>
					</Alert>
				)}
				{stage === "scope" && (
					<Scope ids={workflowIds} selectionLost={selectionLost} />
				)}
				{stage === "analyze" && <ProgressStage job={job} />}
				{stage === "review" && (
					<div
						className={`flex min-h-0 flex-col ${(report?.total ?? 0) > 6 ? "flex-1" : ""}`}
					>
						<div className="mb-4 shrink-0">
							<h2 className="text-lg font-semibold">
								Review proposals
							</h2>
							<p className="text-sm text-muted-foreground">
								{report?.summary.observation_window_days} day
								observation window. Selections persist across
								report pages.
							</p>
						</div>
						<div
							aria-label="Readiness summary"
							className="mb-4 flex shrink-0 flex-wrap gap-2 text-xs"
						>
							{[
								["Ready", report?.summary.ready ?? 0],
								[
									"Needs review",
									report?.summary.needs_review ?? 0,
								],
								["Blocked", report?.summary.blocked ?? 0],
								["Stale", report?.summary.stale ?? 0],
								[
									"Already declared",
									report?.summary.already_declared ?? 0,
								],
								...(platform
									? [
											[
												"Full Access remaining",
												report?.summary
													.remaining_full ?? 0,
											],
										]
									: []),
							]
								.filter(
									([label, count]) =>
										!["Stale", "Already declared"].includes(
											String(label),
										) || Number(count) > 0,
								)
								.map(([label, count]) => (
									<span
										key={label}
										className="rounded-md border bg-muted/30 px-2.5 py-1.5"
									>
										<span className="font-medium">
											{Number(count).toLocaleString()}
										</span>{" "}
										<span className="text-muted-foreground">
											{label}
										</span>
									</span>
								))}
						</div>
						<div
							className={`grid min-h-0 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(20rem,0.9fr)] ${(report?.total ?? 0) > 6 ? "flex-1" : ""}`}
						>
							<div className="flex min-h-0 min-w-0 flex-col rounded-[var(--bf-radius-surface)] border border-border">
								<div className="grid grid-cols-[auto_minmax(0,1fr)_auto] gap-3 border-b px-4 py-3 text-xs font-medium text-muted-foreground">
									<span>Select</span>
									<span>
										Workflow · transition · identity risk
									</span>
									<span>Readiness</span>
								</div>
								<div className="max-h-[min(48vh,34rem)] min-h-0 overflow-auto">
									{items.map((item) => (
										<div
											key={item.workflow_id}
											className={`grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 border-b px-4 py-3 last:border-b-0 ${detail?.workflow_id === item.workflow_id ? "bg-muted/50" : ""}`}
										>
											<Checkbox
												aria-label={`Select ${item.workflow_name}`}
												checked={
													!!selected[item.workflow_id]
												}
												disabled={
													!reviewable.has(item.status)
												}
												onCheckedChange={(checked) => {
													select(
														item,
														checked === true,
													);
													if (checked)
														setDetailId(
															item.workflow_id,
														);
												}}
											/>
											<div className="min-w-0">
												<Button
													variant="link"
													aria-label={`Review ${item.workflow_name}`}
													className="h-auto min-h-0 max-w-full justify-start px-0 py-0 text-left font-medium"
													onClick={(event) => {
														reviewTriggerRef.current =
															event.currentTarget;
														setDetailId(
															item.workflow_id,
														);
														if (!desktop)
															setSheetOpen(true);
													}}
												>
													<span className="truncate">
														{item.workflow_name}
													</span>
												</Button>
												<p className="mt-1 text-xs text-muted-foreground">
													{item.current_permission_mode ===
													"full"
														? "Full Access"
														: "Declared Permissions"}{" "}
													→ Declared Permissions ·{" "}
													{item.identity
														?.broad_power_warning
														? "Broad identity"
														: item.identity?.shared
															? "Shared identity"
															: "Dedicated identity"}
												</p>
												{item.error && (
													<p className="mt-1 text-xs text-destructive">
														{item.error}
													</p>
												)}
												{["stale", "blocked"].includes(
													item.status,
												) && (
													<Link
														className="mt-1 inline-block text-xs text-primary underline"
														to={`/workflows/convert?workflows=${encodeURIComponent(item.workflow_id)}`}
													>
														Analyze this workflow
														again
													</Link>
												)}
											</div>
											<Badge
												variant={
													item.status === "ready"
														? "secondary"
														: "outline"
												}
											>
												{statusLabel(item.status)}
											</Badge>
										</div>
									))}
								</div>
								{report && (
									<PaginationFooter
										aria-label="Report pages"
										className="shrink-0 px-4"
										summary={
											desktop
												? `${report.total === 0 ? 0 : report.offset + 1}–${Math.min(report.offset + report.limit, report.total)} of ${report.total} workflows`
												: `${report.total === 0 ? 0 : report.offset + 1}–${Math.min(report.offset + report.limit, report.total)} / ${report.total}`
										}
										previousDisabled={report.offset === 0}
										nextDisabled={
											report.offset + report.limit >=
											report.total
										}
										onPrevious={() =>
											void changePage(
												Math.max(
													0,
													report.offset -
														report.limit,
												),
											)
										}
										onNext={() =>
											void changePage(
												report.offset + report.limit,
											)
										}
									/>
								)}
							</div>
							<aside
								aria-label="Workflow detail"
								className="hidden max-h-[min(48vh,34rem)] min-h-0 overflow-auto rounded-[var(--bf-radius-surface)] border border-border p-5 lg:block"
							>
								{detail ? (
									<>
										<h3 className="mb-4 font-medium">
											{detail.workflow_name}
										</h3>
										{inspector}
									</>
								) : (
									<p className="text-sm text-muted-foreground">
										Select a workflow to inspect its
										proposal.
									</p>
								)}
							</aside>
						</div>
					</div>
				)}
				{stage === "apply" && <OperationStage operation={operation} />}
				{!desktop && (
					<Sheet
						open={sheetOpen}
						onOpenChange={(open) => {
							setSheetOpen(open);
							if (!open)
								requestAnimationFrame(() =>
									reviewTriggerRef.current?.focus(),
								);
						}}
					>
						<SheetContent className="flex w-full flex-col overflow-hidden sm:max-w-xl">
							<SheetHeader className="border-b">
								<SheetTitle>Workflow details</SheetTitle>
								<SheetDescription>
									{detail?.workflow_name}
								</SheetDescription>
							</SheetHeader>
							<div className="overflow-auto p-5">{inspector}</div>
						</SheetContent>
					</Sheet>
				)}
				<AlertDialog open={confirmApply} onOpenChange={setConfirmApply}>
					<AlertDialogContent>
						<AlertDialogHeader>
							<AlertDialogTitle>
								Confirm reviewed changes
							</AlertDialogTitle>
							<AlertDialogDescription>
								Apply declared-permission proposals to{" "}
								{entries.length} selected workflow
								{entries.length === 1 ? "" : "s"}. This uses
								exact reviewed fingerprints and does not run
								workflows.
							</AlertDialogDescription>
						</AlertDialogHeader>
						<ul className="max-h-40 space-y-1 overflow-auto text-sm">
							{entries.map(([id, value]) => (
								<li key={id}>
									{value.workflow_name || id} ·{" "}
									{value.identity_strategy === "dedicated"
										? "Dedicated identity"
										: "Keep current identity"}
								</li>
							))}
						</ul>
						{solutionRequired && (
							<label className="flex items-start gap-2 text-sm">
								<Checkbox
									checked={solutionAck}
									onCheckedChange={(value) =>
										setSolutionAck(value === true)
									}
								/>
								I reviewed selected Solution declarations.
							</label>
						)}
						<AlertDialogFooter>
							<AlertDialogCancel>
								Keep reviewing
							</AlertDialogCancel>
							<AlertDialogAction
								disabled={
									!selectionReady ||
									(solutionRequired && !solutionAck) ||
									mutating
								}
								onClick={() => void apply()}
							>
								Apply reviewed changes
							</AlertDialogAction>
						</AlertDialogFooter>
					</AlertDialogContent>
				</AlertDialog>
				<AlertDialog
					open={confirmCutover}
					onOpenChange={setConfirmCutover}
				>
					<AlertDialogContent>
						<AlertDialogHeader>
							<AlertDialogTitle>
								Confirm platform cutover
							</AlertDialogTitle>
							<AlertDialogDescription>
								Change the platform default to Declared
								Permissions. The backend rechecks readiness
								before making this change.
							</AlertDialogDescription>
						</AlertDialogHeader>
						<p className="text-sm text-muted-foreground">
							{report?.summary.remaining_full ?? 0} Full Access
							workflows remaining.
						</p>
						<label className="flex items-start gap-2 text-sm">
							<Checkbox
								checked={exceptionsAck}
								onCheckedChange={(value) =>
									setExceptionsAck(value === true)
								}
							/>
							I reviewed the remaining exceptions.
						</label>
						<AlertDialogFooter>
							<AlertDialogCancel>
								Keep reviewing
							</AlertDialogCancel>
							<AlertDialogAction
								variant="destructive"
								disabled={
									report?.summary.cutover_ready !== true ||
									!exceptionsAck ||
									mutating
								}
								onClick={() => void cutover()}
							>
								Change platform default to Declared Permissions
							</AlertDialogAction>
						</AlertDialogFooter>
					</AlertDialogContent>
				</AlertDialog>
			</ConversionWizardFrame>
		</PageWorkspace>
	);
}

function Scope({
	ids,
	selectionLost,
}: {
	ids: string[];
	selectionLost: boolean;
}) {
	return (
		<section className="space-y-5" aria-labelledby="conversion-scope">
			<div className="max-w-2xl space-y-5">
				<div>
					<h2 id="conversion-scope" className="text-lg font-semibold">
						Review your selection
					</h2>
					<p className="mt-1 text-sm text-muted-foreground">
						{selectionLost
							? "This selection is no longer available. Return to Workflows, choose workflows again, then start a new analysis."
							: ids.length
								? `${ids.length} selected workflow${ids.length === 1 ? "" : "s"} will be frozen into this analysis.`
								: "All workflows you are authorized to review will be analyzed."}
					</p>
				</div>
				<div className="grid gap-3 sm:grid-cols-2">
					<div className="rounded-[var(--bf-radius-surface)] border p-4">
						<p className="text-sm font-medium">
							{ids.length
								? "Selected workflows"
								: "All authorized workflows"}
						</p>
						<p className="mt-1 text-sm text-muted-foreground">
							{ids.length
								? `${ids.length} selected`
								: "Workflows, tools, data providers and services"}
						</p>
					</div>
					<div className="rounded-[var(--bf-radius-surface)] border p-4">
						<p className="text-sm font-medium">
							30-day observation
						</p>
						<p className="mt-1 text-sm text-muted-foreground">
							Uses Python source and operations recorded over the
							last 30 days.
						</p>
					</div>
				</div>
				<Alert>
					<ShieldCheck className="size-4" />
					<AlertDescription>
						Analysis is read-only. Applying later never runs a
						workflow or silently adds organization reach.
					</AlertDescription>
				</Alert>
			</div>
		</section>
	);
}
function ProgressStage({
	job,
}: {
	job: ReturnType<typeof useWorkflowConversionJob>["job"];
}) {
	return (
		<section className="space-y-5" aria-labelledby="conversion-analysis">
			<div className="space-y-5">
				<ConversionJobProgress
					job={job}
					kind="analysis"
					titleId="conversion-analysis"
				/>
				{job &&
					!ACTIVE.has(job.status) &&
					job.status !== "succeeded" && (
						<Alert variant="destructive">
							<AlertDescription>
								{job.error?.message ||
									"This analysis stopped. Its saved progress is available in Platform Jobs."}
							</AlertDescription>
						</Alert>
					)}
			</div>
		</section>
	);
}
function Inspector({
	item,
	selection,
	update,
}: {
	item: Item;
	selection?: Selection;
	update: (change: Partial<Selection>) => void;
}) {
	const reviewedStrategy = item.review?.identity_strategy;
	const applied =
		reviewedStrategy === "dedicated" || reviewedStrategy === "keep_current"
			? reviewedStrategy
			: null;
	return (
		<div className="space-y-5">
			<div>
				<Badge
					variant={item.status === "ready" ? "secondary" : "outline"}
				>
					{statusLabel(item.status)}
				</Badge>
				<p className="mt-2 text-sm">
					{item.current_permission_mode === "full"
						? "Full Access"
						: "Declared Permissions"}{" "}
					→ Declared Permissions
				</p>
				<p className="mt-1 text-sm text-muted-foreground">
					{(item.proposed_permissions ?? []).join(", ") ||
						"No SDK permissions found"}
				</p>
			</div>
			<div className="border-t pt-4">
				<h4 className="font-medium">Identity risk</h4>
				<p className="mt-2 text-sm">
					{item.identity?.name ?? "Identity unavailable"}
				</p>
				<p className="text-sm text-muted-foreground">
					{item.identity?.shared
						? "Shared identity"
						: "Dedicated identity"}
					{item.identity?.broad_power_warning
						? " · Broad authority"
						: ""}
				</p>
				<p className="mt-2 text-sm text-muted-foreground">
					Proposed dedicated identity: User base role in this
					workflow’s organization, with no extra roles.
				</p>
				{applied && (
					<p className="mt-2 text-sm text-muted-foreground">
						Applied review:{" "}
						{applied === "dedicated"
							? "Dedicated identity"
							: "Keep current identity"}
					</p>
				)}
				{item.identity?.organization_id && (
					<details className="mt-2 text-sm text-muted-foreground">
						<summary>Identity details</summary>
						<p className="mt-2 break-all">
							Organization: {item.identity.organization_id}
						</p>
					</details>
				)}
			</div>
			<details className="border-t pt-4 text-sm">
				<summary className="cursor-pointer font-medium">
					Permission inputs
				</summary>
				<p className="mt-2 text-muted-foreground">
					Source:{" "}
					{(item.source_permissions ?? []).join(", ") || "None"}
				</p>
				<p className="mt-1 text-muted-foreground">
					Observed:{" "}
					{(item.observed_permissions ?? []).join(", ") || "None"}
				</p>
				{(item.warnings ?? []).map((warning) => (
					<p key={warning} className="mt-1 text-[var(--bf-warning)]">
						{warning}
					</p>
				))}
			</details>
			{selection && (
				<fieldset className="space-y-3 border-t pt-4">
					<legend className="font-medium">Identity strategy</legend>
					<RadioGroup
						value={selection.identity_strategy}
						onValueChange={(value) =>
							update({
								identity_strategy: value as IdentityStrategy,
								acknowledged_broad_identity:
									value === "dedicated"
										? false
										: selection.acknowledged_broad_identity,
							})
						}
					>
						<label className="flex items-center gap-2 text-sm">
							<RadioGroupItem value="dedicated" />
							Use a dedicated identity
						</label>
						<label className="flex items-center gap-2 text-sm">
							<RadioGroupItem value="keep_current" />
							Keep the current identity
						</label>
					</RadioGroup>
					{selection.identity_strategy === "keep_current" && (
						<label className="flex items-start gap-2 text-sm">
							<Checkbox
								checked={selection.acknowledged_broad_identity}
								onCheckedChange={(value) =>
									update({
										acknowledged_broad_identity:
											value === true,
									})
								}
							/>
							I acknowledge the existing authority.
						</label>
					)}
					{(item.unknowns ?? []).length > 0 && (
						<label className="flex items-start gap-2 text-sm">
							<Checkbox
								checked={selection.acknowledged_unknowns}
								onCheckedChange={(value) =>
									update({
										acknowledged_unknowns: value === true,
									})
								}
							/>
							I acknowledge unresolved code paths.
						</label>
					)}
				</fieldset>
			)}
			<Evidence item={item} />
		</div>
	);
}
function OperationStage({
	operation,
}: {
	operation: ReturnType<typeof useWorkflowConversionOperation>;
}) {
	const current = operation.job;
	const completed = current?.result?.completed;
	return (
		<section className="space-y-5" aria-labelledby="conversion-apply">
			<div className="space-y-5">
				<ConversionJobProgress
					job={current}
					kind={
						current?.job_type === "workflow.permissions.cutover"
							? "cutover"
							: "apply"
					}
					titleId="conversion-apply"
				/>
				{operation.error && (
					<Alert variant="destructive">
						<AlertDescription>{operation.error}</AlertDescription>
					</Alert>
				)}
				{Array.isArray(completed) && (
					<p className="text-sm text-muted-foreground">
						{completed.length} workflow change
						{completed.length === 1 ? "" : "s"} saved.
					</p>
				)}
				{current?.status === "succeeded" && (
					<Alert>
						<CheckCircle2 className="size-4" />
						<AlertDescription>
							The requested change completed. Review each workflow
							before running it; this conversion did not execute a
							workflow.
						</AlertDescription>
					</Alert>
				)}
				{current &&
					["failed", "cancelled", "requires_action"].includes(
						current.status,
					) && (
						<Alert variant="destructive">
							<AlertDescription>
								{current.error?.message} The requested change{" "}
								{statusLabel(current.status)}. The durable job
								record keeps any partial outcomes for review.
							</AlertDescription>
						</Alert>
					)}
			</div>
		</section>
	);
}
