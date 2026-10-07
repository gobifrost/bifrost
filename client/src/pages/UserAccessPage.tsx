import { useRef, type ReactNode } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import {
	ArrowLeft,
	Building2,
	ChevronLeft,
	FlaskConical,
	KeyRound,
	RefreshCw,
	ShieldAlert,
	UserRound,
} from "lucide-react";

import { AccessMap } from "@/components/access/AccessMap";
import { TestAccessPanel } from "@/components/access/TestAccessPanel";
import { IdentityActions } from "@/components/identities/IdentityActions";
import { IdentityGlyph } from "@/components/identities/IdentityGlyph";
import { IdentityOrganizationChip } from "@/components/identities/IdentityName";
import { IdentityProfileForm } from "@/components/identities/IdentityProfileForm";
import { ReachChip } from "@/components/access/ReachChip";
import { ListLoadError } from "@/components/layout/ListLoadError";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { RouteUnavailableState } from "@/components/layout/RouteUnavailableState";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
	SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { UserActionsMenu } from "@/components/users/UserActionsMenu";
import { UserProfileForm } from "@/components/users/UserProfileForm";
import { UserRoleAssignmentsPanel } from "@/components/users/UserRoleAssignmentsPanel";
import { UserStatusBadge } from "@/components/users/UserStatusBadge";
import { useUserAccountActions } from "@/components/users/useUserAccountActions";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useUser } from "@/hooks/useUsers";
import { ApiError } from "@/lib/api-error";
import { orgTarget } from "@/lib/authorization";
import { motionSeconds } from "@/lib/motion";
import { permissionDisplayName, permissionParts } from "@/lib/permission-words";
import type { components } from "@/lib/v1";
import type { IdentityKind } from "@/services/identities";
import {
	usePermissionCatalog,
	useUserAccessMap,
	type PermissionCatalogEntry,
	type UserAccessMap,
} from "@/services/access";
import { useAuthorization } from "@/services/authorization";

type User = components["schemas"]["UserPublic"];
type Tab = "access" | "profile";

const LIST = new Intl.ListFormat("en", { type: "conjunction" });

function initials(user: User): string {
	const name = user.name?.trim();
	if (!name) return user.email[0].toUpperCase();
	return name
		.split(/\s+/)
		.map((part) => part[0])
		.join("")
		.toUpperCase()
		.slice(0, 2);
}

/** "Protected: holds …" — what makes this account protected. */
function protectedSummary(
	map: UserAccessMap | undefined,
	catalog: PermissionCatalogEntry[] | undefined,
): string {
	const entryFor = (permission: string) =>
		catalog?.find(
			(entry) => entry.domain === permissionParts(permission).domain,
		);
	const held =
		map && catalog
			? LIST.format(
					map.privileged_permissions.map((permission) =>
						permissionDisplayName(permission, entryFor(permission)),
					),
				)
			: "privileged access";
	return `Protected: holds ${held}. Only a Platform Admin can change this account.`;
}

/** Tab content arrives with the disclosure motion; reduced motion only fades. */
function Disclosure({ children }: { children: ReactNode }) {
	const reduceMotion = useReducedMotion();
	return (
		<motion.div
			initial={{ opacity: 0, y: reduceMotion ? 0 : 6 }}
			animate={{ opacity: 1, y: 0 }}
			transition={{
				duration: motionSeconds("--bf-motion-disclosure"),
				ease: "easeOut",
			}}
			className="space-y-8"
		>
			{children}
		</motion.div>
	);
}

function BackToUsers() {
	return (
		<Button variant="outline" className="min-h-11" asChild>
			<Link to="/users">
				<ArrowLeft aria-hidden="true" className="size-4" />
				Back to Users
			</Link>
		</Button>
	);
}

function UserUnavailable({
	error,
	retrying,
	onRetry,
}: {
	error: unknown;
	retrying: boolean;
	onRetry: () => void;
}) {
	const status = error instanceof ApiError ? error.statusCode : undefined;
	if (status === 403)
		return (
			<RouteUnavailableState
				title="You can't view this person"
				description="Your roles don't reach the organization they belong to."
			>
				<BackToUsers />
			</RouteUnavailableState>
		);
	if (status === 404 || !error)
		return (
			<RouteUnavailableState
				title="User Not Found"
				description="They may have been deleted, or the link is out of date."
			>
				<BackToUsers />
			</RouteUnavailableState>
		);
	return (
		<RouteUnavailableState
			title="Couldn't load this person"
			description={
				error instanceof Error
					? error.message
					: "The user could not be loaded."
			}
		>
			<BackToUsers />
			<Button
				type="button"
				className="min-h-11"
				disabled={retrying}
				onClick={onRetry}
			>
				<RefreshCw aria-hidden="true" className="size-4" />
				Try Again
			</Button>
		</RouteUnavailableState>
	);
}

function PersonHeader({
	user,
	identityKind,
	homeOrganization,
	map,
	catalog,
	actions,
}: {
	user: User;
	/** Set for an identity, which shows a glyph and its organization chip instead of an email. */
	identityKind: IdentityKind | null;
	homeOrganization: string | undefined;
	map: UserAccessMap | undefined;
	catalog: PermissionCatalogEntry[] | undefined;
	actions: ReactNode;
}) {
	// An identity's organization chip: null for Global, unknown until loaded.
	const identityOrganization = user.organization_id ? homeOrganization : null;
	return (
		<header className="min-w-0 space-y-3">
			<Link
				to={identityKind ? "/users/identities" : "/users"}
				className="inline-flex min-h-11 items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
			>
				<ChevronLeft className="size-4" aria-hidden="true" />
				{identityKind ? "Identities" : "Users"}
			</Link>
			<div className="flex min-w-0 items-start gap-4">
				{identityKind ? (
					<IdentityGlyph />
				) : (
					<Avatar className="h-12 w-12 sm:h-14 sm:w-14">
						<AvatarFallback className="font-display text-base font-semibold text-foreground sm:text-lg">
							{initials(user)}
						</AvatarFallback>
					</Avatar>
				)}
				<div className="min-w-0 flex-1 space-y-3">
					<div className="space-y-1">
						<h1 className="flex flex-wrap items-center gap-2 font-display text-2xl font-semibold tracking-tight [overflow-wrap:anywhere] sm:text-3xl">
							{user.name || user.email}
							{identityKind &&
								identityOrganization !== undefined && (
									<IdentityOrganizationChip
										organizationName={identityOrganization}
									/>
								)}
							{!user.is_active && (
								<Badge variant="outline">Disabled</Badge>
							)}
							{user.invite_status &&
								user.invite_status !== "active" && (
									<UserStatusBadge
										status={user.invite_status}
									/>
								)}
						</h1>
						{!identityKind && (
							<p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
								<span className="[overflow-wrap:anywhere]">
									{user.email}
								</span>
								{homeOrganization && (
									<span className="inline-flex items-center gap-1.5">
										<Building2
											aria-hidden="true"
											className="size-3.5"
										/>
										{homeOrganization}
									</span>
								)}
							</p>
						)}
					</div>
					{map && (
						<div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
							<span
								id="person-reach"
								className="text-xs font-medium text-muted-foreground"
							>
								Reach
							</span>
							<ul
								aria-labelledby="person-reach"
								className="flex min-w-0 max-w-full flex-wrap gap-1.5"
							>
								{map.reach.map((place) => (
									<li
										key={`${place.kind}:${place.organization_id}`}
										className="min-w-0 max-w-full"
									>
										<ReachChip place={place} />
									</li>
								))}
							</ul>
						</div>
					)}
					{user.is_protected && (
						<p className="flex items-start gap-2 text-sm text-[var(--bf-warning)]">
							<ShieldAlert
								aria-hidden="true"
								className="mt-0.5 size-4 shrink-0"
							/>
							<span>{protectedSummary(map, catalog)}</span>
						</p>
					)}
				</div>
				{actions}
			</div>
		</header>
	);
}

function SectionHeading({
	id,
	title,
	description,
	action,
}: {
	id: string;
	title: string;
	description: string;
	action?: ReactNode;
}) {
	return (
		<div className="flex flex-wrap items-start justify-between gap-3">
			<div className="min-w-0 flex-1 basis-64 space-y-1">
				<h2 id={id} className="text-base font-semibold">
					{title}
				</h2>
				<p className="text-sm text-muted-foreground">{description}</p>
			</div>
			{action}
		</div>
	);
}

/** "Test Access": what the access model would decide for this person, in a sheet. */
function TestAccessSheet({ person }: { person: User }) {
	return (
		<Sheet>
			<SheetTrigger asChild>
				<Button
					type="button"
					variant="outline"
					className="min-h-11 sm:min-h-9"
				>
					<FlaskConical aria-hidden="true" className="size-4" />
					Test Access
				</Button>
			</SheetTrigger>
			<SheetContent className="sm:max-w-xl">
				<SheetHeader className="border-b border-border/70">
					<SheetTitle>Test Access</SheetTitle>
					<SheetDescription>
						What the access model would decide for{" "}
						{person.name || person.email}. Report-only: nothing is
						recorded or enforced.
					</SheetDescription>
				</SheetHeader>
				<div className="min-h-0 overflow-auto p-6">
					<TestAccessPanel
						subjectId={person.id}
						defaultOrganizationId={
							person.organization_id ?? "global"
						}
					/>
				</div>
			</SheetContent>
		</Sheet>
	);
}

/**
 * One person: who they are, where their access reaches, and what they can
 * do there (`access`), with their profile one tab over (`profile`).
 */
export function UserAccessPage() {
	const { userId, tab } = useParams<{ userId: string; tab?: string }>();
	const navigate = useNavigate();
	const authorization = useAuthorization();
	const { user: currentUser } = useAuth();
	const userQuery = useUser(userId);
	const person = userQuery.data;
	const canViewAccess =
		!!person &&
		authorization.canAt(
			"roleassignments.read",
			orgTarget(person.organization_id),
		);
	const accessQuery = useUserAccessMap(canViewAccess ? userId : undefined);
	const catalogQuery = usePermissionCatalog();
	const organizationsQuery = useOrganizations({
		enabled: authorization.canAnywhere("organizations.read"),
	});
	const actionsButtonRef = useRef<HTMLButtonElement>(null);
	const accountActions = useUserAccountActions({
		returnFocusRef: actionsButtonRef,
		onDeleted: () => navigate("/users"),
	});

	if (userQuery.isLoading || authorization.isLoading)
		return (
			<div
				role="status"
				aria-label="Loading user"
				className="mx-auto w-full max-w-7xl space-y-4"
			>
				<Skeleton className="h-14 w-80 max-w-full" />
				<Skeleton className="h-6 w-96 max-w-full" />
				<Skeleton className="h-72 w-full" />
			</div>
		);
	if (!person)
		return (
			<UserUnavailable
				error={userQuery.error}
				retrying={userQuery.isFetching}
				onRetry={() => void userQuery.refetch()}
			/>
		);

	// Without roleassignments.read the profile is the only tab, so its address
	// is the only one that shows it.
	if (!canViewAccess && tab !== "profile")
		return <Navigate to={`/users/${person.id}/profile`} replace />;

	const map = accessQuery.data;
	// The server's IdentityKind enum; null for people.
	const identityKind = (person.identity_kind ?? null) as IdentityKind | null;
	const sectionCopy = identityKind
		? {
				access: "What this identity can do, in each organization its roles reach. Hover a permission to see which role grants it.",
				roles:
					identityKind === "custom"
						? "The base role applies in its home organization; additional roles apply where they're placed."
						: "A default identity's base role is fixed; additional roles apply where they're placed.",
			}
		: {
				access: "What this person can do, in each organization their roles reach. Hover a permission to see which role grants it.",
				roles: "The base role applies in their home organization; additional roles apply where they're placed.",
			};
	const currentTab: Tab = tab === "profile" ? "profile" : "access";
	const homeOrganization = person.organization_id
		? (map?.home_organization?.name ??
			organizationsQuery.data?.find(
				(org) => org.id === person.organization_id,
			)?.name)
		: "Global (No Organization)";

	return (
		<PageWorkspace className="mx-auto w-full max-w-7xl gap-5">
			<PersonHeader
				user={person}
				identityKind={identityKind}
				homeOrganization={homeOrganization}
				map={map}
				catalog={catalogQuery.data}
				actions={
					identityKind ? (
						<IdentityActions
							identity={person}
							onDeleted={() => navigate("/users/identities")}
						/>
					) : (
						// The Profile tab is the profile editor, so no Edit Profile.
						<UserActionsMenu
							label={`${person.name || person.email} actions`}
							triggerRef={actionsButtonRef}
							{...accountActions.menuPropsFor(person)}
						/>
					)
				}
			/>
			<Tabs
				value={currentTab}
				onValueChange={(next) =>
					navigate(`/users/${person.id}/${next}`)
				}
				className="flex min-h-0 flex-1 flex-col gap-4"
			>
				<TabsList variant="line" className="w-full justify-start">
					{canViewAccess && (
						<TabsTrigger
							value="access"
							className="min-h-11 flex-none px-3"
						>
							<KeyRound aria-hidden="true" />
							Access
						</TabsTrigger>
					)}
					<TabsTrigger
						value="profile"
						className="min-h-11 flex-none px-3"
					>
						<UserRound aria-hidden="true" />
						Profile
					</TabsTrigger>
				</TabsList>
				{canViewAccess && (
					<TabsContent
						value="access"
						className="flex min-h-0 flex-1 flex-col"
					>
						<PageScrollArea className="lg:overflow-auto">
							<Disclosure>
								<section
									aria-labelledby="access-map-heading"
									className="space-y-3"
								>
									<SectionHeading
										id="access-map-heading"
										title="Effective Access"
										description={sectionCopy.access}
										action={
											<TestAccessSheet person={person} />
										}
									/>
									{map && catalogQuery.data ? (
										<AccessMap
											rows={map.rows}
											catalog={catalogQuery.data}
										/>
									) : accessQuery.isError ||
									  catalogQuery.isError ? (
										<ListLoadError
											resource="their access"
											hasCachedData={false}
											isRetrying={
												accessQuery.isFetching ||
												catalogQuery.isFetching
											}
											onRetry={() => {
												if (accessQuery.isError)
													void accessQuery.refetch();
												if (catalogQuery.isError)
													void catalogQuery.refetch();
											}}
										/>
									) : (
										<Skeleton
											role="status"
											aria-label="Loading access"
											className="h-40 w-full"
										/>
									)}
								</section>
								<section
									aria-labelledby="role-assignments-heading"
									className="space-y-3"
								>
									<SectionHeading
										id="role-assignments-heading"
										title="Role Assignments"
										description={sectionCopy.roles}
									/>
									<div className="flex flex-col overflow-hidden rounded-[var(--bf-radius-surface)] border bg-card">
										<UserRoleAssignmentsPanel
											user={person}
											isSelf={
												currentUser?.id === person.id
											}
										/>
									</div>
								</section>
							</Disclosure>
						</PageScrollArea>
					</TabsContent>
				)}
				<TabsContent
					value="profile"
					className="flex min-h-0 flex-1 flex-col"
				>
					<PageScrollArea className="lg:overflow-auto">
						<Disclosure>
							{identityKind ? (
								<IdentityProfileForm
									key={person.id}
									identity={person}
								/>
							) : (
								<UserProfileForm
									key={person.id}
									user={person}
									variant="page"
								/>
							)}
						</Disclosure>
					</PageScrollArea>
				</TabsContent>
			</Tabs>
			{accountActions.dialogs}
		</PageWorkspace>
	);
}
