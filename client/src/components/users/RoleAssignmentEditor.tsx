import { useMemo, useState } from "react";
import {
	Check,
	ChevronsUpDown,
	Shield,
	Users,
	X,
	Building2,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import { Label } from "@/components/ui/label";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";

export type RoleBoundaryKind =
	| "organization"
	| "organization_group"
	| "managed_organizations"
	| "platform";

export interface RoleBoundaryDraft {
	boundary_kind: RoleBoundaryKind;
	organization_id?: string | null;
	organization_group_id?: string | null;
}

export interface RoleAssignmentDraft {
	role_id: string;
	boundaries: RoleBoundaryDraft[];
}

export interface RoleAssignmentSource {
	role_id: string;
	boundaries?: RoleBoundaryDraft[] | null;
}

export interface RoleAssignmentEditorRole {
	id: string;
	name: string;
	description?: string | null;
	key?: string | null;
	is_builtin?: boolean;
	assignable_to_resources?: boolean;
}

export interface RoleAssignmentEditorOrganization {
	id: string;
	name: string;
}

export interface RoleAssignmentEditorOrganizationGroup {
	id: string;
	name: string;
}

export interface RoleAssignmentEditorProps {
	roles: RoleAssignmentEditorRole[];
	value: RoleAssignmentDraft[];
	organizations: RoleAssignmentEditorOrganization[];
	organizationGroups: RoleAssignmentEditorOrganizationGroup[];
	disabled?: boolean;
	isLoading?: boolean;
	defaultBoundary?: RoleBoundaryDraft | null;
	platformAdminRoleIds?: string[];
	onChange: (next: RoleAssignmentDraft[]) => void;
}

type BoundaryMaps = {
	organizations: Map<string, string>;
	organizationGroups: Map<string, string>;
};

export function boundaryKey(boundary: RoleBoundaryDraft): string {
	return [
		boundary.boundary_kind,
		boundary.organization_id ?? "",
		boundary.organization_group_id ?? "",
	].join(":");
}

export function toRoleAssignmentDrafts(
	assignments: RoleAssignmentSource[] | null | undefined,
): RoleAssignmentDraft[] {
	return (assignments ?? []).map((assignment) => ({
		role_id: assignment.role_id,
		boundaries: (assignment.boundaries ?? []).map((boundary) => ({
			boundary_kind: boundary.boundary_kind,
			organization_id: boundary.organization_id,
			organization_group_id: boundary.organization_group_id,
		})),
	}));
}

export function roleAssignmentKey(assignment: RoleAssignmentDraft): string {
	return `${assignment.role_id}:${assignment.boundaries
		.map(boundaryKey)
		.sort()
		.join("|")}`;
}

export function areRoleAssignmentsEqual(
	left: RoleAssignmentDraft[],
	right: RoleAssignmentDraft[],
): boolean {
	return (
		left.map(roleAssignmentKey).sort().join(";") ===
		right.map(roleAssignmentKey).sort().join(";")
	);
}

export function areBoundariesEqual(
	left: RoleBoundaryDraft,
	right: RoleBoundaryDraft,
): boolean {
	return boundaryKey(left) === boundaryKey(right);
}

export function dedupeBoundaries(
	boundaries: RoleBoundaryDraft[],
): RoleBoundaryDraft[] {
	const seen = new Set<string>();
	return boundaries.filter((boundary) => {
		const key = boundaryKey(boundary);
		if (seen.has(key)) return false;
		seen.add(key);
		return true;
	});
}

export function validateBoundaries(
	boundaries: RoleBoundaryDraft[],
	platformLocked = false,
): string | null {
	const unique = dedupeBoundaries(boundaries);
	if (unique.length === 0) {
		return "At least one boundary is required.";
	}
	if (unique.length !== boundaries.length) {
		return "Duplicate boundary selections are not allowed.";
	}
	if (platformLocked) {
		const platformOnly =
			unique.length === 1 &&
			unique[0]?.boundary_kind === "platform" &&
			unique[0].organization_id == null &&
			unique[0].organization_group_id == null;
		if (!platformOnly) {
			return "Platform Admin is fixed to Platform only.";
		}
	}
	return null;
}

export function summarizeBoundaries(
	boundaries: RoleBoundaryDraft[],
	maps: BoundaryMaps,
): string {
	if (boundaries.length === 0) {
		return "No access selected";
	}
	const summaryParts = boundaries.map((boundary) => boundaryLabel(boundary, maps));
	return summaryParts.join(" · ");
}

export function createDefaultBoundaries(
	defaultBoundary: RoleBoundaryDraft | null | undefined,
	platformLocked: boolean,
): RoleBoundaryDraft[] {
	if (platformLocked) {
		return [{ boundary_kind: "platform" }];
	}
	return defaultBoundary ? [defaultBoundary] : [];
}

export function addRoleAssignment(
	value: RoleAssignmentDraft[],
	roleId: string,
	defaultBoundary: RoleBoundaryDraft | null | undefined,
	platformLocked: boolean,
): RoleAssignmentDraft[] {
	const next = value.filter((item) => item.role_id !== roleId);
	next.push({
		role_id: roleId,
		boundaries: createDefaultBoundaries(defaultBoundary, platformLocked),
	});
	return next;
}

export function removeRoleAssignment(
	value: RoleAssignmentDraft[],
	roleId: string,
): RoleAssignmentDraft[] {
	return value.filter((item) => item.role_id !== roleId);
}

export function updateRoleAssignmentBoundaries(
	value: RoleAssignmentDraft[],
	roleId: string,
	boundaries: RoleBoundaryDraft[],
	platformLocked: boolean,
): RoleAssignmentDraft[] {
	return value.map((item) =>
		item.role_id !== roleId
			? item
			: {
					...item,
					boundaries: platformLocked
						? [{ boundary_kind: "platform" }]
						: dedupeBoundaries(boundaries),
				},
	);
}

export function toggleBoundarySelection(
	boundaries: RoleBoundaryDraft[],
	boundary: RoleBoundaryDraft,
): RoleBoundaryDraft[] {
	const next = boundaries.filter((item) => !areBoundariesEqual(item, boundary));
	if (next.length === boundaries.length) {
		next.push(boundary);
	}
	return dedupeBoundaries(next);
}

export function isPlatformLockedRole(
	role: RoleAssignmentEditorRole,
	platformAdminRoleIds?: string[],
): boolean {
	if (platformAdminRoleIds?.includes(role.id)) return true;
	if (role.key === "platform.admin" || role.key === "platform_admin") return true;
	return false;
}

function boundaryResourceMaps(
	organizations: RoleAssignmentEditorOrganization[],
	organizationGroups: RoleAssignmentEditorOrganizationGroup[],
): BoundaryMaps {
	return {
		organizations: new Map(organizations.map((item) => [item.id, item.name])),
		organizationGroups: new Map(
			organizationGroups.map((item) => [item.id, item.name]),
		),
	};
}

function boundaryLabel(boundary: RoleBoundaryDraft, maps: BoundaryMaps): string {
	switch (boundary.boundary_kind) {
		case "platform":
			return "Platform";
		case "managed_organizations":
			return "Managed organizations";
		case "organization":
			return maps.organizations.get(boundary.organization_id ?? "") ?? "Organization";
		case "organization_group":
			return (
				maps.organizationGroups.get(boundary.organization_group_id ?? "") ??
				"Organization group"
			);
	}
}

function RoleBoundaryEditor({
	role,
	boundaries,
	disabled,
	platformLocked,
	organizations,
	organizationGroups,
	onChange,
}: {
	role: RoleAssignmentEditorRole;
	boundaries: RoleBoundaryDraft[];
	disabled?: boolean;
	platformLocked: boolean;
	organizations: RoleAssignmentEditorOrganization[];
	organizationGroups: RoleAssignmentEditorOrganizationGroup[];
	onChange: (next: RoleBoundaryDraft[]) => void;
}) {
	const [open, setOpen] = useState(false);
	const error = validateBoundaries(boundaries, platformLocked);

	const setPlatformOnly = () => {
		onChange([{ boundary_kind: "platform" }]);
	};

	const toggle = (boundary: RoleBoundaryDraft) => {
		if (platformLocked) {
			setPlatformOnly();
			return;
		}
		onChange(toggleBoundarySelection(boundaries, boundary));
	};

	const specialBoundaryItems = [
		{ kind: "platform" as const, label: "Platform" },
		{ kind: "managed_organizations" as const, label: "Managed organizations" },
	];

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					type="button"
					variant="outline"
					size="sm"
					className="shrink-0"
					disabled={disabled}
					aria-label={`Edit access for ${role.name}`}
				>
					<ChevronsUpDown className="mr-2 h-4 w-4" />
					Edit access
				</Button>
			</PopoverTrigger>
			<PopoverContent className="w-[min(96vw,36rem)] p-0" align="end">
				<div className="border-b px-4 py-3">
					<p className="text-sm font-medium">Edit access</p>
					<p className="mt-1 text-xs text-muted-foreground">
						Choose where this role applies. At least one boundary is required.
					</p>
				</div>
				<Command>
					<CommandInput placeholder="Search boundaries..." />
					<CommandList className="max-h-80 overflow-y-auto">
						<CommandEmpty>No boundaries found.</CommandEmpty>
						<CommandGroup heading="Special boundaries">
							{specialBoundaryItems.map((item) => {
								const selected = boundaries.some(
									(boundary) => boundary.boundary_kind === item.kind,
								);
								const selectedBoundary: RoleBoundaryDraft = {
									boundary_kind: item.kind,
								};
								return (
									<CommandItem
										key={item.kind}
										value={item.label}
										keywords={[item.label]}
										data-checked={selected}
										disabled={disabled || (platformLocked && item.kind !== "platform")}
										onSelect={() =>
											item.kind === "platform" && platformLocked
												? setPlatformOnly()
												: toggle(selectedBoundary)
										}
									>
										<div className="flex w-full items-center justify-between gap-3">
											<div className="flex min-w-0 items-center gap-2">
												{item.kind === "platform" ? (
													<Shield className="h-4 w-4 shrink-0" />
												) : (
													<Building2 className="h-4 w-4 shrink-0" />
												)}
												<span className="truncate text-sm">{item.label}</span>
											</div>
											{selected ? <Check className="h-4 w-4" /> : null}
										</div>
									</CommandItem>
								);
							})}
						</CommandGroup>
						<CommandGroup heading="Organizations">
							{organizations.map((organization) => {
								const selected = boundaries.some(
									(boundary) =>
										boundary.boundary_kind === "organization" &&
										boundary.organization_id === organization.id,
								);
								return (
									<CommandItem
										key={organization.id}
										value={organization.name}
										keywords={[organization.id]}
										data-checked={selected}
										disabled={disabled || platformLocked}
										onSelect={() =>
											toggle({
												boundary_kind: "organization",
												organization_id: organization.id,
											})
										}
									>
										<div className="flex w-full items-center justify-between gap-3">
											<div className="min-w-0">
												<p className="truncate text-sm font-medium">
													{organization.name}
												</p>
											</div>
											{selected ? <Check className="h-4 w-4" /> : null}
										</div>
									</CommandItem>
								);
							})}
						</CommandGroup>
						<CommandGroup heading="Organization groups">
							{organizationGroups.map((group) => {
								const selected = boundaries.some(
									(boundary) =>
										boundary.boundary_kind === "organization_group" &&
										boundary.organization_group_id === group.id,
								);
								return (
									<CommandItem
										key={group.id}
										value={group.name}
										keywords={[group.id]}
										data-checked={selected}
										disabled={disabled || platformLocked}
										onSelect={() =>
											toggle({
												boundary_kind: "organization_group",
												organization_group_id: group.id,
											})
										}
									>
										<div className="flex w-full items-center justify-between gap-3">
											<div className="min-w-0">
												<p className="truncate text-sm font-medium">
													{group.name}
												</p>
											</div>
											{selected ? <Check className="h-4 w-4" /> : null}
										</div>
									</CommandItem>
								);
							})}
						</CommandGroup>
					</CommandList>
				</Command>
				<div className="border-t px-4 py-3">
					{error ? (
						<p className="text-xs text-destructive" role="alert">
							{error}
						</p>
					) : (
						<p className="text-xs text-muted-foreground">
							{platformLocked
								? "Platform Admin is fixed to Platform only."
								: "Use multiple boundaries when this role should apply in more than one place."}
						</p>
					)}
				</div>
			</PopoverContent>
		</Popover>
	);
}

export function RoleAssignmentEditor({
	roles,
	value,
	organizations,
	organizationGroups,
	disabled = false,
	isLoading = false,
	defaultBoundary = null,
	platformAdminRoleIds,
	onChange,
}: RoleAssignmentEditorProps) {
	const maps = useMemo(
		() => boundaryResourceMaps(organizations, organizationGroups),
		[organizations, organizationGroups],
	);
	const assignedIds = useMemo(
		() => new Set(value.map((item) => item.role_id)),
		[value],
	);
	const roleOptions = useMemo<ComboboxOption[]>(
		() =>
			roles.map((role) => ({
				value: role.id,
				label: role.name,
				description:
					role.description ??
					(role.key === "platform.admin" || role.key === "platform_admin"
						? "Platform Admin role"
						: undefined),
			})),
		[roles],
	);
	const validationErrors = useMemo(
		() =>
			value
				.map((assignment) => {
					const role = roles.find((item) => item.id === assignment.role_id);
					return validateBoundaries(
						assignment.boundaries,
						role ? isPlatformLockedRole(role, platformAdminRoleIds) : false,
					);
				})
				.filter((error): error is string => Boolean(error)),
		[value, roles, platformAdminRoleIds],
	);

	const addRole = (roleId: string) => {
		const role = roles.find((item) => item.id === roleId);
		if (!role) return;
		onChange(
			addRoleAssignment(
				value,
				role.id,
				defaultBoundary,
				isPlatformLockedRole(role, platformAdminRoleIds),
			),
		);
	};

	return (
		<div className="space-y-4">
			<div className="space-y-2">
				<Label htmlFor="role-assignment-editor-add-role">Add role</Label>
				<Combobox
					id="role-assignment-editor-add-role"
					options={roleOptions.filter((role) => !assignedIds.has(role.value))}
					value=""
					onValueChange={addRole}
					placeholder="Select a role"
					searchPlaceholder="Search roles..."
					emptyText="No roles found."
					disabled={disabled || isLoading}
					isLoading={isLoading}
					aria-label="Add role"
				/>
			</div>

			<div className="space-y-2">
				{value.length === 0 ? (
					<div className="rounded-xl border border-dashed px-4 py-6 text-sm text-muted-foreground">
						No roles assigned yet.
					</div>
				) : (
					value.map((assignment) => {
						const role = roles.find((item) => item.id === assignment.role_id);
						const locked = role
							? isPlatformLockedRole(role, platformAdminRoleIds)
							: false;
						return (
							<RoleAssignmentRow
								key={assignment.role_id}
								role={role}
								assignment={assignment}
								disabled={disabled || isLoading}
								locked={locked}
								maps={maps}
								organizations={organizations}
								organizationGroups={organizationGroups}
								onRemove={() =>
									onChange(removeRoleAssignment(value, assignment.role_id))
								}
								onBoundariesChange={(nextBoundaries) =>
									onChange(
										updateRoleAssignmentBoundaries(
											value,
											assignment.role_id,
											nextBoundaries,
											locked,
										),
									)
								}
							/>
						);
					})
				)}
			</div>

			{validationErrors.length > 0 ? (
				<div
					className="rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
					role="alert"
				>
					<p className="font-medium">Review access selections</p>
					<ul className="mt-1 list-disc space-y-1 pl-5 text-xs">
						{validationErrors.map((error, index) => (
							<li key={`${error}-${index}`}>{error}</li>
						))}
					</ul>
				</div>
			) : null}
		</div>
	);
}

function RoleAssignmentRow({
	role,
	assignment,
	disabled,
	locked,
	maps,
	organizations,
	organizationGroups,
	onRemove,
	onBoundariesChange,
}: {
	role: RoleAssignmentEditorRole | undefined;
	assignment: RoleAssignmentDraft;
	disabled: boolean;
	locked: boolean;
	maps: BoundaryMaps;
	organizations: RoleAssignmentEditorOrganization[];
	organizationGroups: RoleAssignmentEditorOrganizationGroup[];
	onRemove: () => void;
	onBoundariesChange: (next: RoleBoundaryDraft[]) => void;
}) {
	const summary = summarizeBoundaries(assignment.boundaries, maps);

	return (
		<div className="rounded-xl border px-4 py-3">
			<div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
				<div className="min-w-0 space-y-1">
					<div className="flex flex-wrap items-center gap-2">
						<p className="truncate font-medium">
							{role?.name ?? assignment.role_id}
						</p>
						<Badge variant="secondary" className="gap-1">
							{locked ? (
								<Shield className="h-3 w-3" />
							) : (
								<Users className="h-3 w-3" />
							)}
							{locked ? "Platform only" : "Custom access"}
						</Badge>
					</div>
					<p className="text-sm text-muted-foreground">{summary}</p>
					<div className="flex flex-wrap gap-1.5 pt-1">
						{assignment.boundaries.map((boundary) => (
							<Badge key={boundaryKey(boundary)} variant="outline" className="gap-1">
								{boundary.boundary_kind === "organization" ? (
									<Building2 className="h-3 w-3" />
								) : boundary.boundary_kind === "organization_group" ? (
									<Users className="h-3 w-3" />
								) : boundary.boundary_kind === "managed_organizations" ? (
									<Building2 className="h-3 w-3" />
								) : (
									<Shield className="h-3 w-3" />
								)}
								<span className="max-w-52 truncate">
									{boundaryLabel(boundary, maps)}
								</span>
							</Badge>
						))}
					</div>
				</div>

				<div className="flex shrink-0 items-center gap-2">
					<RoleBoundaryEditor
						role={role ?? {
							id: assignment.role_id,
							name: assignment.role_id,
						}}
						boundaries={assignment.boundaries}
						disabled={disabled}
						platformLocked={locked}
						organizations={organizations}
						organizationGroups={organizationGroups}
						onChange={onBoundariesChange}
					/>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={onRemove}
						disabled={disabled}
						aria-label={`Remove ${role?.name ?? assignment.role_id}`}
					>
						<X className="h-4 w-4" />
					</Button>
				</div>
			</div>
		</div>
	);
}
