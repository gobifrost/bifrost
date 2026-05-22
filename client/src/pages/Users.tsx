import { useState, useMemo } from "react";
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
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
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
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import {
	useUsersFiltered,
	useDeleteUser,
	useUpdateUser,
} from "@/hooks/useUsers";
import { useUserSelection } from "@/hooks/useUserSelection";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useAuth } from "@/contexts/AuthContext";
import { useOrgScope } from "@/contexts/OrgScopeContext";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { CreateUserDialog } from "@/components/users/CreateUserDialog";
import { EditUserDialog } from "@/components/users/EditUserDialog";
import { UserActionsMenu } from "@/components/users/UserActionsMenu";
import { UserStatusBadge } from "@/components/users/UserStatusBadge";
import { BulkActionBar } from "@/components/users/BulkActionBar";
import { UserEmailCell } from "@/components/users/UserEmailCell";
import {
	BulkMoveOrgDialog,
	BulkReplaceRolesDialog,
	BulkResultDialog,
	BulkSetActiveDialog,
} from "@/components/users/BulkUserDialogs";
import {
	useRegenerateInvite,
	useResendInvite,
	useRevokeInvite,
} from "@/hooks/useUserInvites";
import { toast } from "sonner";
import type { components, components as v1 } from "@/lib/v1";
type User = components["schemas"]["UserPublic"];
type Organization = components["schemas"]["OrganizationPublic"];

type SortColumn = "name" | "email" | "status" | "created" | "last_login";
type SortDirection = "asc" | "desc";

function SortIcon({ column, sortColumn, sortDirection }: { column: SortColumn; sortColumn: SortColumn; sortDirection: SortDirection }) {
	if (sortColumn !== column) return null;
	return sortDirection === "asc" ? (
		<ArrowUp className="inline ml-1 h-3 w-3" />
	) : (
		<ArrowDown className="inline ml-1 h-3 w-3" />
	);
}

export function Users() {
	const [selectedUser, setSelectedUser] = useState<User | undefined>();
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const [isEditOpen, setIsEditOpen] = useState(false);
	const [isDeleteOpen, setIsDeleteOpen] = useState(false);
	const [isDisableOpen, setIsDisableOpen] = useState(false);
	const [searchTerm, setSearchTerm] = useState("");
	const [showDisabled, setShowDisabled] = useState(false);
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [sortColumn, setSortColumn] = useState<SortColumn>("name");
	const [sortDirection, setSortDirection] = useState<SortDirection>("asc");

	const { scope } = useOrgScope();
	const { user: currentUser, isPlatformAdmin } = useAuth();

	const {
		data: users,
		isLoading,
		refetch,
	} = useUsersFiltered(
		isPlatformAdmin ? filterOrgId : undefined,
		showDisabled,
	);
	const deleteMutation = useDeleteUser();
	const updateMutation = useUpdateUser();
	const resendMutation = useResendInvite();
	const regenerateMutation = useRegenerateInvite();
	const revokeMutation = useRevokeInvite();

	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});

	const getOrgInfo = (
		orgId: string | null | undefined,
	): { name: string; isProvider: boolean } => {
		if (!orgId) return { name: "Platform", isProvider: false };
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return { name: org?.name || orgId, isProvider: org?.is_provider ?? false };
	};

	const filteredUsers = useSearch(users || [], searchTerm, ["email", "name"]);

	const sortedUsers = useMemo(() => {
		if (!filteredUsers) return [];
		return [...filteredUsers].sort((a, b) => {
			const dir = sortDirection === "asc" ? 1 : -1;
			switch (sortColumn) {
				case "name":
					return dir * (a.name || a.email || "").localeCompare(b.name || b.email || "");
				case "email":
					return dir * (a.email || "").localeCompare(b.email || "");
				case "status": {
					const aVal = a.invite_status ?? "active";
					const bVal = b.invite_status ?? "active";
					return dir * aVal.localeCompare(bVal);
				}
				case "created": {
					const aDate = a.created_at ? new Date(a.created_at).getTime() : 0;
					const bDate = b.created_at ? new Date(b.created_at).getTime() : 0;
					return dir * (aDate - bDate);
				}
				case "last_login": {
					const aDate = a.last_login ? new Date(a.last_login).getTime() : 0;
					const bDate = b.last_login ? new Date(b.last_login).getTime() : 0;
					return dir * (aDate - bDate);
				}
				default:
					return 0;
			}
		});
	}, [filteredUsers, sortColumn, sortDirection]);

	const handleSort = (column: SortColumn) => {
		if (sortColumn === column) {
			setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
		} else {
			setSortColumn(column);
			setSortDirection("asc");
		}
	};

	// ===== Bulk selection + actions =====
	const disabledSelectionIds = useMemo(
		() => (currentUser ? [currentUser.id] : []),
		[currentUser],
	);
	const selection = useUserSelection(sortedUsers, disabledSelectionIds);

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

	const handleEditUser = (user: User) => {
		setSelectedUser(user);
		setIsEditOpen(true);
	};

	const handleToggleActive = (user: User) => {
		if (user.is_active) {
			setSelectedUser(user);
			setIsDisableOpen(true);
		} else {
			handleEnableUser(user);
		}
	};

	const handleDeleteUser = (user: User) => {
		setSelectedUser(user);
		setIsDeleteOpen(true);
	};

	const handleConfirmDisable = async () => {
		if (!selectedUser) return;

		try {
			await updateMutation.mutateAsync({
				params: { path: { user_id: selectedUser.id } },
				body: { is_active: false },
			});
			toast.success("User disabled", {
				description: `${selectedUser.name || selectedUser.email} has been disabled`,
			});
			setIsDisableOpen(false);
			setSelectedUser(undefined);
		} catch (error) {
			const errorMessage =
				error instanceof Error
					? error.message
					: "Unknown error occurred";
			toast.error("Failed to disable user", {
				description: errorMessage,
			});
		}
	};

	const handleEnableUser = async (user: User) => {
		try {
			await updateMutation.mutateAsync({
				params: { path: { user_id: user.id } },
				body: { is_active: true },
			});
			toast.success("User enabled", {
				description: `${user.name || user.email} has been re-enabled`,
			});
		} catch (error) {
			const errorMessage =
				error instanceof Error
					? error.message
					: "Unknown error occurred";
			toast.error("Failed to enable user", {
				description: errorMessage,
			});
		}
	};

	const handleConfirmDelete = async () => {
		if (!selectedUser) return;

		try {
			await deleteMutation.mutateAsync({
				params: { path: { user_id: selectedUser.id } },
			});
			toast.success("User permanently deleted", {
				description: `${selectedUser.name || selectedUser.email} has been permanently removed`,
			});
			setIsDeleteOpen(false);
			setSelectedUser(undefined);
		} catch (error) {
			const errorMessage =
				error instanceof Error
					? error.message
					: "Unknown error occurred";
			toast.error("Failed to delete user", {
				description: errorMessage,
			});
		}
	};

	const handleEditClose = () => {
		setIsEditOpen(false);
		setSelectedUser(undefined);
	};

	const isSelf = (user: User) =>
		!!(currentUser && user.id === currentUser.id);

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">
						Users
					</h1>
					<p className="mt-2 text-muted-foreground">
						{scope.type === "global"
							? "Manage platform administrators and organization users"
							: `Users for ${scope.orgName}`}
					</p>
				</div>
				<div className="flex items-center gap-2">
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						title="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button
						variant="outline"
						size="icon"
						onClick={() => setIsCreateOpen(true)}
						title="Create User"
					>
						<Plus className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Filters Row */}
			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search users by email or name..."
					className="flex-1"
				/>
				{isPlatformAdmin && (
					<div className="w-64">
						<OrganizationSelect
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={false}
							placeholder="All users"
						/>
					</div>
				)}
				<div className="flex items-center gap-2 ml-auto">
					<Switch
						id="show-disabled"
						checked={showDisabled}
						onCheckedChange={setShowDisabled}
					/>
					<Label
						htmlFor="show-disabled"
						className="text-sm text-muted-foreground cursor-pointer"
					>
						Show disabled
					</Label>
				</div>
			</div>

			{/* Content */}
			<div className="flex-1 min-h-0">
				{isLoading ? (
					<div className="space-y-2">
						{[...Array(5)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				) : sortedUsers && sortedUsers.length > 0 ? (
					<DataTable>
						<DataTableHeader>
							<DataTableRow>
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
										onCheckedChange={() => selection.toggleAllVisible()}
									/>
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">
									Organization
								</DataTableHead>
								<DataTableHead
									className="w-0 whitespace-nowrap cursor-pointer select-none"
									onClick={() => handleSort("name")}
								>
									Name
									<SortIcon column="name" sortColumn={sortColumn} sortDirection={sortDirection} />
								</DataTableHead>
								<DataTableHead
									className="cursor-pointer select-none"
									onClick={() => handleSort("email")}
								>
									Email
									<SortIcon column="email" sortColumn={sortColumn} sortDirection={sortDirection} />
								</DataTableHead>
								<DataTableHead
									className="w-0 whitespace-nowrap cursor-pointer select-none"
									onClick={() => handleSort("status")}
								>
									Status
									<SortIcon column="status" sortColumn={sortColumn} sortDirection={sortDirection} />
								</DataTableHead>
								<DataTableHead
									className="w-0 whitespace-nowrap cursor-pointer select-none"
									onClick={() => handleSort("created")}
								>
									Created
									<SortIcon column="created" sortColumn={sortColumn} sortDirection={sortDirection} />
								</DataTableHead>
								<DataTableHead
									className="w-0 whitespace-nowrap cursor-pointer select-none"
									onClick={() => handleSort("last_login")}
								>
									Last Login
									<SortIcon column="last_login" sortColumn={sortColumn} sortDirection={sortDirection} />
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right sticky right-0 bg-background"></DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{sortedUsers.map((user) => {
								const orgInfo = getOrgInfo(user.organization_id);
								return (
									<DataTableRow
										key={user.id}
										clickable
										onClick={() => handleEditUser(user)}
										className={
											"group/row" +
											(!user.is_active ? " opacity-60" : "")
										}
									>
										<DataTableCell
											className="w-0 whitespace-nowrap"
											onClick={(e) => e.stopPropagation()}
										>
											{isSelf(user) ? (
												<Tooltip>
													<TooltipTrigger asChild>
														<span>
															<Checkbox
																checked={false}
																disabled
																aria-label="Cannot select yourself"
															/>
														</span>
													</TooltipTrigger>
													<TooltipContent>
														You can't include yourself in a bulk action
													</TooltipContent>
												</Tooltip>
											) : (
												<Checkbox
													aria-label={`Select ${user.name || user.email}`}
													checked={selection.isSelected(user.id)}
													onClick={(e) => {
														selection.toggle(user.id, {
															shiftKey: e.shiftKey,
														});
														e.preventDefault();
													}}
												/>
											)}
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap text-sm">
											{user.is_superuser ? (
												<span className="text-muted-foreground">—</span>
											) : (
												<span className="inline-flex items-center gap-1">
													{orgInfo.isProvider ? (
														<Star className="h-3.5 w-3.5 text-amber-500 fill-amber-500" />
													) : (
														<Building2 className="h-3.5 w-3.5 text-muted-foreground" />
													)}
													<span>{orgInfo.name}</span>
												</span>
											)}
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap">
											<div className="flex items-center gap-1.5">
												<span className="font-medium">
													{user.name || user.email}
												</span>
												{user.is_superuser && (
													<Tooltip>
														<TooltipTrigger asChild>
															<Crown className="h-4 w-4 shrink-0 text-amber-500 fill-amber-500" />
														</TooltipTrigger>
														<TooltipContent>Platform Admin</TooltipContent>
													</Tooltip>
												)}
											</div>
										</DataTableCell>
										<DataTableCell
											className="text-muted-foreground max-w-0"
											onClick={(e) => e.stopPropagation()}
										>
											<UserEmailCell email={user.email} />
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap">
											<UserStatusBadge
												status={user.invite_status ?? "active"}
											/>
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
											className="w-0 whitespace-nowrap text-right sticky right-0 bg-card group-hover/row:bg-[color-mix(in_oklch,var(--card),var(--muted)_50%)]"
											onClick={(e) => e.stopPropagation()}
										>
											<UserActionsMenu
												status={user.invite_status ?? "active"}
												isActive={user.is_active}
												isSelf={isSelf(user)}
												onResend={() =>
													resendMutation.mutate(user.id, {
														onSuccess: (res) => {
															toast.success(
																res.event_emitted
																	? `Invite automation triggered for ${user.email}`
																	: "Invite regenerated (no automations — copy link from regenerate)",
															);
														},
														onError: (e: unknown) =>
															toast.error(
																e instanceof Error ? e.message : "Failed to resend invite",
															),
													})
												}
												onRegenerate={() =>
													regenerateMutation.mutate(user.id, {
														onSuccess: (res) => {
															void navigator.clipboard.writeText(res.registration_url);
															toast.success("New invite link generated and copied");
														},
														onError: (e: unknown) =>
															toast.error(
																e instanceof Error ? e.message : "Failed to regenerate link",
															),
													})
												}
												onCopyLink={() =>
													regenerateMutation.mutate(user.id, {
														onSuccess: (res) => {
															void navigator.clipboard.writeText(res.registration_url);
															toast.success("Registration link copied");
														},
														onError: (e: unknown) =>
															toast.error(
																e instanceof Error ? e.message : "Failed to copy link",
															),
													})
												}
												onRevoke={() =>
													revokeMutation.mutate(user.id, {
														onSuccess: () => toast.success("Invite revoked"),
														onError: (e: unknown) =>
															toast.error(
																e instanceof Error ? e.message : "Failed to revoke invite",
															),
													})
												}
												onToggleActive={() => handleToggleActive(user)}
												onDelete={() => handleDeleteUser(user)}
											/>
										</DataTableCell>
									</DataTableRow>
								);
							})}
						</DataTableBody>
					</DataTable>
				) : (
					<div className="flex flex-col items-center justify-center py-12 text-center">
						<UserCog className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							{searchTerm
								? "No users match your search"
								: "No users found"}
						</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							{searchTerm
								? "Try adjusting your search term or clear the filter"
								: "No users in the system"}
						</p>
					</div>
				)}
			</div>

			<BulkActionBar
				count={selection.count}
				activeMix={activeMix}
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
				user={selectedUser}
				open={isEditOpen}
				onOpenChange={handleEditClose}
			/>

			{/* Disable confirmation dialog */}
			<AlertDialog open={isDisableOpen} onOpenChange={setIsDisableOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Disable User</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to disable{" "}
							{selectedUser?.name || selectedUser?.email}? They
							will no longer be able to access the platform. You
							can re-enable them later.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={handleConfirmDisable}>
							Disable
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Permanent delete confirmation dialog */}
			<AlertDialog open={isDeleteOpen} onOpenChange={setIsDeleteOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Permanently Delete User
						</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to permanently delete{" "}
							{selectedUser?.name || selectedUser?.email}? This
							action cannot be undone and all associated data will
							be removed.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Permanently Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
