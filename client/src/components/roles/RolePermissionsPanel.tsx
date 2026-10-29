import { useState, type ReactNode } from "react";
import { AlertCircle, Loader2, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { SearchBox } from "@/components/search/SearchBox";
import { Skeleton } from "@/components/ui/skeleton";
import { useRolePermissions, useUpdateRolePermissions } from "@/hooks/useRoles";
import { getErrorMessage } from "@/lib/api-error";
import { PLATFORM_ADMIN_ROLE_ID } from "@/lib/builtin-roles";
import { permissionDisplayName, permissionParts } from "@/lib/permission-words";
import {
	usePermissionCatalog,
	type PermissionCatalogEntry,
} from "@/services/access";
import { useAuthorization } from "@/services/authorization";
import type { components } from "@/lib/v1";

type PermissionItem = components["schemas"]["RolePermissionItem"];

type Level = "none" | "read" | "readwrite";

function levelOf(domain: string, held: Set<string>): Level {
	if (held.has(`${domain}.readwrite`)) return "readwrite";
	if (held.has(`${domain}.read`)) return "read";
	return "none";
}

function permissionsFor(domain: string, level: Level, vocabulary: Set<string>) {
	const read = `${domain}.read`;
	const readwrite = `${domain}.readwrite`;
	if (level === "none") return [];
	if (level === "read") return [read];
	// Changing includes viewing wherever the domain has a view permission.
	return vocabulary.has(read) ? [read, readwrite] : [readwrite];
}

const NO_ACCESS = "No Access";

/** "No Access", or the name of the level's permission ("Read and Write Roles"). */
function levelLabel(entry: PermissionCatalogEntry, level: Level) {
	return level === "none"
		? NO_ACCESS
		: permissionDisplayName(`${entry.domain}.${level}`, entry);
}

function matchesSearch(entry: PermissionCatalogEntry, term: string) {
	const needle = term.trim().toLowerCase();
	return (
		!needle ||
		[entry.title, entry.domain, entry.description].some((text) =>
			text.toLowerCase().includes(needle),
		)
	);
}

/** Catalog areas in catalog order, the ones with identity choices first. */
function areasOf(
	entries: PermissionCatalogEntry[],
	identityDomains: Set<string>,
) {
	const areas = new Map<string, PermissionCatalogEntry[]>();
	for (const entry of entries) {
		areas.set(entry.area, [...(areas.get(entry.area) ?? []), entry]);
	}
	const hasIdentity = (list: PermissionCatalogEntry[]) =>
		list.some((entry) => identityDomains.has(entry.domain));
	return [...areas.entries()]
		.map(([area, list]) => ({ area, entries: list }))
		.sort(
			(a, b) =>
				Number(hasIdentity(b.entries)) - Number(hasIdentity(a.entries)),
		);
}

const domainId = (domain: string) => `permission-${domain}`;
const areaId = (area: string) =>
	`permission-area-${area.toLowerCase().replace(/[^a-z]+/g, "-")}`;

function PrivilegedMark() {
	return (
		<Badge variant="warning" className="gap-1">
			<ShieldAlert aria-hidden="true" className="size-3" />
			Privileged
		</Badge>
	);
}

/** Muted note for domains the evaluator does not enforce yet. */
function TakesEffectLater() {
	return (
		<span className="rounded-full border border-border px-2 py-0.5 text-xs font-normal text-muted-foreground">
			Takes Effect with R3b
		</span>
	);
}

/** Catalog prose, with `backticked` names set as code. */
function Description({ text }: { text: string }) {
	return (
		<p className="text-xs leading-5 text-muted-foreground">
			{text.split("`").map((part, index) =>
				index % 2 === 1 ? (
					<code
						key={index}
						className="rounded bg-muted/50 px-1 font-mono text-[0.92em]"
					>
						{part}
					</code>
				) : (
					part
				),
			)}
		</p>
	);
}

/** One catalog domain: what it covers, and what the role holds in it. */
function DomainRow({
	entry,
	noteLater,
	children,
}: {
	entry: PermissionCatalogEntry;
	/** Show the R3b note on this row; false when the area header carries it. */
	noteLater: boolean;
	children: ReactNode;
}) {
	return (
		<li>
			<div
				role="group"
				aria-labelledby={domainId(entry.domain)}
				className="grid gap-3 p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start sm:gap-6"
			>
				<div className="min-w-0 space-y-1">
					<div className="flex flex-wrap items-center gap-2">
						<h3
							id={domainId(entry.domain)}
							className="text-sm font-medium"
						>
							{entry.title}
						</h3>
						{noteLater && <TakesEffectLater />}
					</div>
					<Description text={entry.description} />
				</div>
				{children}
			</div>
		</li>
	);
}

/** What a role holds in a domain it can't change here, by name. */
function HeldValue({
	label,
	privileged,
}: {
	label: string;
	privileged: boolean;
}) {
	return (
		<p className="flex flex-wrap items-center gap-2 text-sm sm:justify-end">
			<span
				className={label === NO_ACCESS ? "text-muted-foreground" : ""}
			>
				{label}
			</span>
			{privileged && <PrivilegedMark />}
		</p>
	);
}

/**
 * Every permission domain in the catalog, grouped by area and searchable,
 * with what the role holds in each. Identity domains are a choice wherever
 * the server says they are editable; saving replaces only the identity
 * permissions and the server keeps the rest.
 */
export function RolePermissionsPanel({
	roleId,
	isBuiltin,
}: {
	roleId: string;
	isBuiltin: boolean;
}) {
	const authorization = useAuthorization();
	const query = useRolePermissions(roleId);
	const catalogQuery = usePermissionCatalog();
	const update = useUpdateRolePermissions();
	const data = query.data;
	const catalog = catalogQuery.data;
	const [draft, setDraft] = useState<Set<string> | null>(null);
	const [saveError, setSaveError] = useState<string | null>(null);
	const [search, setSearch] = useState("");

	const canEdit =
		!isBuiltin &&
		authorization.meets({ permission: "roles.readwrite", at: "global" });
	const isPlatformAdminRole = isBuiltin && roleId === PLATFORM_ADMIN_ROLE_ID;

	if ((query.isError && !data) || (catalogQuery.isError && !catalog)) {
		return (
			<div className="space-y-3">
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						{getErrorMessage(
							query.error ?? catalogQuery.error,
							"Permissions could not be loaded.",
						)}
					</AlertDescription>
				</Alert>
				<Button
					variant="outline"
					className="min-h-11"
					disabled={query.isFetching || catalogQuery.isFetching}
					onClick={() => {
						if (query.isError) void query.refetch();
						if (catalogQuery.isError) void catalogQuery.refetch();
					}}
				>
					Retry Permissions
				</Button>
			</div>
		);
	}
	if (!data || !catalog) {
		return (
			<div
				role="status"
				aria-label="Loading permissions"
				className="space-y-3"
			>
				<Skeleton className="h-20 w-full" />
				<Skeleton className="h-20 w-full" />
			</div>
		);
	}

	const vocabulary = new Set(
		data.identity_permissions.map((p) => p.permission),
	);
	const editableVocabulary = new Set(
		data.identity_permissions
			.filter((p) => p.editable)
			.map((p) => p.permission),
	);
	const identityDomains = new Set(
		[...vocabulary].map((permission) => permissionParts(permission).domain),
	);
	const privileged = new Set(
		[...data.permissions, ...data.identity_permissions]
			.filter((p) => p.privileged)
			.map((p) => p.permission),
	);
	const savedIdentity = new Set(
		data.permissions
			.map((p) => p.permission)
			.filter((permission) => vocabulary.has(permission)),
	);
	const selected = draft ?? savedIdentity;
	const heldByDomain = new Map<string, PermissionItem[]>();
	for (const held of data.permissions) {
		const { domain } = permissionParts(held.permission);
		heldByDomain.set(domain, [...(heldByDomain.get(domain) ?? []), held]);
	}
	// Areas where nothing is enforced yet say so once, on the area.
	const laterAreas = new Set<string>(
		[...new Set(catalog.map((entry) => entry.area))].filter((area) =>
			catalog
				.filter((entry) => entry.area === area)
				.every((entry) => !entry.enforced),
		),
	);
	const areas = areasOf(
		catalog.filter((entry) => matchesSearch(entry, search)),
		identityDomains,
	);
	const dirty =
		draft !== null &&
		(draft.size !== savedIdentity.size ||
			[...draft].some((permission) => !savedIdentity.has(permission)));

	const setLevel = (domain: string, level: Level) => {
		const next = new Set(
			[...selected].filter(
				(permission) =>
					permission !== `${domain}.read` &&
					permission !== `${domain}.readwrite`,
			),
		);
		for (const permission of permissionsFor(domain, level, vocabulary)) {
			next.add(permission);
		}
		setDraft(next);
	};

	const handleSave = async () => {
		if (!dirty || update.isPending) return;
		setSaveError(null);
		try {
			await update.mutateAsync({
				params: { path: { role_id: roleId } },
				body: { permissions: [...selected].sort() },
			});
			setDraft(null);
			toast.success("Permissions saved");
		} catch (error) {
			setSaveError(
				getErrorMessage(error, "Permissions could not be saved"),
			);
		}
	};

	const identityControl = (entry: PermissionCatalogEntry) => {
		const { domain } = entry;
		const level = levelOf(domain, selected);
		const choices: Level[] = [
			"none",
			...(["read", "readwrite"] as const).filter((choice) =>
				vocabulary.has(`${domain}.${choice}`),
			),
		];
		const isPrivileged = (choice: Level) =>
			permissionsFor(domain, choice, vocabulary).some((p) =>
				privileged.has(p),
			);
		// A domain is a choice only when the server accepts every change to it.
		const editable =
			canEdit &&
			[...vocabulary]
				.filter(
					(permission) =>
						permissionParts(permission).domain === domain,
				)
				.every((permission) => editableVocabulary.has(permission));
		if (!editable)
			return (
				<HeldValue
					label={levelLabel(entry, level)}
					privileged={isPrivileged(level)}
				/>
			);
		return (
			<RadioGroup
				value={level}
				onValueChange={(value) => setLevel(domain, value as Level)}
				className="flex flex-wrap gap-x-6 gap-y-1 sm:justify-end"
				aria-label={entry.title}
			>
				{choices.map((choice) => {
					const id = `${domain}-${choice}`;
					return (
						<div
							key={choice}
							className="flex min-h-11 items-center gap-2"
						>
							<RadioGroupItem id={id} value={choice} />
							<Label htmlFor={id}>
								{levelLabel(entry, choice)}
							</Label>
							{isPrivileged(choice) && <PrivilegedMark />}
						</div>
					);
				})}
			</RadioGroup>
		);
	};

	// Every held permission is listed: none implies another (Read and Write
	// does not include Read, Read All does not include Read).
	const heldControl = (entry: PermissionCatalogEntry) => {
		const held = heldByDomain.get(entry.domain) ?? [];
		return (
			<HeldValue
				label={
					held.length > 0
						? held
								.map((p) =>
									permissionDisplayName(p.permission, entry),
								)
								.join(", ")
						: NO_ACCESS
				}
				privileged={held.some((p) => p.privileged)}
			/>
		);
	};

	return (
		<div className="space-y-6">
			{!isBuiltin && !canEdit && (
				<p className="text-sm text-muted-foreground">
					You can view this role's permissions but not change them.
				</p>
			)}
			<p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
				<PrivilegedMark />
				Anyone holding a privileged permission becomes a protected
				account: only a Platform Admin can change their profile,
				sign-in, or roles.
			</p>

			{isPlatformAdminRole ? (
				<p className="rounded-[var(--bf-radius-surface)] border border-border/70 p-4 text-sm">
					Platform Admin holds every permission except reading
					secrets, which always takes an explicit role.
				</p>
			) : (
				<div className="space-y-6">
					<SearchBox
						value={search}
						onChange={setSearch}
						aria-label="Search permissions"
						placeholder="Search permissions..."
						className="w-full sm:max-w-md"
					/>
					{areas.length === 0 ? (
						<p className="text-sm text-muted-foreground">
							No permissions match your search.
						</p>
					) : (
						areas.map(({ area, entries }) => (
							<section
								key={area}
								aria-labelledby={areaId(area)}
								className="space-y-2"
							>
								<div className="flex flex-wrap items-center gap-2">
									<h2
										id={areaId(area)}
										className="text-base font-semibold"
									>
										{area}
									</h2>
									{laterAreas.has(area) && (
										<TakesEffectLater />
									)}
								</div>
								<ul className="divide-y divide-border/70 rounded-[var(--bf-radius-surface)] border border-border/70">
									{entries.map((entry) => (
										<DomainRow
											key={entry.domain}
											entry={entry}
											noteLater={
												!entry.enforced &&
												!laterAreas.has(area)
											}
										>
											{identityDomains.has(entry.domain)
												? identityControl(entry)
												: heldControl(entry)}
										</DomainRow>
									))}
								</ul>
							</section>
						))
					)}
				</div>
			)}

			{canEdit && (
				<div className="space-y-3">
					{saveError && (
						<Alert variant="destructive">
							<AlertCircle className="h-4 w-4" />
							<AlertDescription>{saveError}</AlertDescription>
						</Alert>
					)}
					<div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
						<Button
							variant="outline"
							className="min-h-11"
							disabled={!dirty || update.isPending}
							onClick={() => {
								setDraft(null);
								setSaveError(null);
							}}
						>
							Discard Changes
						</Button>
						<Button
							className="min-h-11"
							disabled={!dirty || update.isPending}
							onClick={() => void handleSave()}
						>
							{update.isPending && (
								<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
							)}
							Save Permissions
						</Button>
					</div>
				</div>
			)}
		</div>
	);
}
