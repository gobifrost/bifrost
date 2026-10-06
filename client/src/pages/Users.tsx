import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
	Crown,
	RefreshCw,
	UserCog,
	Plus,
	Star,
	Building2,
	ArrowUp,
	ArrowDown,
} from "lucide-react";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableFooter,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { SearchBox } from "@/components/search/SearchBox";
import { useUsersPage } from "@/hooks/useUsers";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useUserSelection } from "@/hooks/useUserSelection";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useAuth } from "@/contexts/AuthContext";
import { useOrgScope } from "@/contexts/OrgScopeContext";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { CreateUserDialog } from "@/components/users/CreateUserDialog";
import { EditUserDialog } from "@/components/users/EditUserDialog";
import { profileAbilities } from "@/components/users/UserProfileForm";
import {
	PROTECTED_ACCOUNT_NOTICE,
	UserActionsMenu,
} from "@/components/users/UserActionsMenu";
import { useUserAccountActions } from "@/components/users/useUserAccountActions";
import { UserStatusBadge } from "@/components/users/UserStatusBadge";
import { Badge } from "@/components/ui/badge";
import { BulkActionBar } from "@/components/users/BulkActionBar";
import { UserEmailCell } from "@/components/users/UserEmailCell";
import { UsersViewTabs } from "@/components/users/UsersViewTabs";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import {
	BulkMoveOrgDialog,
	BulkReplaceRolesDialog,
	BulkResultDialog,
	BulkSetActiveDialog,
} from "@/components/users/BulkUserDialogs";
import { orgTarget } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";
import { ListPagination } from "@/components/pagination/ListPagination";
import type { components, components as v1 } from "@/lib/v1";
type User = components["schemas"]["UserPublic"];
type Organization = components["schemas"]["OrganizationPublic"];

type SortColumn = "name" | "email" | "status" | "created" | "last_login";
type SortDirection = "asc" | "desc";
const PAGE_SIZE = 25;

function SortIcon({
	column,
	sortColumn,
	sortDirection,
}: {
	column: SortColumn;
	sortColumn: SortColumn;
	sortDirection: SortDirection;
}) {
	if (sortColumn !== column) return null;
	return sortDirection === "asc" ? (
		<ArrowUp className="inline ml-1 h-3 w-3" />
	) : (
		<ArrowDown className="inline ml-1 h-3 w-3" />
	);
}

function ProtectedBadge() {
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<Badge variant="warning" className="shrink-0 text-xs">
					Protected
				</Badge>
			</TooltipTrigger>
			<TooltipContent>{PROTECTED_ACCOUNT_NOTICE}</TooltipContent>
		</Tooltip>
	);
}

export function Users() {
	const createUserButtonRef = useRef<HTMLButtonElement>(null);
	const navigate = useNavigate();
	const isNarrow = useMediaQuery("(max-width: 1023px)");
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const [searchTerm, setSearchTerm] = useState("");
	const [showDisabled, setShowDisabled] = useState(false);
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [profileUser, setProfileUser] = useState<User | undefined>();
	const [sortColumn, setSortColumn] = useState<SortColumn>("name");
	const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
	const [offset, setOffset] = useState(0);

	const { scope } = useOrgScope();
	const { user: currentUser } = useAuth();
	const authorization = useAuthorization();
	const canSeeOrganizations = authorization.canAnywhere("organizations.read");

	const usersQuery = useUsersPage({
		scope: canSeeOrganizations ? filterOrgId : undefined,
		includeInactive: showDisabled,
		search: searchTerm,
		sortBy: sortColumn,
		sortDirection,
		limit: PAGE_SIZE,
		offset,
	});
	const users = usersQuery.data?.items ?? [];
	const total = usersQuery.data?.total ?? 0;
	const accountActions = useUserAccountActions({
		returnFocusRef: createUserButtonRef,
	});

	const { data: organizations } = useOrganizations({
		enabled: canSeeOrganizations,
	});

	const getOrgInfo = (
		orgId: string | null | undefined,
	): { name: string; isProvider: boolean } => {
		if (!orgId) return { name: "Platform", isProvider: false };
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return {
			name:
				org?.name ||
				(organizations
					? "Unknown organization"
					: canSeeOrganizations
						? "Loading…"
						: "Not visible to you"),
			isProvider: org?.is_provider ?? false,
		};
	};

	// Bulk operations follow the organization filter (anywhere when it shows
	// every organization); the server still decides each user and reports
	// per-user failures.
	const canBulk = (permission: string) =>
		filterOrgId
			? authorization.canAt(permission, orgTarget(filterOrgId))
			: authorization.canAnywhere(permission);
	const bulkAbilities = {
		canMoveOrg: canBulk("users.lifecycle.readwrite"),
		// The replace-roles dialog lists every role, which needs roles.read.
		canReplaceRoles:
			canBulk("roleassignments.readwrite") &&
			authorization.meets({ permission: "roles.read", at: "global" }),
		canSetActive: canBulk("users.readwrite"),
	};
	const showSelection =
		bulkAbilities.canMoveOrg ||
		bulkAbilities.canReplaceRoles ||
		bulkAbilities.canSetActive;

	const handleSort = (column: SortColumn) => {
		if (sortColumn === column) {
			setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
		} else {
			setSortColumn(column);
			setSortDirection("asc");
		}
		setOffset(0);
	};

	// ===== Bulk selection + actions =====
	const disabledSelectionIds = useMemo(
		() => (currentUser ? [currentUser.id] : []),
		[currentUser],
	);
	const selection = useUserSelection(users, disabledSelectionIds);

	type BulkMode = "move_org" | "replace_roles" | "disable" | "enable" | null;
	const [bulkMode, setBulkMode] = useState<BulkMode>(null);
	const [bulkResult, setBulkResult] = useState<
		v1["schemas"]["BulkUserResponse"] | null
	>(null);
	const [bulkResultUsers, setBulkResultUsers] = useState<User[]>([]);

	const activeMix: "all_active" | "all_inactive" | "mixed" = useMemo(() => {
		const selected = selection.selectedItems;
		if (selected.length === 0) return "all_active";
		const anyActive = selected.some((u) => u.is_active);
		const anyInactive = selected.some((u) => !u.is_active);
		if (anyActive && anyInactive) return "mixed";
		return anyActive ? "all_active" : "all_inactive";
	}, [selection.selectedItems]);

	const handlePartialFailure = (
		result: v1["schemas"]["BulkUserResponse"],
		opUsers: User[],
	) => {
		setBulkResult(result);
		setBulkResultUsers(opUsers);
	};

	// Cancel/dismiss: just close the dialog, keep the selection so the user
	// can pivot to a different action without re-ticking every row. Selection
	// is cleared via `onSuccess` (below) only after a successful submit.
	const closeBulk = () => {
		setBulkMode(null);
	};

	const onBulkSuccess = () => {
		selection.clear();
	};

	const handleOpenUser = (user: User) => {
		navigate(`/users/${user.id}`);
	};

	const isSelf = (user: User) =>
		!!(currentUser && user.id === currentUser.id);

	const renderUserActions = (user: User) => (
		<UserActionsMenu
			label={`${user.name || user.email} actions`}
			{...accountActions.menuPropsFor(user)}
			onEditProfile={
				profileAbilities(user, isSelf(user), authorization).canSave
					? () => setProfileUser(user)
					: undefined
			}
		/>
	);

	return (
		<PageWorkspace className="max-w-7xl mx-auto">
			<ListPageHeader
				title="Users"
				titleSlot={<UsersViewTabs />}
				description={
					scope.type !== "global"
						? `Users for ${scope.orgName}`
						: authorization.isPlatformAdmin
							? "Manage platform administrators and organization users"
							: "View and support users in the organizations you can reach"
				}
				actions={
					<>
						<Button
							variant="outline"
							size="icon"
							onClick={() => usersQuery.refetch()}
							title="Refresh"
							aria-label="Refresh users"
							className="h-11 w-11 lg:h-9 lg:w-9"
							disabled={usersQuery.isFetching}
						>
							<RefreshCw
								className={`h-4 w-4 ${usersQuery.isFetching ? "animate-spin motion-reduce:animate-none" : ""}`}
							/>
						</Button>
						{authorization.canAnywhere("users.readwrite") && (
							<Button
								className="min-h-11 lg:min-h-0"
								ref={createUserButtonRef}
								onClick={() => setIsCreateOpen(true)}
							>
								<Plus className="h-4 w-4 mr-1.5" />
								Create User
							</Button>
						)}
					</>
				}
			/>

			<ListToolbar>
				<SearchBox
					value={searchTerm}
					onChange={(value) => {
						setSearchTerm(value);
						setOffset(0);
					}}
					placeholder="Search users by email or name..."
					className="w-full sm:flex-1"
				/>
				{canSeeOrganizations && (
					<div className="w-full sm:w-64">
						<OrganizationSelect
							value={filterOrgId}
							onChange={(value) => {
								setFilterOrgId(value);
								setOffset(0);
							}}
							showAll={true}
							showGlobal={false}
							placeholder="All users"
						/>
					</div>
				)}
				<div className="flex items-center gap-2 sm:ml-auto">
					<Switch
						id="show-disabled"
						checked={showDisabled}
						onCheckedChange={(checked) => {
							setShowDisabled(checked);
							setOffset(0);
						}}
					/>
					<Label
						htmlFor="show-disabled"
						className="cursor-pointer text-sm text-muted-foreground"
					>
						Show Inactive
					</Label>
				</div>
			</ListToolbar>

			{usersQuery.isError && (
				<Alert variant="destructive">
					<AlertTitle>
						Users could not be{" "}
						{usersQuery.data ? "refreshed" : "loaded"}
					</AlertTitle>
					<AlertDescription>
						Check your connection and try again.
						{usersQuery.data &&
							" Previously loaded users are still shown."}
					</AlertDescription>
					<Button
						variant="outline"
						className="mt-3 min-h-11 lg:min-h-0"
						disabled={usersQuery.isFetching}
						onClick={() => usersQuery.refetch()}
					>
						Retry Users
					</Button>
				</Alert>
			)}
			{/* Content */}
			<PageScrollArea
				aria-label="Users list"
				className="lg:flex lg:flex-col lg:overflow-hidden"
				aria-busy={usersQuery.isFetching}
			>
				{usersQuery.isLoading ? (
					<div
						className="space-y-2"
						role="status"
						aria-label="Loading users"
					>
						{[...Array(5)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : usersQuery.isError &&
				  !usersQuery.data ? null : users.length > 0 ? (
					isNarrow ? (
						<div className="rounded-[var(--bf-radius-surface)] border bg-card">
							<div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-2">
								{showSelection && (
									<label className="flex min-h-11 items-center gap-3 text-sm">
										<Checkbox
											aria-label="Select all visible users"
											checked={
												selection.allVisibleSelected
													? true
													: selection.someVisibleSelected
														? "indeterminate"
														: false
											}
											onCheckedChange={() =>
												selection.toggleAllVisible()
											}
										/>
										Select Page
									</label>
								)}
								<label className="flex min-w-0 items-center gap-2 text-sm">
									Sort
									<select
										aria-label="Sort users"
										className="h-11 min-w-0 rounded-[var(--bf-radius-control)] border bg-background px-2 text-foreground"
										value={`${sortColumn}:${sortDirection}`}
										onChange={(event) => {
											const [column, direction] =
												event.target.value.split(":");
											setSortColumn(column as SortColumn);
											setSortDirection(
												direction as SortDirection,
											);
											setOffset(0);
										}}
									>
										{(
											[
												["name", "Name"],
												["email", "Email"],
												["status", "Status"],
												["created", "Created"],
												["last_login", "Last Login"],
											] as const
										).flatMap(([column, label]) =>
											(["asc", "desc"] as const).map(
												(direction) => (
													<option
														key={`${column}:${direction}`}
														value={`${column}:${direction}`}
													>
														{label} ·{" "}
														{direction === "asc"
															? "ascending"
															: "descending"}
													</option>
												),
											),
										)}
									</select>
								</label>
							</div>
							<ul aria-label="Users" className="divide-y">
								{users.map((user) => (
									<li key={user.id} className="min-w-0 p-4">
										<div className="flex items-start gap-2">
											{showSelection && (
												<label className="flex h-11 w-11 shrink-0 items-center justify-center">
													<Checkbox
														aria-label={
															isSelf(user)
																? "Cannot select yourself"
																: `Select ${user.name || user.email}`
														}
														disabled={isSelf(user)}
														checked={
															!isSelf(user) &&
															selection.isSelected(
																user.id,
															)
														}
														onClick={(event) => {
															selection.toggle(
																user.id,
																{
																	shiftKey:
																		event.shiftKey,
																},
															);
															event.preventDefault();
														}}
													/>
												</label>
											)}
											<div className="min-w-0 flex-1">
												<button
													type="button"
													onClick={() =>
														handleOpenUser(user)
													}
													className="min-h-11 text-left font-medium [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-2 focus-visible:outline-ring"
												>
													{user.name || user.email}
												</button>
											</div>
											{renderUserActions(user)}
										</div>
										<div className="mt-1 text-sm text-muted-foreground">
											<UserEmailCell
												email={user.email}
												wrap
											/>
										</div>
										<div className="mt-3 flex flex-wrap gap-2">
											{!user.is_active ? (
												<Badge variant="secondary">
													Inactive
												</Badge>
											) : (
												<UserStatusBadge
													status={
														user.invite_status ??
														"active"
													}
												/>
											)}
											{user.is_superuser && (
												<Badge variant="outline">
													Platform Admin
												</Badge>
											)}
											{user.is_protected && (
												<ProtectedBadge />
											)}
											{user.is_external && (
												<Badge variant="outline">
													External
												</Badge>
											)}
										</div>
										<dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
											<div className="col-span-2">
												<dt className="text-xs text-muted-foreground">
													Organization
												</dt>
												<dd className="mt-1 [overflow-wrap:anywhere]">
													{
														getOrgInfo(
															user.organization_id,
														).name
													}
													{getOrgInfo(
														user.organization_id,
													).isProvider && (
														<span className="ml-2 text-xs text-muted-foreground">
															Provider
														</span>
													)}
												</dd>
											</div>
											<div>
												<dt className="text-xs text-muted-foreground">
													Created
												</dt>
												<dd className="mt-1 font-mono text-xs">
													{user.created_at
														? new Date(
																user.created_at,
															).toLocaleDateString()
														: "Not available"}
												</dd>
											</div>
											<div>
												<dt className="text-xs text-muted-foreground">
													Last Login
												</dt>
												<dd className="mt-1 font-mono text-xs">
													{user.last_login
														? new Date(
																user.last_login,
															).toLocaleDateString()
														: "Never"}
												</dd>
											</div>
										</dl>
									</li>
								))}
							</ul>
							<ListPagination
								offset={offset}
								limit={PAGE_SIZE}
								total={total}
								isFetching={usersQuery.isFetching}
								onPageChange={setOffset}
							/>
						</div>
					) : (
						<DataTable className="max-h-full">
							<DataTableHeader>
								<DataTableRow>
									{showSelection && (
										<DataTableHead className="w-0 whitespace-nowrap">
											<Checkbox
												aria-label="Select all visible users"
												checked={
													selection.allVisibleSelected
														? true
														: selection.someVisibleSelected
															? "indeterminate"
															: false
												}
												onCheckedChange={() =>
													selection.toggleAllVisible()
												}
											/>
										</DataTableHead>
									)}
									<DataTableHead className="w-0 whitespace-nowrap">
										Organization
									</DataTableHead>
									<DataTableHead
										className="w-0 whitespace-nowrap"
										aria-sort={
											sortColumn === "name"
												? sortDirection === "asc"
													? "ascending"
													: "descending"
												: "none"
										}
									>
										<button
											type="button"
											className="inline-flex min-h-11 items-center text-left focus-visible:outline-2 focus-visible:outline-ring"
											onClick={() => handleSort("name")}
										>
											Name
											<SortIcon
												column="name"
												sortColumn={sortColumn}
												sortDirection={sortDirection}
											/>
										</button>
									</DataTableHead>
									<DataTableHead
										className="w-0 whitespace-nowrap"
										aria-sort={
											sortColumn === "email"
												? sortDirection === "asc"
													? "ascending"
													: "descending"
												: "none"
										}
									>
										<button
											type="button"
											className="inline-flex min-h-11 items-center text-left focus-visible:outline-2 focus-visible:outline-ring"
											onClick={() => handleSort("email")}
										>
											Email
											<SortIcon
												column="email"
												sortColumn={sortColumn}
												sortDirection={sortDirection}
											/>
										</button>
									</DataTableHead>
									<DataTableHead
										className="w-0 whitespace-nowrap"
										aria-sort={
											sortColumn === "status"
												? sortDirection === "asc"
													? "ascending"
													: "descending"
												: "none"
										}
									>
										<button
											type="button"
											className="inline-flex min-h-11 items-center text-left focus-visible:outline-2 focus-visible:outline-ring"
											onClick={() => handleSort("status")}
										>
											Status
											<SortIcon
												column="status"
												sortColumn={sortColumn}
												sortDirection={sortDirection}
											/>
										</button>
									</DataTableHead>
									<DataTableHead
										className="w-0 whitespace-nowrap"
										aria-sort={
											sortColumn === "created"
												? sortDirection === "asc"
													? "ascending"
													: "descending"
												: "none"
										}
									>
										<button
											type="button"
											className="inline-flex min-h-11 items-center text-left focus-visible:outline-2 focus-visible:outline-ring"
											onClick={() =>
												handleSort("created")
											}
										>
											Created
											<SortIcon
												column="created"
												sortColumn={sortColumn}
												sortDirection={sortDirection}
											/>
										</button>
									</DataTableHead>
									<DataTableHead
										className="w-0 whitespace-nowrap"
										aria-sort={
											sortColumn === "last_login"
												? sortDirection === "asc"
													? "ascending"
													: "descending"
												: "none"
										}
									>
										<button
											type="button"
											className="inline-flex min-h-11 items-center text-left focus-visible:outline-2 focus-visible:outline-ring"
											onClick={() =>
												handleSort("last_login")
											}
										>
											Last Login
											<SortIcon
												column="last_login"
												sortColumn={sortColumn}
												sortDirection={sortDirection}
											/>
										</button>
									</DataTableHead>
									<DataTableHead className="sticky right-0 w-px whitespace-nowrap bg-muted text-right">
										Actions
									</DataTableHead>
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{users.map((user) => {
									const orgInfo = getOrgInfo(
										user.organization_id,
									);
									return (
										<DataTableRow
											key={user.id}
											href={`/users/${user.id}`}
											onClick={() => handleOpenUser(user)}
											className={"group/row"}
										>
											{showSelection && (
												<DataTableCell
													className="w-0 whitespace-nowrap"
													onClick={(e) =>
														e.stopPropagation()
													}
												>
													{isSelf(user) ? (
														<Tooltip>
															<TooltipTrigger
																asChild
															>
																<span>
																	<Checkbox
																		checked={
																			false
																		}
																		disabled
																		aria-label="Cannot select yourself"
																	/>
																</span>
															</TooltipTrigger>
															<TooltipContent>
																You can't
																include yourself
																in a bulk action
															</TooltipContent>
														</Tooltip>
													) : (
														<Checkbox
															aria-label={`Select ${user.name || user.email}`}
															checked={selection.isSelected(
																user.id,
															)}
															onClick={(e) => {
																selection.toggle(
																	user.id,
																	{
																		shiftKey:
																			e.shiftKey,
																	},
																);
																e.preventDefault();
															}}
														/>
													)}
												</DataTableCell>
											)}
											<DataTableCell className="min-w-0 w-0 whitespace-nowrap text-sm">
												<span className="inline-flex min-w-0 items-center gap-1">
													{orgInfo.isProvider ? (
														<Star className="h-3.5 w-3.5 text-muted-foreground" />
													) : (
														<Building2 className="h-3.5 w-3.5 text-muted-foreground" />
													)}
													<span className="truncate">
														{orgInfo.name}
													</span>
												</span>
											</DataTableCell>
											<DataTableCell className="min-w-48 max-w-64">
												<div className="flex min-w-0 items-center gap-1.5">
													<span className="min-w-0 font-medium [overflow-wrap:anywhere]">
														{user.name ||
															user.email}
													</span>
													{user.is_superuser && (
														<Tooltip>
															<TooltipTrigger
																asChild
															>
																<Crown className="h-4 w-4 shrink-0 text-muted-foreground" />
															</TooltipTrigger>
															<TooltipContent>
																Platform Admin
															</TooltipContent>
														</Tooltip>
													)}
													{user.is_protected && (
														<ProtectedBadge />
													)}
													{user.is_external && (
														<Tooltip>
															<TooltipTrigger
																asChild
															>
																<Badge
																	variant="outline"
																	className="text-xs shrink-0"
																>
																	External
																</Badge>
															</TooltipTrigger>
															<TooltipContent>
																External user —
																sees only what
																the Everyone
																tier or an
																explicit role
																grant allows
															</TooltipContent>
														</Tooltip>
													)}
												</div>
											</DataTableCell>
											<DataTableCell className="min-w-48 max-w-64 text-muted-foreground">
												<UserEmailCell
													email={user.email}
												/>
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap">
												{!user.is_active ? (
													<Badge variant="secondary">
														Inactive
													</Badge>
												) : (
													<UserStatusBadge
														status={
															user.invite_status ??
															"active"
														}
													/>
												)}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
												{user.created_at
													? new Date(
															user.created_at,
														).toLocaleDateString()
													: "N/A"}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
												{user.last_login
													? new Date(
															user.last_login,
														).toLocaleDateString()
													: "Never"}
											</DataTableCell>
											<DataTableCell
												className="sticky right-0 w-px whitespace-nowrap bg-card text-right group-hover/row:bg-[color-mix(in_oklch,var(--card),var(--muted)_50%)]"
												onClick={(e) =>
													e.stopPropagation()
												}
											>
												{renderUserActions(user)}
											</DataTableCell>
										</DataTableRow>
									);
								})}
							</DataTableBody>
							<DataTableFooter>
								<DataTableRow>
									<DataTableCell
										colSpan={showSelection ? 8 : 7}
										className="p-0"
									>
										<ListPagination
											offset={offset}
											limit={PAGE_SIZE}
											total={total}
											isFetching={usersQuery.isFetching}
											onPageChange={setOffset}
										/>
									</DataTableCell>
								</DataTableRow>
							</DataTableFooter>
						</DataTable>
					)
				) : (
					<div className="flex flex-col items-center justify-center py-12 text-center">
						<UserCog className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							{searchTerm
								? "No users match your search"
								: "No Users Found"}
						</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							{searchTerm
								? "Try adjusting your search term or clear the filter"
								: "No users in the system"}
						</p>
					</div>
				)}
			</PageScrollArea>

			<BulkActionBar
				count={selection.count}
				activeMix={activeMix}
				{...bulkAbilities}
				onClear={selection.clear}
				onMoveOrg={() => setBulkMode("move_org")}
				onReplaceRoles={() => setBulkMode("replace_roles")}
				onDisable={() => setBulkMode("disable")}
				onEnable={() => setBulkMode("enable")}
			/>

			<BulkMoveOrgDialog
				open={bulkMode === "move_org"}
				onOpenChange={(o) => !o && closeBulk()}
				users={selection.selectedItems}
				onPartialFailure={handlePartialFailure}
				onSuccess={onBulkSuccess}
			/>
			<BulkReplaceRolesDialog
				open={bulkMode === "replace_roles"}
				onOpenChange={(o) => !o && closeBulk()}
				users={selection.selectedItems}
				onPartialFailure={handlePartialFailure}
				onSuccess={onBulkSuccess}
			/>
			<BulkSetActiveDialog
				open={bulkMode === "disable"}
				mode="disable"
				onOpenChange={(o) => !o && closeBulk()}
				users={selection.selectedItems}
				onPartialFailure={handlePartialFailure}
				onSuccess={onBulkSuccess}
			/>
			<BulkSetActiveDialog
				open={bulkMode === "enable"}
				mode="enable"
				onOpenChange={(o) => !o && closeBulk()}
				users={selection.selectedItems}
				onPartialFailure={handlePartialFailure}
				onSuccess={onBulkSuccess}
			/>
			<BulkResultDialog
				open={bulkResult !== null}
				onOpenChange={(o) => !o && setBulkResult(null)}
				result={bulkResult}
				users={bulkResultUsers}
			/>

			<CreateUserDialog
				open={isCreateOpen}
				onOpenChange={setIsCreateOpen}
			/>

			<EditUserDialog
				user={profileUser}
				open={profileUser !== undefined}
				onOpenChange={(open) => !open && setProfileUser(undefined)}
			/>

			{accountActions.dialogs}
		</PageWorkspace>
	);
}
