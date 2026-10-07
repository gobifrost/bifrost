import { useMemo, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
	AlertCircle,
	AlertTriangle,
	Building,
	Building2,
	Globe,
	Loader2,
	Shield,
	SlidersHorizontal,
	X,
} from "lucide-react";
import { RadioGroup as RadioGroupPrimitive } from "radix-ui";
import { toast } from "sonner";

import { GrantChip } from "@/components/access/GrantChip";
import { PlaceLabel } from "@/components/access/PlaceLabel";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { MultiCombobox } from "@/components/ui/multi-combobox";
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
import { motionSeconds } from "@/lib/motion";
import { permissionDisplayName, permissionParts } from "@/lib/permission-words";
import {
	offeredPresets,
	placeKey,
	placeLabel,
	placesForPreset,
	presetFor,
	type PlacementPreset,
	type RolePlace,
} from "@/lib/role-boundaries";
import {
	usePermissionCatalog,
	type PermissionCatalogEntry,
	type Place as MapPlace,
} from "@/services/access";
import { useAuthorization } from "@/services/authorization";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];
type Assignments = components["schemas"]["UserRoleAssignmentsResponse"];
type AssignableRole = components["schemas"]["AssignableRole"];

interface DraftRole {
	roleId: string;
	places: RolePlace[];
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

type PresetChoice = PlacementPreset | "custom";

const PRESET_LABELS: Record<PresetChoice, string> = {
	selected: "Selected Organizations",
	customers: "All Customer Organizations",
	all: "All Organizations",
	custom: "Custom",
};

const PRESET_ICONS = {
	selected: Building2,
	customers: Building,
	all: Globe,
	custom: SlidersHorizontal,
} satisfies Record<PresetChoice, typeof Globe>;

/** Grant chips shown before "+N more". */
const GRANTS_SHOWN = 4;

/** "Read Agents, Read and Write Tables", or "none". */
function describePermissions(
	permissions: string[],
	catalog: Map<string, PermissionCatalogEntry>,
): string {
	return permissions.length > 0
		? permissions
				.map((permission) =>
					permissionDisplayName(
						permission,
						catalog.get(permissionParts(permission).domain),
					),
				)
				.join(", ")
		: "none";
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

/** A role's place as the access map shows it, labelled by `placeLabel`. */
function mapPlace(place: RolePlace, orgName: (id: string) => string): MapPlace {
	const organizationName =
		place.kind === "organization"
			? orgName(place.organization_id ?? "")
			: null;
	return {
		kind: place.kind,
		organization_id: place.organization_id,
		organization_name: organizationName,
		label: placeLabel(place.kind, organizationName ?? ""),
	};
}

/**
 * The platform-wide permissions a role holds when it is placed only on
 * selected organizations, where they do nothing.
 */
function platformWideAtSelected(
	places: RolePlace[],
	permissions: string[],
	catalog: Map<string, PermissionCatalogEntry>,
): PermissionCatalogEntry[] {
	if (
		places.length === 0 ||
		places.some((place) => place.kind !== "organization")
	)
		return [];
	const domains = new Set(
		permissions.map((permission) => permissionParts(permission).domain),
	);
	return [...domains].flatMap((domain) => {
		const entry = catalog.get(domain);
		return entry?.scope === "platform_wide" ? [entry] : [];
	});
}

/** Sections that appear after a choice arrive with the disclosure motion. */
function Reveal({
	animate,
	className,
	children,
}: {
	animate: boolean;
	className?: string;
	children: ReactNode;
}) {
	const reduceMotion = useReducedMotion();
	return (
		<motion.div
			initial={animate ? { opacity: 0, y: reduceMotion ? 0 : 4 } : false}
			animate={{ opacity: 1, y: 0 }}
			transition={{
				duration: motionSeconds("--bf-motion-disclosure"),
				ease: "easeOut",
			}}
			className={className}
		>
			{children}
		</motion.div>
	);
}

function GrantChips({
	roleName,
	permissions,
	catalog,
}: {
	roleName: string;
	permissions: string[];
	catalog: Map<string, PermissionCatalogEntry>;
}) {
	const [expanded, setExpanded] = useState(false);
	if (permissions.length === 0) return null;
	const shown = expanded ? permissions : permissions.slice(0, GRANTS_SHOWN);
	return (
		<div className="flex flex-wrap items-center gap-1.5">
			<ul aria-label={`What ${roleName} grants`} className="contents">
				{shown.map((permission) => (
					<li key={permission}>
						<GrantChip
							permission={permission}
							entry={catalog.get(
								permissionParts(permission).domain,
							)}
						/>
					</li>
				))}
			</ul>
			{permissions.length > GRANTS_SHOWN && (
				<button
					type="button"
					aria-expanded={expanded}
					className="min-h-8 rounded-[var(--bf-radius-control)] px-1.5 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					onClick={() => setExpanded(!expanded)}
				>
					{expanded
						? "Show Fewer"
						: `+${permissions.length - GRANTS_SHOWN} More`}
				</button>
			)}
		</div>
	);
}

/** A place a role applies, in the reach colours. */
function PlaceChip({ place }: { place: MapPlace }) {
	return (
		<Badge
			variant="secondary"
			data-place={place.kind}
			className="h-auto min-h-8 gap-1 whitespace-normal bg-[var(--bf-reach-soft)] py-1 text-sm text-[var(--bf-reach)]"
		>
			<PlaceLabel place={place} />
		</Badge>
	);
}

function PresetControl({
	roleName,
	presets,
	value,
	onChange,
}: {
	roleName: string;
	presets: PlacementPreset[];
	value: PresetChoice;
	onChange: (preset: PlacementPreset) => void;
}) {
	// Custom is shown, never chosen: it is a placement no preset describes.
	const choices: PresetChoice[] =
		value === "custom" ? [...presets, "custom"] : presets;
	return (
		<RadioGroupPrimitive.Root
			aria-label={`Placement for ${roleName}`}
			value={value}
			onValueChange={(next) => onChange(next as PlacementPreset)}
			className="grid gap-1 rounded-[var(--bf-radius-control)] border border-border/70 bg-muted/50 p-1 sm:inline-flex sm:flex-wrap"
		>
			{choices.map((choice) => {
				const Icon = PRESET_ICONS[choice];
				return (
					<RadioGroupPrimitive.Item
						key={choice}
						value={choice}
						disabled={choice === "custom"}
						className="inline-flex min-h-11 items-center gap-2 rounded-[calc(var(--bf-radius-control)-2px)] px-3 text-left text-sm font-medium text-muted-foreground transition-[color,background-color,box-shadow] duration-[var(--bf-motion-feedback)] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-default data-[state=checked]:bg-background data-[state=checked]:text-primary data-[state=checked]:shadow-sm motion-reduce:transition-none sm:min-h-8"
					>
						<Icon aria-hidden="true" className="size-4 shrink-0" />
						{PRESET_LABELS[choice]}
					</RadioGroupPrimitive.Item>
				);
			})}
		</RadioGroupPrimitive.Root>
	);
}

/**
 * Platform-wide permissions do nothing at selected organizations; say so.
 * Opens on click, tap, Enter or Space, so touch screens can read it too.
 */
function PlatformWideWarning({
	entries,
}: {
	entries: PermissionCatalogEntry[];
}) {
	return (
		<Popover>
			<PopoverTrigger asChild>
				<Badge asChild variant="warning">
					<button
						type="button"
						className="h-auto min-h-8 gap-1.5 whitespace-normal py-1 focus-visible:ring-2 focus-visible:ring-ring"
					>
						<AlertTriangle aria-hidden="true" />
						Some Permissions Need Global
					</button>
				</Badge>
			</PopoverTrigger>
			<PopoverContent
				align="end"
				collisionPadding={16}
				aria-label="Platform-Wide Permissions"
				className="w-72 max-w-[calc(100vw-2rem)] text-sm"
			>
				<ul className="space-y-1.5">
					{entries.map((entry) => (
						<li key={entry.domain}>
							{entry.title} is platform-wide; it applies only
							through a Global placement.
						</li>
					))}
				</ul>
			</PopoverContent>
		</Popover>
	);
}

interface AdditionalRoleCardProps {
	role: DraftRole;
	/** Where the role applies in saved state; undefined for a newly added role. */
	savedPlaces: RolePlace[] | undefined;
	info: RoleInfo | undefined;
	/** The role as the caller may grant it; undefined when they can't. */
	grantable: AssignableRole | undefined;
	permissions: string[];
	catalog: Map<string, PermissionCatalogEntry>;
	canEdit: boolean;
	providerOrgId: string;
	userOrganizationId: string | null | undefined;
	orgName: (id: string) => string;
	organizationChoices: (
		role: AssignableRole,
		places: RolePlace[],
	) => { value: string; label: string }[];
	homeIfAllowed: (role: AssignableRole) => string[];
	onRemove: () => void;
	onPlacesChange: (places: RolePlace[]) => void;
}

function organizationIds(places: RolePlace[] = []): string[] {
	return places.flatMap((place) =>
		place.kind === "organization" && place.organization_id
			? [place.organization_id]
			: [],
	);
}

/** One additional role: what it grants, and where it applies. */
function AdditionalRoleCard({
	role,
	savedPlaces,
	info,
	grantable,
	permissions,
	catalog,
	canEdit,
	providerOrgId,
	userOrganizationId,
	orgName,
	organizationChoices,
	homeIfAllowed,
	onRemove,
	onPlacesChange,
}: AdditionalRoleCardProps) {
	const name = info?.name ?? "Unknown Role";
	// Grantable but not as an additional role: the user holds a role they
	// could no longer be given (e.g. Platform Operator outside the provider
	// org). It can only be removed.
	const removable = canEdit && !!grantable;
	const editable = removable && !!grantable?.can_be_additional;
	// The server fixes where some roles apply; nobody picks.
	const fixedPlaces = !!grantable?.fixed_boundaries?.length;
	const presets = grantable ? offeredPresets(grantable) : [];
	const preset = grantable
		? presetFor(role.places, grantable, providerOrgId)
		: "custom";
	const savedPreset =
		savedPlaces && grantable
			? presetFor(savedPlaces, grantable, providerOrgId)
			: undefined;
	const showPresets =
		editable &&
		presets.length > 0 &&
		(presets.length > 1 || preset === "custom");
	const picksOrganizations = editable && preset === "selected";
	const platformWide = platformWideAtSelected(
		role.places,
		permissions,
		catalog,
	);

	/**
	 * Selected organizations keeps the organizations already chosen, else the
	 * saved ones, else the person's home organization.
	 */
	const choosePreset = (grantable: AssignableRole, next: PlacementPreset) => {
		const current = organizationIds(role.places);
		const before = organizationIds(savedPlaces);
		const selected =
			current.length > 0
				? current
				: before.length > 0
					? before
					: homeIfAllowed(grantable);
		onPlacesChange(
			placesForPreset(next, grantable, selected, providerOrgId),
		);
	};

	return (
		<Reveal
			animate={!savedPlaces}
			className="overflow-hidden rounded-[var(--bf-radius-surface)] border border-border/60 bg-muted/30"
		>
			<div className="flex items-start gap-2 p-3 sm:p-4">
				<div className="min-w-0 flex-1 space-y-2">
					<div className="space-y-1">
						<p className="flex flex-wrap items-center gap-2 font-medium [overflow-wrap:anywhere]">
							{name}
							{info?.is_builtin && (
								<Badge variant="outline">Built-in</Badge>
							)}
						</p>
						{info?.description && (
							<p className="text-xs leading-5 text-muted-foreground">
								{info.description}
							</p>
						)}
					</div>
					<GrantChips
						roleName={name}
						permissions={permissions}
						catalog={catalog}
					/>
				</div>
				{removable && (
					<Button
						type="button"
						variant="ghost"
						size="icon"
						className="h-11 w-11 shrink-0 sm:h-8 sm:w-8"
						aria-label={`Remove ${name}`}
						onClick={onRemove}
					>
						<X className="h-4 w-4" />
					</Button>
				)}
			</div>
			<div className="space-y-3 border-t border-border/60 px-3 py-3 sm:px-4">
				{fixedPlaces ? (
					<p className="text-xs text-muted-foreground">
						Applies everywhere.
					</p>
				) : (
					<>
						<div className="flex flex-wrap items-center justify-between gap-2">
							<span className="text-xs font-medium text-muted-foreground">
								Where It Applies
							</span>
							{platformWide.length > 0 && (
								<PlatformWideWarning entries={platformWide} />
							)}
						</div>
						{showPresets && grantable && (
							<PresetControl
								roleName={name}
								presets={presets}
								value={preset}
								onChange={(next) =>
									choosePreset(grantable, next)
								}
							/>
						)}
						<Reveal key={preset} animate={preset !== savedPreset}>
							{picksOrganizations && grantable ? (
								<MultiCombobox
									options={organizationChoices(
										grantable,
										role.places,
									)}
									value={organizationIds(role.places)}
									onValueChange={(ids) =>
										onPlacesChange(
											ids.map((id) => ({
												kind: "organization",
												organization_id: id,
											})),
										)
									}
									placeholder={`Select organizations for ${name}...`}
									searchPlaceholder="Search organizations..."
									emptyText="No organizations found."
								/>
							) : (
								<ul
									className="flex flex-wrap items-center gap-1.5"
									aria-label={`Where ${name} applies`}
								>
									{role.places.map((place) => (
										<li key={placeKey(place)}>
											<PlaceChip
												place={mapPlace(place, orgName)}
											/>
										</li>
									))}
								</ul>
							)}
						</Reveal>
					</>
				)}
				{role.places.length === 0 && (
					<p role="alert" className="text-xs text-destructive">
						Choose where {name} applies.
					</p>
				)}
				{canEdit && !editable && (
					<p className="text-xs text-muted-foreground">
						{role.roleId === PLATFORM_ADMIN_ROLE_ID &&
						!userOrganizationId
							? "Move this person into an organization before removing Platform Admin."
							: removable
								? "This person can't be given this role any more. You can remove it in Additional Roles, but not change where it applies."
								: "You can't change this role. Saving keeps it as it is."}
					</p>
				)}
			</div>
		</Reveal>
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
	const catalogQuery = usePermissionCatalog();

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

	const summary = authorization.authorization;
	const providerOrgId = summary?.provider_organization_id;
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
	const rolePermissions = useMemo(() => {
		const permissions = new Map<string, string[]>();
		if (!data) return permissions;
		for (const role of data.additional)
			permissions.set(role.role_id, role.permissions);
		for (const role of data.assignable_roles)
			permissions.set(role.id, role.permissions);
		return permissions;
	}, [data]);
	const catalog = useMemo(
		() =>
			new Map(
				(catalogQuery.data ?? []).map((entry) => [entry.domain, entry]),
			),
		[catalogQuery.data],
	);

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

	/**
	 * Organizations this role can be placed at, and the ones it already is
	 * (which may only be removed), for the Selected Organizations picker.
	 */
	const organizationChoices = (role: AssignableRole, places: RolePlace[]) => {
		if (!role.boundary_kinds.includes("organization")) return [];
		const ids = new Set((organizations ?? []).map((org) => org.id));
		if (user.organization_id) ids.add(user.organization_id);
		const placed = organizationIds(places);
		return [
			...[...ids].filter(
				(id) => orgAllowed(role, id) && !placed.includes(id),
			),
			...placed,
		]
			.map((id) => ({ value: id, label: orgName(id) }))
			.sort((a, b) => a.label.localeCompare(b.label));
	};

	const homeIfAllowed = (role: AssignableRole): string[] => {
		const home = user.organization_id;
		return home &&
			role.boundary_kinds.includes("organization") &&
			orgAllowed(role, home)
			? [home]
			: [];
	};

	const defaultPlaces = (role: AssignableRole): RolePlace[] => {
		if (role.fixed_boundaries?.length) {
			return role.fixed_boundaries.map((boundary) => ({
				kind: boundary.kind,
				organization_id: boundary.organization_id ?? null,
			}));
		}
		const home = homeIfAllowed(role);
		if (home.length > 0) {
			return [{ kind: "organization", organization_id: home[0] }];
		}
		if (role.boundary_kinds.includes("managed_organizations")) {
			return [{ kind: "managed_organizations", organization_id: null }];
		}
		if (role.boundary_kinds.includes("platform")) {
			return [{ kind: "platform", organization_id: null }];
		}
		const first = organizationChoices(role, [])[0];
		return first
			? [{ kind: "organization", organization_id: first.value }]
			: [];
	};

	const loading = (
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

	// Presets and the organization picker depend on the caller's summary.
	if (authorization.isLoading) return loading;

	if (!canRead || !summary) {
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
					Retry Roles
				</Button>
			</div>
		);
	}

	if (!data || !draft) return loading;

	const saved = draftFrom(data);
	const dirty = draftSignature(draft) !== draftSignature(saved);
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
	// The roles picker: what can be added, and what's held and may be
	// removed. A held role the caller can't grant isn't offered, so it stays.
	const roleOptions = data.assignable_roles
		.filter((r) => r.can_be_additional || takenRoleIds.has(r.id))
		.map((r) => ({
			value: r.id,
			label: r.name,
			description: r.description ?? undefined,
		}));
	const chooseRoles = (roleIds: string[]) => {
		const chosen = new Set(roleIds);
		setDraft({
			...draft,
			additional: [
				...draft.additional.filter((r) => chosen.has(r.roleId)),
				...roleIds.flatMap((id) => {
					const role = assignable.get(id);
					return role && !takenRoleIds.has(id)
						? [{ roleId: id, places: defaultPlaces(role) }]
						: [];
				}),
			],
		});
	};
	const holdsAdmin = (roles: { roleId: string }[]) =>
		roles.some((role) => role.roleId === PLATFORM_ADMIN_ROLE_ID);
	const promoting =
		holdsAdmin(draft.additional) && !holdsAdmin(saved.additional);
	const demoting =
		!holdsAdmin(draft.additional) && holdsAdmin(saved.additional);
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
			`they'll have only ${newCustomBase.name}'s permissions (${describePermissions(newCustomBase.permissions, catalog)}) ` +
			`instead of ${data.base_role.name}'s` +
			(savedBasePermissions
				? ` (${describePermissions(savedBasePermissions, catalog)}).`
				: " permissions.");

	const updateRole = (roleId: string, places: RolePlace[]) =>
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
			const result = await replace.mutateAsync({
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
			setLoadedFrom(result);
			setDraft(draftFrom(result));
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

	const baseRoleName = roleInfo.get(draft.baseRoleId)?.name ?? "Base Role";

	return (
		<>
			<div
				aria-busy={replace.isPending}
				inert={replace.isPending}
				className="space-y-6 px-4 py-4 sm:px-6 sm:py-5"
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
						Base Role
					</h3>
					<p
						id="base-role-help"
						className="text-xs leading-5 text-muted-foreground"
					>
						What this person can do in their own organization.
						Everyone has exactly one. A custom base role replaces
						the User role's defaults.
					</p>
					<div className="space-y-3 rounded-[var(--bf-radius-surface)] border border-border/60 bg-muted/30 p-3 sm:p-4">
						{canChangeBase ? (
							<Combobox
								id="base-role"
								aria-label="Base Role"
								aria-describedby="base-role-help"
								value={draft.baseRoleId}
								onValueChange={(value) =>
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
							<p className="font-medium">{data.base_role.name}</p>
						)}
						<GrantChips
							key={draft.baseRoleId}
							roleName={baseRoleName}
							permissions={
								rolePermissions.get(draft.baseRoleId) ?? []
							}
							catalog={catalog}
						/>
					</div>
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
							Additional Roles
						</h3>
						<p className="text-xs leading-5 text-muted-foreground">
							Extra access on top of the base role. Each one
							applies only where you choose.
						</p>
					</div>
					{canEdit && roleOptions.length > 0 && (
						<MultiCombobox
							options={roleOptions}
							value={draft.additional.map((r) => r.roleId)}
							onValueChange={chooseRoles}
							// Each chosen role is listed below with its places.
							showSelected={false}
							placeholder="Select additional roles..."
							searchPlaceholder="Search roles..."
							emptyText="No roles found."
						/>
					)}
					{draft.additional.length === 0 ? (
						<p className="rounded-[var(--bf-radius-surface)] border border-dashed border-border/70 p-4 text-sm text-muted-foreground">
							No additional roles.
						</p>
					) : (
						<ul className="space-y-3" aria-label="Additional Roles">
							{draft.additional.map((role) => (
								<li key={role.roleId}>
									<AdditionalRoleCard
										role={role}
										savedPlaces={
											saved.additional.find(
												(r) => r.roleId === role.roleId,
											)?.places
										}
										info={roleInfo.get(role.roleId)}
										grantable={assignable.get(role.roleId)}
										permissions={
											rolePermissions.get(role.roleId) ??
											[]
										}
										catalog={catalog}
										canEdit={canEdit}
										providerOrgId={
											summary.provider_organization_id
										}
										userOrganizationId={
											user.organization_id
										}
										orgName={orgName}
										organizationChoices={
											organizationChoices
										}
										homeIfAllowed={homeIfAllowed}
										onRemove={() =>
											chooseRoles(
												draft.additional
													.map((r) => r.roleId)
													.filter(
														(id) =>
															id !== role.roleId,
													),
											)
										}
										onPlacesChange={(places) =>
											updateRole(role.roleId, places)
										}
									/>
								</li>
							))}
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
				</section>
			</div>
			{canEdit && (
				<div className="flex flex-col-reverse gap-2 border-t border-border/70 px-4 py-4 sm:flex-row sm:items-center sm:justify-end sm:px-6">
					{dirty && (
						<p
							aria-live="polite"
							className="text-xs text-muted-foreground sm:mr-auto"
						>
							Unsaved Changes
						</p>
					)}
					<Button
						type="button"
						variant="outline"
						className="h-11"
						disabled={!dirty || replace.isPending}
						onClick={() => {
							setDraft(saved);
							setSaveError(null);
						}}
					>
						Discard Changes
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
						Save Roles
					</Button>
				</div>
			)}
		</>
	);
}
