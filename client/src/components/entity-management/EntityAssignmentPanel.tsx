import { useMemo, useRef, useState } from "react";
import {
	ArrowRight,
	Building2,
	Shield,
	UserPlus,
	UsersRound,
} from "lucide-react";

import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useOrganizationName } from "@/hooks/useOrganizationName";
import { AccessLevelSelect } from "@/components/access/AccessLevelSelect";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";

import { isEntityManaged, formatEntityAccess } from "./types";
import type { EntityWithScope, Organization, Role } from "./types";

const NO_CHANGE = "__no_change__";
const ACCESS_AUTHENTICATED = "authenticated";
const ACCESS_ROLE_BASED = "role_based";
const ROLE_ACTION_ADD = "add";
const ROLE_ACTION_CLEAR = "clear";

interface EntityAssignmentPanelProps {
	hideInstructions?: boolean;
	entities: EntityWithScope[];
	selectedIds: Set<string>;
	organizations: Organization[];
	roles: Role[];
	disabled: boolean;
	onOrganization: (
		ids: string[],
		organizationId: string | null,
	) => Promise<void>;
	onAccess: (
		ids: string[],
		change: {
			accessLevel?: string;
			addRoleId?: string;
			clearRoles?: boolean;
		},
	) => Promise<void>;
}

type ChangeDraft = {
	organizationId: string | null | typeof NO_CHANGE;
	accessLevel: string;
	roleAction: string;
	roleId: string;
};

export function EntityAssignmentPanel({
	entities,
	hideInstructions = false,
	selectedIds,
	organizations,
	roles,
	disabled,
	onOrganization,
	onAccess,
}: EntityAssignmentPanelProps) {
	const getOrgName = useOrganizationName(organizations);
	const [draft, setDraft] = useState<ChangeDraft>({
		organizationId: NO_CHANGE,
		accessLevel: NO_CHANGE,
		roleAction: NO_CHANGE,
		roleId: NO_CHANGE,
	});
	const [submitSnapshot, setSubmitSnapshot] = useState<
		EntityWithScope[] | null
	>(null);
	const [pending, setPending] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const submitting = useRef(false);

	const selected = useMemo(
		() =>
			entities.filter(
				(entity) =>
					selectedIds.has(entity.key) && !isEntityManaged(entity),
			),
		[entities, selectedIds],
	);
	const reviewEntities = useMemo(
		() => effectiveChangeEntities(submitSnapshot ?? selected, draft),
		[submitSnapshot, selected, draft],
	);
	const hasDraft =
		draft.organizationId !== NO_CHANGE ||
		draft.accessLevel !== NO_CHANGE ||
		draft.roleAction !== NO_CHANGE;
	const hasEffectiveChange =
		effectiveChangeEntities(selected, draft).length > 0;
	const canApply =
		selected.length > 0 &&
		hasDraft &&
		hasEffectiveChange &&
		!disabled &&
		!pending;

	const confirm = async () => {
		if (!canApply || submitting.current) return;
		submitting.current = true;
		setPending(true);
		setError(null);
		const snapshot = selected;
		setSubmitSnapshot(snapshot);
		try {
			const organizationIds = affectedByOrganization(snapshot, draft).map(
				(entity) => entity.key,
			);
			if (draft.organizationId !== NO_CHANGE) {
				if (organizationIds.length) {
					await onOrganization(organizationIds, draft.organizationId);
				}
			}
			const accessChange = buildAccessChange(draft);
			if (accessChange) {
				const accessIds = affectedByAccess(snapshot, draft).map(
					(entity) => entity.key,
				);
				if (accessIds.length) {
					await onAccess(accessIds, accessChange);
				}
			}
			setDraft({
				organizationId: NO_CHANGE,
				accessLevel: NO_CHANGE,
				roleAction: NO_CHANGE,
				roleId: NO_CHANGE,
			});
		} catch (cause) {
			setError(
				cause instanceof Error
					? cause.message
					: "Could not apply changes. Try again.",
			);
		} finally {
			submitting.current = false;
			setPending(false);
			setSubmitSnapshot(null);
		}
	};

	return (
		<section
			aria-label="Entity assignment"
			className="flex min-w-0 flex-col gap-5"
		>
			{!hideInstructions ? (
				<p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
					Choose scope, access, or both. Review the exact changes
					before applying them.
				</p>
			) : null}

			<div className="grid gap-4">
				<div className="grid gap-2">
					<Label className="flex items-center gap-2">
						<Building2 className="size-4 text-muted-foreground" />
						Organization
					</Label>
					<div
						className={
							draft.organizationId === NO_CHANGE
								? "grid gap-2"
								: "grid gap-2 sm:grid-cols-[9rem_minmax(0,1fr)]"
						}
					>
						<Select
							value={
								draft.organizationId === NO_CHANGE
									? NO_CHANGE
									: "change"
							}
							onValueChange={(value) =>
								setDraft((current) => ({
									...current,
									organizationId:
										value === NO_CHANGE ? NO_CHANGE : null,
								}))
							}
							disabled={disabled || pending}
						>
							<SelectTrigger
								aria-label="Organization change mode"
								className="min-h-11 w-full"
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value={NO_CHANGE}>
									No change
								</SelectItem>
								<SelectItem value="change">
									Set scope
								</SelectItem>
							</SelectContent>
						</Select>
						{draft.organizationId !== NO_CHANGE ? (
							<OrganizationSelect
								value={draft.organizationId}
								onChange={(value) =>
									setDraft((current) => ({
										...current,
										organizationId: value ?? null,
									}))
								}
								disabled={disabled || pending}
								showGlobal
								placeholder="Select organization..."
							/>
						) : null}
					</div>
				</div>

				<div className="grid gap-2">
					<Label className="flex items-center gap-2">
						<Shield className="size-4 text-muted-foreground" />
						Access level
					</Label>
					<AccessLevelSelect
						value={draft.accessLevel}
						onValueChange={(value) =>
							setDraft((current) => ({
								...current,
								accessLevel: value,
							}))
						}
						disabled={disabled || pending}
						includeNoChange
						noChangeValue={NO_CHANGE}
						className="min-h-11"
						aria-label="Access level change"
					/>
				</div>

				<div className="grid gap-2">
					<Label className="flex items-center gap-2">
						<UserPlus className="size-4 text-muted-foreground" />
						Roles
					</Label>
					<div
						className={
							draft.roleAction === ROLE_ACTION_ADD
								? "grid gap-2 sm:grid-cols-[9rem_minmax(0,1fr)]"
								: "grid gap-2"
						}
					>
						<Select
							value={draft.roleAction}
							onValueChange={(value) =>
								setDraft((current) => ({
									...current,
									roleAction: value,
									roleId:
										value === ROLE_ACTION_ADD
											? current.roleId
											: NO_CHANGE,
								}))
							}
							disabled={disabled || pending}
						>
							<SelectTrigger
								aria-label="Roles change"
								className="min-h-11 w-full"
							>
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value={NO_CHANGE}>
									No change
								</SelectItem>
								<SelectItem value={ROLE_ACTION_ADD}>
									Add role
								</SelectItem>
								<SelectItem value={ROLE_ACTION_CLEAR}>
									Clear roles
								</SelectItem>
							</SelectContent>
						</Select>
						{draft.roleAction === ROLE_ACTION_ADD ? (
							<Select
								value={
									draft.roleId === NO_CHANGE
										? undefined
										: draft.roleId
								}
								onValueChange={(value) =>
									setDraft((current) => ({
										...current,
										roleAction: ROLE_ACTION_ADD,
										roleId: value,
									}))
								}
								disabled={disabled || pending}
							>
								<SelectTrigger
									aria-label="Role to add"
									className="min-h-11 w-full"
								>
									<SelectValue placeholder="Select role..." />
								</SelectTrigger>
								<SelectContent>
									{roles.map((role) => (
										<SelectItem
											key={role.id}
											value={role.id}
										>
											{role.name}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						) : null}
					</div>
					<p className="flex gap-2 text-xs leading-5 text-muted-foreground">
						<UsersRound className="mt-0.5 size-3.5 shrink-0" />
						Applies to selected resources only. Add role preserves
						existing roles; Clear roles removes all assigned roles.
						Role changes use Role-based access unless you choose
						another access level.
					</p>
				</div>
			</div>

			{reviewEntities.length ? (
				<div className="grid gap-3 border-t border-border pt-5">
					<div>
						<h3 className="text-sm font-semibold">
							Review changes
						</h3>
						<p className="text-sm text-muted-foreground">
							Compare current and new settings for each selected
							resource.
						</p>
					</div>
					<ul aria-label="Entities to update" className="space-y-2">
						{reviewEntities.map((entity) => (
							<li
								key={`${entity.entityType}:${entity.id}`}
								className="grid gap-2 rounded-[var(--bf-radius-surface)] border border-border bg-card p-3 text-sm"
							>
								<div className="flex min-w-0 flex-wrap items-center gap-2">
									<span className="min-w-0 font-medium [overflow-wrap:anywhere]">
										{entity.name}
									</span>
									<span className="text-xs capitalize text-muted-foreground">
										{entity.entityType}
									</span>
								</div>
								<ChangeLine
									label="Scope"
									current={getOrgName(
										entity.organizationId,
										"Unknown organization",
									)}
									proposed={
										!organizationChanges(entity, draft)
											? null
											: getOrgName(
													draft.organizationId,
													"Unknown organization",
												)
									}
								/>
								<ChangeLine
									label="Access"
									current={accessName(entity)}
									proposed={
										proposedAccessLevel(entity, draft) ===
										null
											? null
											: accessTargetName(
													proposedAccessLevel(
														entity,
														draft,
													) ?? "",
												)
									}
								/>
								<ChangeLine
									label="Roles"
									current={roleListName(entity, roles)}
									proposed={
										!roleAssignmentsChange(entity, draft)
											? null
											: proposedRoleName(
													entity,
													draft,
													roles,
												)
									}
								/>
							</li>
						))}
					</ul>
				</div>
			) : null}

			{error ? (
				<p role="alert" className="text-sm text-[var(--bf-danger)]">
					{error}
				</p>
			) : null}

			<div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
				{selected.length > 0 && hasDraft && !hasEffectiveChange ? (
					<p className="self-center text-sm text-muted-foreground">
						No selected resources would change.
					</p>
				) : null}
				<Button
					type="button"
					variant="outline"
					disabled={disabled || pending || !hasDraft}
					onClick={() =>
						setDraft({
							organizationId: NO_CHANGE,
							accessLevel: NO_CHANGE,
							roleAction: NO_CHANGE,
							roleId: NO_CHANGE,
						})
					}
				>
					Reset
				</Button>
				<Button
					type="button"
					disabled={!canApply}
					onClick={() => void confirm()}
				>
					{pending ? "Applying..." : "Apply changes"}
				</Button>
			</div>
		</section>
	);
}

function ChangeLine({
	label,
	current,
	proposed,
}: {
	label: string;
	current: string;
	proposed: string | null;
}) {
	const changed = proposed !== null;
	if (!changed) return null;
	return (
		<p className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-muted-foreground [overflow-wrap:anywhere]">
			<span className="font-medium text-foreground">{label}:</span>
			<span>{current}</span>
			<ArrowRight
				aria-hidden="true"
				className="size-3.5 shrink-0 text-muted-foreground"
			/>
			<span className="font-medium text-primary">{proposed}</span>
		</p>
	);
}

function accessName(entity: EntityWithScope) {
	return formatEntityAccess(entity.accessLevel);
}

function accessTargetName(target: string) {
	if (target === ACCESS_AUTHENTICATED) {
		return "Everyone except external users";
	}
	if (target === ACCESS_ROLE_BASED) {
		return "Restricted to roles";
	}
	if (target === "everyone") {
		return "Everyone";
	}
	return "Unknown access level";
}

function proposedRoleName(
	entity: EntityWithScope,
	draft: ChangeDraft,
	roles: Role[],
) {
	if (draft.roleAction === ROLE_ACTION_CLEAR) {
		return "No roles";
	}
	if (draft.roleAction !== ROLE_ACTION_ADD || draft.roleId === NO_CHANGE) {
		return null;
	}
	return entity.roleIds.includes(draft.roleId)
		? roleListName(entity, roles)
		: roleListName(
				{ ...entity, roleIds: [...entity.roleIds, draft.roleId] },
				roles,
			);
}

function roleNames(roleIds: string[], roles: Role[]) {
	const names = roleIds.map(
		(roleId) => roles.find((role) => role.id === roleId)?.name ?? roleId,
	);
	return names.join(", ");
}

function roleListName(entity: EntityWithScope, roles: Role[]) {
	return entity.roleIds.length
		? roleNames(entity.roleIds, roles)
		: "No roles";
}

function buildAccessChange(draft: ChangeDraft) {
	const change: {
		accessLevel?: string;
		addRoleId?: string;
		clearRoles?: boolean;
	} = {};
	if (draft.accessLevel !== NO_CHANGE) {
		change.accessLevel = draft.accessLevel;
	} else if (
		draft.roleAction === ROLE_ACTION_CLEAR ||
		(draft.roleAction === ROLE_ACTION_ADD && draft.roleId !== NO_CHANGE)
	) {
		change.accessLevel = ACCESS_ROLE_BASED;
	}
	if (draft.roleAction === ROLE_ACTION_ADD && draft.roleId !== NO_CHANGE) {
		change.addRoleId = draft.roleId;
	}
	if (draft.roleAction === ROLE_ACTION_CLEAR) change.clearRoles = true;
	return Object.keys(change).length ? change : null;
}

function effectiveChangeEntities(
	entities: EntityWithScope[],
	draft: ChangeDraft,
) {
	return entities.filter(
		(entity) =>
			organizationChanges(entity, draft) || accessChanges(entity, draft),
	);
}

function affectedByOrganization(
	entities: EntityWithScope[],
	draft: ChangeDraft,
) {
	return entities.filter((entity) => organizationChanges(entity, draft));
}

function affectedByAccess(entities: EntityWithScope[], draft: ChangeDraft) {
	return entities.filter((entity) => accessChanges(entity, draft));
}

function organizationChanges(entity: EntityWithScope, draft: ChangeDraft) {
	return (
		draft.organizationId !== NO_CHANGE &&
		draft.organizationId !== entity.organizationId
	);
}

function accessChanges(entity: EntityWithScope, draft: ChangeDraft) {
	if (proposedAccessLevel(entity, draft) !== null) return true;
	return roleAssignmentsChange(entity, draft);
}

function roleAssignmentsChange(entity: EntityWithScope, draft: ChangeDraft) {
	if (draft.roleAction === ROLE_ACTION_ADD && draft.roleId !== NO_CHANGE) {
		return !entity.roleIds.includes(draft.roleId);
	}
	if (draft.roleAction === ROLE_ACTION_CLEAR) {
		return entity.roleIds.length > 0;
	}
	return false;
}

function proposedAccessLevel(entity: EntityWithScope, draft: ChangeDraft) {
	const nextAccessLevel =
		draft.accessLevel !== NO_CHANGE
			? draft.accessLevel
			: draft.roleAction === ROLE_ACTION_CLEAR ||
				  (draft.roleAction === ROLE_ACTION_ADD &&
						draft.roleId !== NO_CHANGE)
				? ACCESS_ROLE_BASED
				: null;
	return nextAccessLevel !== null && nextAccessLevel !== entity.accessLevel
		? nextAccessLevel
		: null;
}
