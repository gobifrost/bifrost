import { useMemo, useState } from "react";
import {
	AlertCircle,
	AlertTriangle,
	Building2,
	Globe,
	Loader2,
	Network,
	Plus,
	Shield,
	X,
} from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import { DialogFooter } from "@/components/ui/dialog";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrganizations } from "@/hooks/useOrganizations";
import {
	useReplaceUserRoleAssignments,
	useUserRoleAssignments,
} from "@/hooks/useUsers";
import { getErrorMessage } from "@/lib/api-error";
import { orgTarget } from "@/lib/authorization";
import { PLATFORM_ADMIN_ROLE_ID } from "@/lib/builtin-roles";
import { placeLabel } from "@/lib/role-boundaries";
import { useAuthorization } from "@/services/authorization";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];
type Assignments = components["schemas"]["UserRoleAssignmentsResponse"];
type AssignableRole = components["schemas"]["AssignableRole"];
type BoundaryKind = components["schemas"]["RoleBoundaryInput"]["kind"];

interface Place {
	kind: BoundaryKind;
	organization_id: string | null;
}

interface DraftRole {
	roleId: string;
	places: Place[];
}

interface Draft {
	baseRoleId: string;
	additional: DraftRole[];
}

interface RoleInfo {
	name: string;
	description?: string | null;
	is_builtin: boolean;
}

const ACTION_WORDS: Record<string, string> = {
	read: "view",
	readwrite: "change",
	execute: "run",
};
const DOMAIN_WORDS: Record<string, string> = {
	agentruns: "agent runs",
	configs: "configuration",
	filepolicies: "file policies",
	mcp: "MCP servers",
	policyrules: "policy rules",
	roleassignments: "role assignments",
	"users.lifecycle": "user lifecycle",
};

/** "agents.read" → "view agents". */
function describePermission(permission: string): string {
	const split = permission.lastIndexOf(".");
	const domain = permission.slice(0, split);
	const action = permission.slice(split + 1);
	return `${ACTION_WORDS[action] ?? action} ${DOMAIN_WORDS[domain] ?? domain.replace(/\./g, " ")}`;
}

function describePermissions(permissions: string[]): string {
	return permissions.length > 0
		? permissions.map(describePermission).join(", ")
		: "none";
}

function placeKey(place: Place): string {
	return `${place.kind}:${place.organization_id ?? ""}`;
}

function draftFrom(data: Assignments): Draft {
	return {
		baseRoleId: data.base_role.id,
		additional: data.additional.map((role) => ({
			roleId: role.role_id,
			places: role.boundaries.map((boundary) => ({
				kind: boundary.kind,
				organization_id: boundary.organization_id ?? null,
			})),
		})),
	};
}

function draftSignature(draft: Draft): string {
	return JSON.stringify({
		base: draft.baseRoleId,
		additional: draft.additional
			.map((role) => ({
				role: role.roleId,
				places: role.places.map(placeKey).sort(),
			}))
			.sort((a, b) => a.role.localeCompare(b.role)),
	});
}

function placeText(place: Place, orgName: (id: string) => string): string {
	return placeLabel(
		place.kind,
		place.kind === "organization"
			? orgName(place.organization_id ?? "")
			: "",
	);
}

function PlaceIcon({ kind }: { kind: BoundaryKind }) {
	if (kind === "managed_organizations")
		return <Network aria-hidden="true" className="size-3.5" />;
	if (kind === "platform")
		return <Globe aria-hidden="true" className="size-3.5" />;
	return <Building2 aria-hidden="true" className="size-3.5" />;
}

interface PlacementOption {
	place: Place;
	label: string;
	description?: string;
}

function PlacementPicker({
	roleName,
	options,
	onAdd,
}: {
	roleName: string;
	options: PlacementOption[];
	onAdd: (place: Place) => void;
}) {
	const [open, setOpen] = useState(false);
	const broad = options.filter((o) => o.place.kind !== "organization");
	const orgs = options.filter((o) => o.place.kind === "organization");
	if (options.length === 0) return null;
	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					className="min-h-11 sm:min-h-8"
					aria-label={`Add where ${roleName} applies`}
				>
					<Plus aria-hidden="true" className="size-4" />
					Add place
				</Button>
			</PopoverTrigger>
			<PopoverContent variant="picker" className="p-0" align="start">
				<Command>
					<CommandInput
						placeholder="Search organizations..."
						aria-label="Search places"
					/>
					<CommandList className="max-h-60 overflow-y-auto">
						<CommandEmpty>No places found.</CommandEmpty>
						{broad.length > 0 && (
							<CommandGroup heading="Broad">
								{broad.map((option) => (
									<CommandItem
										key={placeKey(option.place)}
										value={placeKey(option.place)}
										keywords={[option.label]}
										onSelect={() => {
											onAdd(option.place);
											setOpen(false);
										}}
									>
										<PlaceIcon kind={option.place.kind} />
										<div className="flex min-w-0 flex-1 flex-col">
											<span className="font-medium">
												{option.label}
											</span>
											{option.description && (
												<span className="text-xs text-muted-foreground">
													{option.description}
												</span>
											)}
										</div>
									</CommandItem>
								))}
							</CommandGroup>
						)}
						{orgs.length > 0 && (
							<CommandGroup heading="Organizations">
								{orgs.map((option) => (
									<CommandItem
										key={placeKey(option.place)}
										value={placeKey(option.place)}
										keywords={[option.label]}
										onSelect={() => {
											onAdd(option.place);
											setOpen(false);
										}}
									>
										<PlaceIcon kind="organization" />
										<span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
											{option.label}
										</span>
									</CommandItem>
								))}
							</CommandGroup>
						)}
					</CommandList>
				</Command>
			</PopoverContent>
		</Popover>
	);
}

function AddRolePicker({
	roles,
	onAdd,
}: {
	roles: AssignableRole[];
	onAdd: (role: AssignableRole) => void;
}) {
	const [open, setOpen] = useState(false);
	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<Button
					type="button"
					variant="outline"
					className="min-h-11 sm:min-h-9"
				>
					<Plus aria-hidden="true" className="size-4" />
					Add role
				</Button>
			</PopoverTrigger>
			<PopoverContent variant="picker" className="p-0" align="start">
				<Command>
					<CommandInput
						placeholder="Search roles..."
						aria-label="Search roles"
					/>
					<CommandList className="max-h-60 overflow-y-auto">
						<CommandEmpty>No roles found.</CommandEmpty>
						<CommandGroup>
							{roles.map((role) => (
								<CommandItem
									key={role.id}
									value={role.id}
									keywords={[role.name]}
									onSelect={() => {
										onAdd(role);
										setOpen(false);
									}}
								>
									<div className="flex min-w-0 flex-1 flex-col">
										<span className="font-medium">
											{role.name}
											{role.is_builtin && (
												<span className="ml-2 text-xs font-normal text-muted-foreground">
													Built-in
												</span>
											)}
										</span>
										{role.description && (
											<span className="text-xs text-muted-foreground">
												{role.description}
											</span>
										)}
									</div>
								</CommandItem>
							))}
						</CommandGroup>
					</CommandList>
				</Command>
			</PopoverContent>
		</Popover>
	);
}

/**
 * A user's base role and additional roles, with where each additional role
 * applies. Saving replaces both in one request; the server decides what the
 * caller may grant (`assignable_roles`) and validates every change.
 */
export function UserRoleAssignmentsPanel({
	user,
	isSelf,
}: {
	user: User;
	isSelf: boolean;
}) {
	const authorization = useAuthorization();
	const target = orgTarget(user.organization_id);
	const canRead = authorization.canAt("roleassignments.read", target);
	const assignmentsQuery = useUserRoleAssignments(user.id, canRead);
	const data = assignmentsQuery.data;
	const replace = useReplaceUserRoleAssignments();
	const organizationsQuery = useOrganizations({
		enabled: authorization.canAnywhere("organizations.read"),
	});
	const organizations = organizationsQuery.data;

	const [draft, setDraft] = useState<Draft | null>(null);
	const [saveError, setSaveError] = useState<string | null>(null);
	const [loadedFrom, setLoadedFrom] = useState<Assignments | undefined>();
	// Take fresh server state, unless it would overwrite unsaved edits.
	if (data && data !== loadedFrom) {
		const pristine =
			!draft ||
			!loadedFrom ||
			draftSignature(draft) === draftSignature(draftFrom(loadedFrom));
		setLoadedFrom(data);
		if (pristine) setDraft(draftFrom(data));
	}

	const providerOrgId = authorization.authorization?.provider_organization_id;
	const blockedByProtection =
		!!data?.is_protected && !authorization.isPlatformAdmin;
	const canEdit =
		!!data &&
		!isSelf &&
		!blockedByProtection &&
		authorization.canAt("roleassignments.readwrite", target);

	const assignable = useMemo(
		() => new Map((data?.assignable_roles ?? []).map((r) => [r.id, r])),
		[data],
	);
	const roleInfo = useMemo(() => {
		const info = new Map<string, RoleInfo>();
		if (!data) return info;
		info.set(data.base_role.id, data.base_role);
		for (const role of data.additional) info.set(role.role_id, role);
		for (const role of data.assignable_roles) info.set(role.id, role);
		return info;
	}, [data]);

	const orgName = (id: string) => {
		if (id === user.organization_id && !organizations) {
			return "their organization";
		}
		const fromList = organizations?.find((org) => org.id === id)?.name;
		if (fromList) return fromList;
		for (const role of data?.additional ?? []) {
			for (const boundary of role.boundaries) {
				if (
					boundary.organization_id === id &&
					boundary.organization_name
				)
					return boundary.organization_name;
			}
		}
		return "an organization you can't see";
	};

	const orgAllowed = (role: AssignableRole, orgId: string) =>
		(role.provider_organization_allowed || orgId !== providerOrgId) &&
		(authorization.isPlatformAdmin ||
			authorization.canAt("roleassignments.readwrite", {
				kind: "org",
				id: orgId,
			}));

	const placementOptions = (
		role: AssignableRole,
		taken: Place[],
	): PlacementOption[] => {
		const takenKeys = new Set(taken.map(placeKey));
		const options: PlacementOption[] = [];
		if (role.boundary_kinds.includes("managed_organizations")) {
			options.push({
				place: { kind: "managed_organizations", organization_id: null },
				label: "All customer organizations",
				description:
					"Every organization except the provider organization",
			});
		}
		if (role.boundary_kinds.includes("platform")) {
			options.push({
				place: { kind: "platform", organization_id: null },
				label: "Platform-wide",
				description:
					"Global users and platform-level items, not each organization",
			});
		}
		if (role.boundary_kinds.includes("organization")) {
			const ids = new Set((organizations ?? []).map((org) => org.id));
			if (user.organization_id) ids.add(user.organization_id);
			for (const id of ids) {
				if (!orgAllowed(role, id)) continue;
				options.push({
					place: { kind: "organization", organization_id: id },
					label: orgName(id),
				});
			}
		}
		return options.filter((o) => !takenKeys.has(placeKey(o.place)));
	};

	const defaultPlaces = (role: AssignableRole): Place[] => {
		if (role.fixed_boundaries?.length) {
			return role.fixed_boundaries.map((boundary) => ({
				kind: boundary.kind,
				organization_id: boundary.organization_id ?? null,
			}));
		}
		const home = user.organization_id;
		if (
			home &&
			role.boundary_kinds.includes("organization") &&
			orgAllowed(role, home)
		) {
			return [{ kind: "organization", organization_id: home }];
		}
		if (role.boundary_kinds.includes("managed_organizations")) {
			return [{ kind: "managed_organizations", organization_id: null }];
		}
		if (!home && role.boundary_kinds.includes("platform")) {
			return [{ kind: "platform", organization_id: null }];
		}
		const first = placementOptions(role, [])[0];
		return first ? [first.place] : [];
	};

	if (!canRead) {
		return (
			<div className="px-4 py-4 text-sm text-muted-foreground sm:px-6">
				You can't view this person's roles.
			</div>
		);
	}

	if (assignmentsQuery.isError && !data) {
		return (
			<div className="space-y-3 px-4 py-4 sm:px-6">
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						{getErrorMessage(
							assignmentsQuery.error,
							"Roles could not be loaded.",
						)}
					</AlertDescription>
				</Alert>
				<Button
					type="button"
					variant="outline"
					className="min-h-11"
					disabled={assignmentsQuery.isFetching}
					onClick={() => void assignmentsQuery.refetch()}
				>
					Retry roles
				</Button>
			</div>
		);
	}

	if (!data || !draft) {
		return (
			<div
				role="status"
				aria-label="Loading roles"
				className="space-y-3 px-4 py-4 sm:px-6"
			>
				<Skeleton className="h-4 w-32" />
				<Skeleton className="h-11 w-full" />
				<Skeleton className="h-24 w-full" />
			</div>
		);
	}

	const dirty = draftSignature(draft) !== draftSignature(draftFrom(data));
	const missingPlace = draft.additional.find((r) => r.places.length === 0);
	const baseOptions = [
		data.base_role,
		...data.assignable_roles.filter(
			(r) => r.can_be_base && r.id !== data.base_role.id,
		),
	];
	const canChangeBase = canEdit && baseOptions.length > 1;
	// The server offers Platform Admin as an additional role only to people in
	// the provider organization (or Global users).
	const adminHint =
		canEdit &&
		authorization.isPlatformAdmin &&
		!user.is_superuser &&
		!data.assignable_roles.some(
			(role) => role.id === PLATFORM_ADMIN_ROLE_ID,
		);
	const takenRoleIds = new Set(draft.additional.map((r) => r.roleId));
	const addableRoles = data.assignable_roles.filter(
		(r) => r.can_be_additional && !takenRoleIds.has(r.id),
	);
	const holdsAdmin = (roles: { roleId: string }[]) =>
		roles.some((role) => role.roleId === PLATFORM_ADMIN_ROLE_ID);
	const promoting =
		holdsAdmin(draft.additional) && !holdsAdmin(draftFrom(data).additional);
	const demoting =
		!holdsAdmin(draft.additional) && holdsAdmin(draftFrom(data).additional);
	// A custom base role replaces the old base role's permissions outright.
	const newCustomBase =
		draft.baseRoleId !== data.base_role.id
			? data.assignable_roles.find(
					(role) => role.id === draft.baseRoleId && !role.is_builtin,
				)
			: undefined;
	const savedBasePermissions = assignable.get(data.base_role.id)?.permissions;
	const baseChangeNotice =
		newCustomBase &&
		`${newCustomBase.name} replaces ${data.base_role.name} as ${user.name || user.email}'s base role. ` +
			`In ${user.organization_id ? orgName(user.organization_id) : "their organization"}, ` +
			`they'll have only ${newCustomBase.name}'s permissions (${describePermissions(newCustomBase.permissions)}) ` +
			`instead of ${data.base_role.name}'s` +
			(savedBasePermissions
				? ` (${describePermissions(savedBasePermissions)}).`
				: " permissions.");

	const updateRole = (roleId: string, places: Place[]) =>
		setDraft({
			...draft,
			additional: draft.additional.map((r) =>
				r.roleId === roleId ? { ...r, places } : r,
			),
		});

	const handleSave = async () => {
		if (!dirty || missingPlace || replace.isPending) return;
		setSaveError(null);
		try {
			const saved = await replace.mutateAsync({
				params: { path: { user_id: user.id } },
				body: {
					base_role_id: draft.baseRoleId,
					additional: draft.additional.map((role) => ({
						role_id: role.roleId,
						boundaries: role.places.map((place) => ({
							kind: place.kind,
							organization_id: place.organization_id,
						})),
					})),
				},
			});
			setLoadedFrom(saved);
			setDraft(draftFrom(saved));
			toast.success("Roles saved", {
				description: `${user.name || user.email}'s roles and access are up to date`,
			});
		} catch (error) {
			setSaveError(getErrorMessage(error, "Roles could not be saved"));
		}
	};

	const readOnlyReason = isSelf
		? "You can't change your own roles."
		: blockedByProtection
			? null
			: !canEdit
				? "You can view these roles but not change them."
				: null;

	return (
		<>
			<div
				aria-busy={replace.isPending}
				inert={replace.isPending}
				className="min-h-0 flex-1 space-y-6 overflow-y-auto px-4 py-4 sm:px-6"
			>
				{readOnlyReason && (
					<p className="text-sm text-muted-foreground">
						{readOnlyReason}
					</p>
				)}
				{saveError && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{saveError}</AlertDescription>
					</Alert>
				)}

				<section
					aria-labelledby="base-role-heading"
					className="space-y-2"
				>
					<h3
						id="base-role-heading"
						className="text-sm font-semibold"
					>
						Base role
					</h3>
					<p
						id="base-role-help"
						className="text-xs leading-5 text-muted-foreground"
					>
						What this person can do in their own organization.
						Everyone has exactly one. A custom base role replaces
						the User role's defaults.
					</p>
					{canChangeBase ? (
						<Combobox
							id="base-role"
							aria-label="Base role"
							aria-describedby="base-role-help"
							value={draft.baseRoleId}
							onValueChange={(value) =>
								value &&
								setDraft({ ...draft, baseRoleId: value })
							}
							options={baseOptions.map((role) => ({
								value: role.id,
								label: role.name,
								description: role.description ?? undefined,
							}))}
							placeholder="Choose a base role"
							searchPlaceholder="Search roles..."
						/>
					) : (
						<p className="flex min-h-11 items-center rounded-[var(--bf-radius-control)] border border-border/70 bg-muted/40 px-3 text-sm">
							{data.base_role.name}
						</p>
					)}
					{canEdit && !canChangeBase && (
						<p className="text-xs text-muted-foreground">
							You can't change this person's base role.
						</p>
					)}
					{baseChangeNotice && (
						<Alert>
							<AlertTriangle className="h-4 w-4" />
							<AlertDescription>
								{baseChangeNotice}
							</AlertDescription>
						</Alert>
					)}
				</section>

				<section
					aria-labelledby="additional-roles-heading"
					className="space-y-3"
				>
					<div className="space-y-1">
						<h3
							id="additional-roles-heading"
							className="text-sm font-semibold"
						>
							Additional roles
						</h3>
						<p className="text-xs leading-5 text-muted-foreground">
							Extra access on top of the base role. Each one
							applies only where you choose.
						</p>
					</div>
					{draft.additional.length === 0 ? (
						<p className="rounded-[var(--bf-radius-surface)] border border-dashed border-border/70 p-4 text-sm text-muted-foreground">
							No additional roles.
						</p>
					) : (
						<ul className="space-y-2" aria-label="Additional roles">
							{draft.additional.map((role) => {
								const info = roleInfo.get(role.roleId);
								const name = info?.name ?? "Unknown role";
								const grantable = assignable.get(role.roleId);
								// Listed but not grantable: the user holds a role
								// they could no longer be given (e.g. Platform
								// Operator outside the provider org). It can only
								// be removed.
								const removable = canEdit && !!grantable;
								const editable =
									removable && !!grantable?.can_be_additional;
								// The server fixes where some roles apply; nobody picks.
								const fixedPlaces =
									!!grantable?.fixed_boundaries?.length;
								return (
									<li
										key={role.roleId}
										className="rounded-[var(--bf-radius-surface)] border border-border/70 p-3"
									>
										<div className="flex items-start gap-2">
											<div className="min-w-0 flex-1 space-y-1">
												<p className="flex flex-wrap items-center gap-2 font-medium [overflow-wrap:anywhere]">
													{name}
													{info?.is_builtin && (
														<Badge variant="outline">
															Built-in
														</Badge>
													)}
												</p>
												{info?.description && (
													<p className="text-xs leading-5 text-muted-foreground">
														{info.description}
													</p>
												)}
											</div>
											{removable && (
												<Button
													type="button"
													variant="ghost"
													size="icon"
													className="h-11 w-11 shrink-0 sm:h-8 sm:w-8"
													aria-label={`Remove ${name}`}
													onClick={() =>
														setDraft({
															...draft,
															additional:
																draft.additional.filter(
																	(r) =>
																		r.roleId !==
																		role.roleId,
																),
														})
													}
												>
													<X className="h-4 w-4" />
												</Button>
											)}
										</div>
										{fixedPlaces ? (
											<p className="mt-2 text-xs text-muted-foreground">
												Applies everywhere.
											</p>
										) : (
											<div className="mt-2 flex flex-wrap items-center gap-1.5">
												<span className="text-xs text-muted-foreground">
													Applies
												</span>
												<ul
													className="contents"
													aria-label={`Where ${name} applies`}
												>
													{role.places.map(
														(place) => {
															const label =
																placeText(
																	place,
																	orgName,
																);
															return (
																<li
																	key={placeKey(
																		place,
																	)}
																>
																	<Badge
																		variant="secondary"
																		className="h-auto min-h-8 gap-1.5 whitespace-normal py-1 [overflow-wrap:anywhere]"
																	>
																		<PlaceIcon
																			kind={
																				place.kind
																			}
																		/>
																		{label}
																		{editable &&
																			role
																				.places
																				.length >
																				1 && (
																				<button
																					type="button"
																					className="flex size-6 items-center justify-center rounded-[var(--bf-radius-control)] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
																					aria-label={`Remove ${label} from ${name}`}
																					onClick={() =>
																						updateRole(
																							role.roleId,
																							role.places.filter(
																								(
																									p,
																								) =>
																									placeKey(
																										p,
																									) !==
																									placeKey(
																										place,
																									),
																							),
																						)
																					}
																				>
																					<X className="h-3 w-3" />
																				</button>
																			)}
																	</Badge>
																</li>
															);
														},
													)}
												</ul>
												{editable && grantable && (
													<PlacementPicker
														roleName={name}
														options={placementOptions(
															grantable,
															role.places,
														)}
														onAdd={(place) =>
															updateRole(
																role.roleId,
																[
																	...role.places,
																	place,
																],
															)
														}
													/>
												)}
											</div>
										)}
										{role.places.length === 0 && (
											<p
												role="alert"
												className="mt-2 text-xs text-destructive"
											>
												Choose where {name} applies.
											</p>
										)}
										{canEdit && !editable && (
											<p className="mt-2 text-xs text-muted-foreground">
												{role.roleId ===
													PLATFORM_ADMIN_ROLE_ID &&
												!user.organization_id
													? "Move this person into an organization before removing Platform Admin."
													: removable
														? "This person can't be given this role any more. You can remove it, but not change where it applies."
														: "You can't change this role. Saving keeps it as it is."}
											</p>
										)}
									</li>
								);
							})}
						</ul>
					)}
					{adminHint && (
						<p className="text-xs text-muted-foreground">
							To make this person a Platform Admin, first move
							them to the provider organization on the Profile
							tab.
						</p>
					)}
					{promoting && (
						<Alert>
							<Shield className="h-4 w-4" />
							<AlertDescription>
								A Platform Admin has unrestricted access to
								every organization, user, and setting.
							</AlertDescription>
						</Alert>
					)}
					{demoting && (
						<Alert variant="destructive">
							<AlertTriangle className="h-4 w-4" />
							<AlertDescription>
								This person will lose access to other
								organizations and platform settings.
							</AlertDescription>
						</Alert>
					)}
					{canEdit && addableRoles.length > 0 && (
						<AddRolePicker
							roles={addableRoles}
							onAdd={(role) =>
								setDraft({
									...draft,
									additional: [
										...draft.additional,
										{
											roleId: role.id,
											places: defaultPlaces(role),
										},
									],
								})
							}
						/>
					)}
				</section>
			</div>
			{canEdit && (
				<DialogFooter className="shrink-0 border-t border-border/70 px-4 py-4 sm:px-6">
					<Button
						type="button"
						variant="outline"
						className="h-11"
						disabled={!dirty || replace.isPending}
						onClick={() => {
							setDraft(draftFrom(data));
							setSaveError(null);
						}}
					>
						Discard changes
					</Button>
					<Button
						type="button"
						className="h-11"
						disabled={!dirty || !!missingPlace || replace.isPending}
						onClick={() => void handleSave()}
					>
						{replace.isPending && (
							<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
						)}
						Save roles
					</Button>
				</DialogFooter>
			)}
		</>
	);
}
