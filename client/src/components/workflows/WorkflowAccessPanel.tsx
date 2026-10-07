import { useId, useState, type ReactNode } from "react";
import {
	AlertCircle,
	Building2,
	Globe,
	History,
	Loader2,
	Plus,
	Shield,
	ShieldCheck,
	Workflow as WorkflowIcon,
	type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";

import { ReachChip } from "@/components/access/ReachChip";
import { TestAccessPanel } from "@/components/access/TestAccessPanel";
import { IdentityName } from "@/components/identities/IdentityName";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Label } from "@/components/ui/label";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";
import { useUserAccessMap } from "@/services/access";
import { useAuthorization } from "@/services/authorization";
import {
	identityLabel,
	identityOrganization,
	useCreateIdentity,
	type Identity,
} from "@/services/identities";
import {
	isDefaultIdentityFor,
	rolesGrantableAt,
	useGrantToIdentity,
	useSetWorkflowRunIdentity,
	useWorkflowRecommendedAccess,
	useWorkflowRunIdentities,
	type AssignableRole,
	type RecommendedAccessItem,
	type RecommendedGrant,
} from "@/services/workflowAccess";

type Workflow = components["schemas"]["WorkflowMetadata"];

const ACCESS_MODE_LABELS: Record<Workflow["permission_mode"], string> = {
	full: "Full",
	restricted: "Restricted",
};

const RECOMMENDATION_ICONS: Record<RecommendedAccessItem["kind"], LucideIcon> =
	{
		reach: Building2,
		policy_role: Shield,
		workflow_role: WorkflowIcon,
	};

/** A role to grant, and its name for the confirmation and toast. */
interface PendingGrant {
	grant: RecommendedGrant;
	roleName: string;
}

/** An organization a run reached outside the identity's reach. */
interface ReachTarget {
	organizationId: string;
	organizationName: string;
}

function Section({
	title,
	description,
	children,
}: {
	title: string;
	description?: ReactNode;
	children: ReactNode;
}) {
	const headingId = useId();
	return (
		<section aria-labelledby={headingId} className="space-y-3">
			<div className="space-y-1">
				<h3 id={headingId} className="text-sm font-semibold">
					{title}
				</h3>
				{description && (
					<p className="text-xs text-muted-foreground">
						{description}
					</p>
				)}
			</div>
			{children}
		</section>
	);
}

function ErrorAlert({ message }: { message: string }) {
	return (
		<Alert variant="destructive">
			<AlertCircle aria-hidden="true" className="size-4" />
			<AlertDescription>{message}</AlertDescription>
		</Alert>
	);
}

/** "Applies to all 4 workflows in Contoso that run as the default identity". */
function defaultIdentityReach(identity: Identity): string {
	const place = identityOrganization(identity);
	return identity.workflows_using === 1
		? `Applies to the 1 workflow in ${place} that runs as the default identity`
		: `Applies to all ${identity.workflows_using} workflows in ${place} that run as the default identity`;
}

/** "Contoso · Default", "Global · Default", "Contoso · Custom". */
function identityPlace(identity: Identity): string {
	const kind = identity.identity_kind === "custom" ? "Custom" : "Default";
	return `${identityOrganization(identity)} · ${kind}`;
}

function RecommendationRow({
	item,
	busy,
	onGrant,
	onGrantAccess,
}: {
	item: RecommendedAccessItem;
	busy: boolean;
	onGrant: (pending: PendingGrant) => void;
	onGrantAccess: (target: ReachTarget) => void;
}) {
	const Icon = RECOMMENDATION_ICONS[item.kind];
	const grant = item.grant;
	const reachOrganizationId =
		item.kind === "reach" ? item.organization_id : null;
	return (
		<li className="flex flex-col gap-3 px-3 py-3 sm:flex-row sm:items-center">
			<div className="flex min-w-0 flex-1 items-start gap-3">
				<Icon
					aria-hidden="true"
					className="mt-0.5 size-4 shrink-0 text-primary"
				/>
				<div className="min-w-0 space-y-0.5">
					<p className="text-sm font-medium [overflow-wrap:anywhere]">
						{item.label}
					</p>
					<p className="text-xs text-muted-foreground">
						{item.detail}
					</p>
				</div>
			</div>
			{grant && (
				<Button
					type="button"
					variant="outline"
					className="min-h-11 shrink-0 sm:min-h-9"
					disabled={busy}
					onClick={() => onGrant({ grant, roleName: item.label })}
				>
					Grant to Identity
				</Button>
			)}
			{reachOrganizationId && (
				<Button
					type="button"
					variant="outline"
					className="min-h-11 shrink-0 sm:min-h-9"
					disabled={busy}
					onClick={() =>
						onGrantAccess({
							organizationId: reachOrganizationId,
							organizationName: item.label,
						})
					}
				>
					Grant Access
				</Button>
			)}
		</li>
	);
}

/**
 * Chooses a role for the identity to hold at an organization its runs
 * reached: one of its grantable roles that can be placed there.
 */
function GrantAccessDialog({
	target,
	identity,
	roles,
	onOpenChange,
	onChoose,
}: {
	target: ReachTarget | null;
	identity: Identity | undefined;
	roles: AssignableRole[];
	onOpenChange: (open: boolean) => void;
	onChoose: (pending: PendingGrant) => void;
}) {
	const [roleId, setRoleId] = useState("");
	const role = roles.find((item) => item.id === roleId);
	const options: ComboboxOption[] = roles.map((item) => ({
		value: item.id,
		label: item.name,
		description: item.description ?? undefined,
	}));
	return (
		<Dialog
			open={!!target}
			onOpenChange={(open) => {
				if (!open) setRoleId("");
				onOpenChange(open);
			}}
		>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>
						Grant Access to {target?.organizationName}
					</DialogTitle>
					<DialogDescription>
						{identity &&
							`Choose a role for ${identityLabel(identity)} to hold in ${target?.organizationName}.`}
					</DialogDescription>
				</DialogHeader>
				{roles.length === 0 ? (
					<p className="text-sm text-muted-foreground">
						None of the roles you can grant this identity can be
						placed in {target?.organizationName}.
					</p>
				) : (
					<div className="space-y-2">
						<Label htmlFor="grant-access-role">Role</Label>
						<Combobox
							id="grant-access-role"
							aria-label="Role"
							options={options}
							value={roleId}
							onValueChange={setRoleId}
							placeholder="Choose a role"
							searchPlaceholder="Search roles"
							emptyText="No role found."
						/>
					</div>
				)}
				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						className="min-h-11 sm:min-h-9"
						onClick={() => onOpenChange(false)}
					>
						Cancel
					</Button>
					<Button
						type="button"
						className="min-h-11 sm:min-h-9"
						disabled={!role}
						onClick={() => {
							if (!role || !target) return;
							setRoleId("");
							onChoose({
								grant: {
									role_id: role.id,
									boundaries: [
										{
											kind: "organization",
											organization_id:
												target.organizationId,
										},
									],
								},
								roleName: role.name,
							});
						}}
					>
						Grant
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

/**
 * Who a workflow runs as when no person starts it, the access its runs used
 * that the identity lacks (Recommended Access), and where each kind of run
 * reaches. Changes here apply right away; the dialog's Save covers the rest
 * of the Access tab.
 */
export function WorkflowAccessPanel({ workflow }: { workflow: Workflow }) {
	const workflowOrganizationId = workflow.organization_id ?? null;
	const identitiesQuery = useWorkflowRunIdentities(workflow.id);
	const recommendationsQuery = useWorkflowRecommendedAccess(workflow.id);
	const providerOrganizationId =
		useAuthorization().authorization?.provider_organization_id;
	const setRunIdentity = useSetWorkflowRunIdentity();
	const createIdentity = useCreateIdentity();

	const [identityError, setIdentityError] = useState<string | null>(null);
	const [grantError, setGrantError] = useState<string | null>(null);
	const [confirming, setConfirming] = useState<PendingGrant | null>(null);
	const [reachTarget, setReachTarget] = useState<ReachTarget | null>(null);
	const [creating, setCreating] = useState(false);
	const [granting, setGranting] = useState(false);

	const identities = identitiesQuery.data ?? [];
	const recommendations = recommendationsQuery.data;
	// The server names the identity the workflow runs as now; the dialog's
	// copy of the workflow can be older than a change made here. Nothing acts
	// on an identity until the server has said which.
	const current = recommendations
		? identities.find(
				(identity) => identity.id === recommendations.identity_id,
			)
		: undefined;
	const grants = useGrantToIdentity(current?.id);
	const reachQuery = useUserAccessMap(current?.id);
	const currentLabel = current ? identityLabel(current) : "The identity";

	// A Solution's deployment owns the workflow, so who it runs as stays
	// put; granting the identity access doesn't change the workflow.
	const isSolutionManaged = workflow.is_solution_managed;
	const busy = setRunIdentity.isPending || creating || granting;
	const workflowName = workflow.display_name || workflow.name;

	const runAs = async (identity: Identity) => {
		const next = isDefaultIdentityFor(identity, workflowOrganizationId)
			? null
			: identity.id;
		await setRunIdentity.mutateAsync({
			params: { path: { workflow_id: workflow.id } },
			// Generated as required; false leaves the workflow's roles alone.
			body: { run_identity_id: next, clear_roles: false },
		});
	};

	const handleChoose = async (identityId: string) => {
		const identity = identities.find((item) => item.id === identityId);
		if (
			!identity ||
			!current ||
			identity.id === current.id ||
			busy ||
			isSolutionManaged
		)
			return;
		setIdentityError(null);
		try {
			await runAs(identity);
			toast.success("Identity changed", {
				description: `${workflowName} runs as ${identityLabel(identity)} when no person starts it`,
			});
		} catch (cause) {
			setIdentityError(
				getErrorMessage(
					cause,
					"The workflow's identity could not be changed.",
				),
			);
		}
	};

	const handleCreateDedicated = async () => {
		if (busy || isSolutionManaged) return;
		setConfirming(null);
		setIdentityError(null);
		setCreating(true);
		try {
			const identity = await createIdentity.mutateAsync({
				body: {
					name: `${workflowName} Identity`,
					organization_id: workflowOrganizationId,
				},
			});
			await runAs(identity);
			toast.success("Dedicated identity created", {
				description: `${workflowName} runs as ${identityLabel(identity)} when no person starts it`,
			});
		} catch (cause) {
			setIdentityError(
				getErrorMessage(
					cause,
					"The dedicated identity could not be created.",
				),
			);
		} finally {
			setCreating(false);
		}
	};

	const applyGrant = async ({ grant, roleName }: PendingGrant) => {
		if (!current) return;
		setConfirming(null);
		setGrantError(null);
		setGranting(true);
		try {
			await grants.grant(grant);
			toast.success("Role granted", {
				description: `${identityLabel(current)} now holds ${roleName}`,
			});
		} catch (cause) {
			setGrantError(
				getErrorMessage(cause, "The role could not be granted."),
			);
		} finally {
			setGranting(false);
		}
	};

	const requestGrant = (pending: PendingGrant) => {
		if (!current || busy) return;
		if (current.identity_kind === "custom") void applyGrant(pending);
		else setConfirming(pending);
	};

	const identityOptions: ComboboxOption[] = identities.map((identity) => ({
		value: identity.id,
		label: identity.name,
		description: identityPlace(identity),
		icon: identity.organization_id ? Building2 : Globe,
	}));

	return (
		<div className="space-y-6 border-t border-border/70 pt-6">
			<Section
				title="Runs Unattended As"
				description="The identity this workflow runs as when no person starts it: schedules, events and API keys. Changes here apply right away."
			>
				{identityError && <ErrorAlert message={identityError} />}
				<div className="flex flex-col gap-2 sm:flex-row">
					<div className="min-w-0 flex-1">
						<Label
							htmlFor="workflow-run-identity"
							className="sr-only"
						>
							Runs Unattended As
						</Label>
						<Combobox
							id="workflow-run-identity"
							aria-label="Runs Unattended As"
							options={identityOptions}
							value={current?.id ?? ""}
							onValueChange={(value) => void handleChoose(value)}
							isLoading={identitiesQuery.isLoading}
							showSelectedDescription
							disabled={busy || !current || isSolutionManaged}
							aria-describedby={
								isSolutionManaged
									? "workflow-run-identity-managed"
									: undefined
							}
							placeholder="Choose an identity"
							searchPlaceholder="Search identities"
							emptyText="No identity found."
						/>
					</div>
					<Button
						type="button"
						variant="outline"
						className="min-h-11 sm:min-h-10"
						disabled={busy || isSolutionManaged}
						aria-describedby={
							isSolutionManaged
								? "workflow-run-identity-managed"
								: undefined
						}
						onClick={() => void handleCreateDedicated()}
					>
						{creating ? (
							<Loader2
								aria-hidden="true"
								className="size-4 animate-spin motion-reduce:animate-none"
							/>
						) : (
							<Plus aria-hidden="true" className="size-4" />
						)}
						Create a Dedicated Identity
					</Button>
				</div>
				{isSolutionManaged && (
					<p
						id="workflow-run-identity-managed"
						className="text-xs text-muted-foreground"
					>
						This workflow is managed by a Solution. Re-deploy the
						Solution to change who it runs as.
					</p>
				)}
				<div className="flex flex-wrap items-center gap-2 text-sm">
					<span className="text-muted-foreground">Access Mode</span>
					<Badge variant="secondary">
						{ACCESS_MODE_LABELS[workflow.permission_mode]}
					</Badge>
				</div>
				<p className="text-xs text-muted-foreground">
					The workflow can use everything its identity can. Restricted
					mode, which limits it to what it declares, arrives in a
					later release.
				</p>
			</Section>

			<Section title="Reach Summary">
				<dl className="divide-y divide-border/70 rounded-[var(--bf-radius-surface)] border border-border/70">
					<div className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-center">
						<dt className="w-40 shrink-0 text-sm text-muted-foreground">
							Started by a Person
						</dt>
						<dd className="min-w-0">
							<Badge
								variant="secondary"
								className="bg-[var(--bf-reach-soft)] text-[var(--bf-reach)]"
							>
								That Person's Reach
							</Badge>
						</dd>
					</div>
					<div className="flex flex-col gap-2 px-3 py-3 sm:flex-row sm:items-start">
						<dt className="w-40 shrink-0 text-sm text-muted-foreground sm:pt-1">
							Unattended
						</dt>
						<dd className="min-w-0 flex-1 space-y-2">
							{current && <IdentityName identity={current} />}
							<div className="flex flex-wrap gap-1.5">
								{(reachQuery.data?.reach ?? []).map((place) => (
									<ReachChip
										key={`${place.kind}:${place.organization_id ?? ""}`}
										place={place}
									/>
								))}
							</div>
						</dd>
					</div>
				</dl>
			</Section>

			<Section
				title="Recommended Access"
				description={
					recommendations &&
					`Based on this workflow's runs in the last ${recommendations.window_days} days, ${currentLabel} would also need:`
				}
			>
				{grantError && <ErrorAlert message={grantError} />}
				{recommendations &&
					recommendations.items.length === 0 &&
					(recommendations.observed_runs === 0 ? (
						<EmptyState
							icon={History}
							title="No Runs Observed Yet"
							description="Recommendations appear after the workflow runs."
						/>
					) : (
						<EmptyState
							icon={ShieldCheck}
							title="Nothing Missing"
							description={`${currentLabel} has everything these runs used.`}
						/>
					))}
				{recommendations && recommendations.items.length > 0 && (
					<ul className="divide-y divide-border/70 rounded-[var(--bf-radius-surface)] border border-border/70">
						{recommendations.items.map((item) => (
							<RecommendationRow
								key={`${item.kind}:${item.organization_id ?? ""}:${item.label}`}
								item={item}
								busy={busy}
								onGrant={requestGrant}
								onGrantAccess={setReachTarget}
							/>
						))}
					</ul>
				)}
			</Section>

			{current && (
				<Section
					title="Test Access"
					description={`What ${identityLabel(current)} could do when this workflow runs unattended. Report-only: nothing is recorded or enforced.`}
				>
					<TestAccessPanel
						key={current.id}
						subjectId={current.id}
						defaultWorkflowId={workflow.id}
						defaultOrganizationId={
							workflowOrganizationId ?? "global"
						}
					/>
				</Section>
			)}

			<GrantAccessDialog
				target={reachTarget}
				identity={current}
				roles={
					reachTarget
						? rolesGrantableAt(
								grants.assignableRoles,
								reachTarget.organizationId,
								providerOrganizationId,
							)
						: []
				}
				onOpenChange={(open) => {
					if (!open) setReachTarget(null);
				}}
				onChoose={(pending) => {
					setReachTarget(null);
					requestGrant(pending);
				}}
			/>

			<AlertDialog
				open={!!confirming}
				onOpenChange={(open) => {
					if (!open) setConfirming(null);
				}}
			>
				<AlertDialogContent className="data-[size=default]:sm:max-w-lg">
					<AlertDialogHeader>
						<AlertDialogTitle>
							Grant to the Default Identity?
						</AlertDialogTitle>
						<AlertDialogDescription>
							{current && defaultIdentityReach(current)}.
							{!isSolutionManaged &&
								` To give ${confirming?.roleName} to this workflow alone, create a dedicated identity and grant it there.`}
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel className="min-h-11 sm:min-h-9">
							Cancel
						</AlertDialogCancel>
						{!isSolutionManaged && (
							<Button
								type="button"
								variant="outline"
								className="min-h-11 sm:min-h-9"
								onClick={() => void handleCreateDedicated()}
							>
								Create a Dedicated Identity Instead
							</Button>
						)}
						<AlertDialogAction
							className="min-h-11 sm:min-h-9"
							onClick={() => {
								if (confirming) void applyGrant(confirming);
							}}
						>
							Grant
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
