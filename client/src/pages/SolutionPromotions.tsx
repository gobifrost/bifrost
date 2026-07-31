import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	AlertTriangle,
	ArrowRight,
	Building2,
	CheckCircle2,
	FileDiff,
	Globe2,
	Loader2,
	ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";

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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { MultiCombobox } from "@/components/ui/multi-combobox";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import { useUsersFiltered } from "@/hooks/useUsers";
import {
	listPromotionReviews,
	promoteSolution,
	type PromotionReview,
	type PromotionTargetRequest,
} from "@/services/solutionPromotions";
import { cn } from "@/lib/utils";

function formatBytes(bytes: number | null | undefined): string {
	if (bytes == null) return "—";
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ReviewQueueItem({
	review,
	selected,
	onSelect,
}: {
	review: PromotionReview;
	selected: boolean;
	onSelect: () => void;
}) {
	const needsApprovals =
		(review.unresolved_roles?.length ?? 0) +
		(review.connection_names?.length ?? 0);
	return (
		<button
			type="button"
			className={cn(
				"w-full border-b px-4 py-3 text-left transition-colors hover:bg-accent/60",
				selected && "bg-accent",
			)}
			onClick={onSelect}
		>
			<div className="flex items-start justify-between gap-3">
				<div className="min-w-0">
					<p className="truncate text-sm font-medium">{review.name}</p>
					<p className="truncate text-xs text-muted-foreground">
						{review.slug}
					</p>
				</div>
				{review.ready ? (
					<Badge
						variant="outline"
						className="border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
					>
						Ready
					</Badge>
				) : (
					<Badge variant="destructive">Blocked</Badge>
				)}
			</div>
			<div className="mt-2 flex items-center gap-2 text-[11px] text-muted-foreground">
				<span>{review.changed_paths?.length ?? 0} changed files</span>
				{needsApprovals > 0 ? <span>· {needsApprovals} approvals</span> : null}
			</div>
		</button>
	);
}

function PromotionReviewWorkspace({ review }: { review: PromotionReview }) {
	const queryClient = useQueryClient();
	const blockers = review.blockers ?? [];
	const changedPaths = review.changed_paths ?? [];
	const connectionNames = review.connection_names ?? [];
	const entityCountsByType = review.entity_counts ?? {};
	const globalConfigKeys =
		review.config_keys_requiring_reentry_for_global ?? [];
	const unresolvedRoles = review.unresolved_roles ?? [];
	const [target, setTarget] = useState<"company" | "global">("company");
	const [approveRoleCreation, setApproveRoleCreation] = useState(false);
	const [approvedConnections, setApprovedConnections] = useState<string[]>([]);
	const [allowGlobalRepoAccess, setAllowGlobalRepoAccess] = useState(false);
	const [roleAssignments, setRoleAssignments] = useState<
		Record<string, string[]>
	>({});
	const [confirmOpen, setConfirmOpen] = useState(false);
	const { data: users, isLoading: usersLoading } = useUsersFiltered(
		review.organization_id,
	);
	const userOptions = (users ?? [])
		.filter((user) => user.is_active)
		.map((user) => ({
			value: user.id,
			label: user.name || user.email,
			description: user.name ? user.email : undefined,
		}));
	const missingConnectionApprovals = connectionNames.filter(
		(name) => !approvedConnections.includes(name),
	);
	const approvalsComplete =
		(unresolvedRoles.length === 0 || approveRoleCreation) &&
		missingConnectionApprovals.length === 0;
	const canPromote = review.ready && approvalsComplete;

	const promoteMutation = useMutation({
		mutationFn: (request: PromotionTargetRequest) =>
			promoteSolution(review.solution_id, request),
		onSuccess: (result) => {
			setConfirmOpen(false);
			toast.success(
				`${review.name} promoted to ${
					result.target === "company" ? "Company" : "Global"
				}`,
			);
			queryClient.invalidateQueries({ queryKey: ["solution-promotions"] });
			queryClient.invalidateQueries({ queryKey: ["solutions"] });
		},
		onError: (error: Error) => {
			setConfirmOpen(false);
			toast.error(error.message);
		},
	});

	const entityCounts = Object.entries(entityCountsByType).flatMap(
		([name, count]) =>
			typeof count === "number" && count > 0
				? ([[name, count]] as const)
				: [],
	);
	const targetLabel = target === "company" ? "Company" : "Global";

	function submitPromotion() {
		promoteMutation.mutate({
			target,
			approve_role_creation: approveRoleCreation,
			approved_connection_names: approvedConnections,
			allow_global_repo_access: allowGlobalRepoAccess,
			role_user_assignments: roleAssignments,
		});
	}

	return (
		<div className="flex min-h-0 flex-1 flex-col">
			<header className="flex flex-wrap items-start justify-between gap-3 border-b px-5 py-4">
				<div>
					<div className="flex flex-wrap items-center gap-2">
						<h2 className="text-lg font-semibold">{review.name}</h2>
						<Badge variant="outline">Private</Badge>
						{review.ready ? (
							<Badge className="gap-1 bg-emerald-600 text-white">
								<CheckCircle2 className="h-3 w-3" />
								Build verified
							</Badge>
						) : (
							<Badge variant="destructive">Review blocked</Badge>
						)}
					</div>
					<p className="mt-1 text-sm text-muted-foreground">
						Reviewing pinned revision{" "}
						<code className="text-foreground">
							{review.pinned_revision_id?.slice(0, 8) ?? "missing"}
						</code>
					</p>
				</div>
				<div className="text-right text-xs text-muted-foreground">
					<p>Requested {review.requested_at ? new Date(review.requested_at).toLocaleString() : "—"}</p>
					<p className="font-mono">{formatBytes(review.source_size_bytes)}</p>
				</div>
			</header>

			<div className="min-h-0 flex-1 overflow-auto">
				{blockers.length > 0 ? (
					<section className="border-b bg-destructive/10 px-5 py-4">
						<div className="flex items-center gap-2 text-sm font-medium text-destructive">
							<AlertTriangle className="h-4 w-4" />
							Resolve before promotion
						</div>
						<ul className="mt-2 list-disc space-y-1 pl-6 text-sm text-destructive">
							{blockers.map((blocker) => (
								<li key={blocker}>{blocker}</li>
							))}
						</ul>
					</section>
				) : null}

				<div className="grid xl:grid-cols-[minmax(0,1fr)_380px]">
					<div className="min-w-0 border-b xl:border-b-0 xl:border-r">
						<section className="border-b px-5 py-4">
							<div className="mb-3 flex items-center justify-between gap-3">
								<h3 className="flex items-center gap-2 text-sm font-semibold">
									<FileDiff className="h-4 w-4" />
									Source review
								</h3>
								<div className="flex gap-2 text-xs">
									<span className={review.build_status === "succeeded" ? "text-emerald-600" : "text-muted-foreground"}>
										Build {review.build_status ?? "not required"}
									</span>
									<span className={review.deploy_status === "succeeded" ? "text-emerald-600" : "text-muted-foreground"}>
										Deploy {review.deploy_status ?? "unknown"}
									</span>
								</div>
							</div>
							<div className="flex flex-wrap gap-2">
								{entityCounts.length > 0 ? (
									entityCounts.map(([name, count]) => (
										<Badge key={name} variant="secondary" className="capitalize">
											{name.replaceAll("_", " ")} {count}
										</Badge>
									))
								) : (
									<span className="text-sm text-muted-foreground">No managed entities detected.</span>
								)}
							</div>
							<dl className="mt-4 grid gap-2 text-xs sm:grid-cols-[130px_1fr]">
								<dt className="text-muted-foreground">Source SHA-256</dt>
								<dd className="truncate font-mono" title={review.source_sha256 ?? undefined}>
									{review.source_sha256 ?? "—"}
								</dd>
								<dt className="text-muted-foreground">Current / deployed</dt>
								<dd className="font-mono">
									{review.current_revision_id?.slice(0, 8) ?? "—"} /{" "}
									{review.deployed_revision_id?.slice(0, 8) ?? "—"}
								</dd>
							</dl>
						</section>

						<section className="px-5 py-4">
							<h3 className="mb-3 text-sm font-semibold">
								Changed paths ({changedPaths.length})
							</h3>
							{changedPaths.length > 0 ? (
								<div className="max-h-72 overflow-auto rounded-lg bg-muted/35 py-1 ring-1 ring-foreground/5">
									{changedPaths.map((path) => (
										<div key={path} className="border-b px-3 py-1.5 font-mono text-xs last:border-b-0">
											{path}
										</div>
									))}
								</div>
							) : (
								<p className="text-sm text-muted-foreground">
									Initial promotion has no prior deployed revision to compare.
								</p>
							)}
						</section>
					</div>

					<aside className="min-w-0">
						<section className="border-b px-5 py-4">
							<h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
								<ShieldCheck className="h-4 w-4" />
								Target scope
							</h3>
							<RadioGroup
								value={target}
								onValueChange={(value) =>
									setTarget(value as "company" | "global")
								}
								className="grid gap-2"
							>
								<label className={cn("flex cursor-pointer gap-3 rounded-lg p-3 ring-1 ring-foreground/10", target === "company" && "bg-primary/10 ring-primary/30")}>
									<RadioGroupItem value="company" className="mt-0.5" />
									<span>
										<span className="flex items-center gap-2 text-sm font-medium">
											<Building2 className="h-3.5 w-3.5" />
											Company
										</span>
										<span className="mt-0.5 block text-xs text-muted-foreground">
											Share inside the current organization.
										</span>
									</span>
								</label>
								<label className={cn("flex cursor-pointer gap-3 rounded-lg p-3 ring-1 ring-foreground/10", target === "global" && "bg-primary/10 ring-primary/30")}>
									<RadioGroupItem value="global" className="mt-0.5" />
									<span>
										<span className="flex items-center gap-2 text-sm font-medium">
											<Globe2 className="h-3.5 w-3.5" />
											Global
										</span>
										<span className="mt-0.5 block text-xs text-muted-foreground">
											Make available across every organization.
										</span>
									</span>
								</label>
							</RadioGroup>
							{target === "global" &&
							globalConfigKeys.length > 0 ? (
								<div className="mt-3 rounded-lg bg-amber-500/10 p-3 text-xs text-amber-800 ring-1 ring-amber-500/20 dark:text-amber-200">
									Re-enter after promotion:{" "}
									{globalConfigKeys.join(", ")}
								</div>
							) : null}
						</section>

						{unresolvedRoles.length > 0 ? (
							<section className="border-b px-5 py-4">
								<label className="flex items-start gap-3">
									<Checkbox
										checked={approveRoleCreation}
										onCheckedChange={(checked) =>
											setApproveRoleCreation(checked === true)
										}
										aria-label="Approve role creation"
									/>
									<span>
										<span className="block text-sm font-medium">
											Approve role creation
										</span>
										<span className="block text-xs text-muted-foreground">
											{unresolvedRoles.join(", ")}
										</span>
									</span>
								</label>
								{approveRoleCreation ? (
									<div className="mt-4 space-y-3">
										{unresolvedRoles.map((role) => (
											<div key={role}>
												<label className="mb-1.5 block text-xs font-medium">
													Assign users to {role}
												</label>
												<MultiCombobox
													options={userOptions}
													value={roleAssignments[role] ?? []}
													onValueChange={(values) =>
														setRoleAssignments((current) => ({
															...current,
															[role]: values,
														}))
													}
													placeholder="No users assigned"
													searchPlaceholder="Find a user"
													isLoading={usersLoading}
													maxDisplayedItems={2}
												/>
											</div>
										))}
									</div>
								) : null}
							</section>
						) : null}

						{connectionNames.length > 0 ? (
							<section className="border-b px-5 py-4">
								<h3 className="mb-2 text-sm font-semibold">
									Connection grants
								</h3>
								<div className="space-y-2">
									{connectionNames.map((name) => (
										<label key={name} className="flex items-center gap-3 text-sm">
											<Checkbox
												checked={approvedConnections.includes(name)}
												onCheckedChange={(checked) =>
													setApprovedConnections((current) =>
														checked === true
															? [...new Set([...current, name])]
															: current.filter((item) => item !== name),
													)
												}
											/>
											{name}
										</label>
									))}
								</div>
							</section>
						) : null}

						<section className="px-5 py-4">
							<label className="flex items-start gap-3">
								<Checkbox
									checked={allowGlobalRepoAccess}
									onCheckedChange={(checked) =>
										setAllowGlobalRepoAccess(checked === true)
									}
								/>
								<span>
									<span className="block text-sm font-medium">
										Allow shared repository imports
									</span>
									<span className="block text-xs text-muted-foreground">
										Keep off unless this reviewed source intentionally imports
										global code.
									</span>
								</span>
							</label>
						</section>
					</aside>
				</div>
			</div>

			<footer className="flex flex-wrap items-center justify-between gap-3 border-t px-5 py-3">
				<p className="text-xs text-muted-foreground">
					Promotion replays this pinned revision. Later private edits are not
					included.
				</p>
				<Button
					disabled={!canPromote || promoteMutation.isPending}
					onClick={() => setConfirmOpen(true)}
				>
					{promoteMutation.isPending ? (
						<Loader2 className="h-4 w-4 animate-spin" />
					) : (
						<ArrowRight className="h-4 w-4" />
					)}
					Promote to {targetLabel}
				</Button>
			</footer>

			<AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Promote {review.name} to {targetLabel}?
						</AlertDialogTitle>
						<AlertDialogDescription>
							This replays pinned revision{" "}
							{review.pinned_revision_id?.slice(0, 8)}, applies the reviewed
							role and connection grants, and ends private-owner bypass.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={submitPromotion}>
							Promote Solution
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}

export function SolutionPromotions() {
	const [selectedId, setSelectedId] = useState<string | null>(null);
	const reviewsQuery = useQuery({
		queryKey: ["solution-promotions"],
		queryFn: ({ signal }) => listPromotionReviews({ signal }),
	});
	const reviews = useMemo(() => reviewsQuery.data ?? [], [reviewsQuery.data]);
	const selected =
		reviews.find((review) => review.solution_id === selectedId) ??
		reviews[0] ??
		null;

	return (
		<div className="flex h-full min-h-0 flex-col">
			<header className="border-b px-6 py-5">
				<div className="flex flex-wrap items-end justify-between gap-3">
					<div>
						<h1 className="text-2xl font-semibold tracking-tight">
							Promotion review
						</h1>
						<p className="mt-1 max-w-2xl text-sm text-muted-foreground">
							Approve the exact green revision, scope, roles, and
							connections before a private build becomes shared.
						</p>
					</div>
					<Badge variant="secondary">
						{reviews.length} pending
					</Badge>
				</div>
			</header>

			{reviewsQuery.isLoading ? (
				<div className="grid min-h-0 flex-1 grid-cols-[300px_1fr]">
					<div className="space-y-2 border-r p-3">
						<Skeleton className="h-20 w-full" />
						<Skeleton className="h-20 w-full" />
					</div>
					<Skeleton className="m-5 h-80" />
				</div>
			) : reviewsQuery.isError ? (
				<div className="flex flex-1 items-center justify-center p-8 text-center">
					<div>
						<AlertTriangle className="mx-auto h-7 w-7 text-destructive" />
						<p className="mt-2 text-sm font-medium">
							Could not load promotion requests
						</p>
						<p className="mt-1 text-sm text-muted-foreground">
							{(reviewsQuery.error as Error).message}
						</p>
						<Button
							variant="outline"
							size="sm"
							className="mt-3"
							onClick={() => reviewsQuery.refetch()}
						>
							Try again
						</Button>
					</div>
				</div>
			) : reviews.length === 0 ? (
				<div className="flex flex-1 items-center justify-center p-8 text-center">
					<div>
						<ShieldCheck className="mx-auto h-8 w-8 text-emerald-600" />
						<h2 className="mt-3 text-base font-semibold">Review queue is clear</h2>
						<p className="mt-1 max-w-sm text-sm text-muted-foreground">
							Private builders appear here after their Source and Preview
							match and they request promotion.
						</p>
					</div>
				</div>
			) : (
				<div className="flex min-h-0 flex-1 flex-col md:flex-row">
					<aside className="max-h-64 shrink-0 overflow-auto border-b md:max-h-none md:w-[300px] md:border-b-0 md:border-r">
						{reviews.map((review) => (
							<ReviewQueueItem
								key={review.solution_id}
								review={review}
								selected={selected?.solution_id === review.solution_id}
								onSelect={() => setSelectedId(review.solution_id)}
							/>
						))}
					</aside>
					{selected ? (
						<PromotionReviewWorkspace
							key={selected.solution_id}
							review={selected}
						/>
					) : null}
				</div>
			)}
		</div>
	);
}
