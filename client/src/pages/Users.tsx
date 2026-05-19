import { useState, useMemo } from "react";
import {
	Shield,
	Users as UsersIcon,
	RefreshCw,
	UserCog,
	Edit,
	Plus,
	Trash2,
	Globe,
	Building2,
	Star,
	ArrowUp,
	ArrowDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Badge } from "@/components/ui/badge";
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
import { useOrganizations } from "@/hooks/useOrganizations";
import { useAuth } from "@/contexts/AuthContext";
import { useOrgScope } from "@/contexts/OrgScopeContext";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { CreateUserDialog } from "@/components/users/CreateUserDialog";
import { EditUserDialog } from "@/components/users/EditUserDialog";
import { InviteActionsMenu } from "@/components/users/InviteActionsMenu";
import { UserStatusBadge } from "@/components/users/UserStatusBadge";
import {
	useRegenerateInvite,
	useResendInvite,
	useRevokeInvite,
} from "@/hooks/useUserInvites";
import { toast } from "sonner";
import type { components } from "@/lib/v1";
type User = components["schemas"]["UserPublic"];
type Organization = components["schemas"]["OrganizationPublic"];

type SortColumn = "organization" | "name" | "email" | "type" | "status" | "created" | "last_login";
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

	// Fetch users with scope filter (undefined = all, null = global only, UUID = specific org)
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

	// Fetch organizations for the org name lookup (platform admins only)
	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});

	// Helper to get organization info from ID
	const getOrgInfo = (
		orgId: string | null | undefined,
	): { name: string; isProvider: boolean } => {
		if (!orgId) return { name: "Platform", isProvider: false };
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return { name: org?.name || orgId, isProvider: org?.is_provider ?? false };
	};

	// Apply search filter
	const filteredUsers = useSearch(users || [], searchTerm, ["email", "name"]);

	// Apply sorting
	const sortedUsers = useMemo(() => {
		if (!filteredUsers) return [];
		return [...filteredUsers].sort((a, b) => {
			const dir = sortDirection === "asc" ? 1 : -1;
			switch (sortColumn) {
				case "organization": {
					const aOrg = a.organization_id
						? (organizations?.find((o: Organization) => o.id === a.organization_id)?.name || a.organization_id)
						: "Platform";
					const bOrg = b.organization_id
						? (organizations?.find((o: Organization) => o.id === b.organization_id)?.name || b.organization_id)
						: "Platform";
					return dir * aOrg.localeCompare(bOrg);
				}
				case "name":
					return dir * (a.name || a.email || "").localeCompare(b.name || b.email || "");
				case "email":
					return dir * (a.email || "").localeCompare(b.email || "");
				case "type": {
					const aVal = a.is_superuser ? 1 : 0;
					const bVal = b.is_superuser ? 1 : 0;
					return dir * (aVal - bVal);
				}
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
	}, [filteredUsers, sortColumn, sortDirection, organizations]);

	const handleSort = (column: SortColumn) => {
		if (sortColumn === column) {
			setSortDirection((d) => (d === "asc" ? "desc" : "asc"));
		} else {
			setSortColumn(column);
			setSortDirection("asc");
		}
	};

	const handleEditUser = (user: User) => {
		setSelectedUser(user);
		setIsEditOpen(true);
	};

	const handleToggleActive = (user: User) => {
		if (user.is_active) {
			// Disabling requires confirmation
			setSelectedUser(user);
			setIsDisableOpen(true);
		} else {
			// Enabling is instant
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

	const getUserTypeBadge = (isSuperuser: boolean) => {
		return isSuperuser ? (
			<Badge variant="default">
				<Shield className="mr-1 h-3 w-3" />
				Platform Admin
			</Badge>
		) : (
			<Badge variant="secondary">
				<UsersIcon className="mr-1 h-3 w-3" />
				Organization User
			</Badge>
		);
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
								{isPlatformAdmin && (
									<DataTableHead
										className="w-0 whitespace-nowrap cursor-pointer select-none"
										onClick={() => handleSort("organization")}
									>
										Organization
										<SortIcon column="organization" sortColumn={sortColumn} sortDirection={sortDirection} />
									</DataTableHead>
								)}
								<DataTableHead
									className="cursor-pointer select-none"
									onClick={() => handleSort("name")}
								>
									Name
									<SortIcon column="name" sortColumn={sortColumn} sortDirection={sortDirection} />
								</DataTableHead>
								<DataTableHead
									className="w-0 whitespace-nowrap cursor-pointer select-none"
									onClick={() => handleSort("email")}
								>
									Email
									<SortIcon column="email" sortColumn={sortColumn} sortDirection={sortDirection} />
								</DataTableHead>
								<DataTableHead
									className="w-0 whitespace-nowrap cursor-pointer select-none"
									onClick={() => handleSort("type")}
								>
									Type
									<SortIcon column="type" sortColumn={sortColumn} sortDirection={sortDirection} />
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
									onClick={() => handleSort("status")}
								>
									Status
									<SortIcon column="status" sortColumn={sortColumn} sortDirection={sortDirection} />
								</DataTableHead>
								<DataTableHead
									className="w-0 whitespace-nowrap cursor-pointer select-none"
									onClick={() => handleSort("last_login")}
								>
									Last Login
									<SortIcon column="last_login" sortColumn={sortColumn} sortDirection={sortDirection} />
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right"></DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{sortedUsers.map((user) => (
								<DataTableRow
									key={user.id}
									clickable
									onClick={() => handleEditUser(user)}
									className={
										!user.is_active
											? "opacity-60"
											: undefined
									}
								>
									{isPlatformAdmin && (
										<DataTableCell className="w-0 whitespace-nowrap">
											{(() => {
												const orgInfo = getOrgInfo(
													user.organization_id,
												);
												return user.organization_id ? (
													<Badge
														variant="outline"
														className="text-xs"
													>
														{orgInfo.isProvider ? (
															<Star className="mr-1 h-3 w-3 text-amber-500 fill-amber-500" />
														) : (
															<Building2 className="mr-1 h-3 w-3" />
														)}
														{orgInfo.name}
													</Badge>
												) : (
													<Badge
														variant="default"
														className="text-xs"
													>
														<Globe className="mr-1 h-3 w-3" />
														Platform
													</Badge>
												);
											})()}
										</DataTableCell>
									)}
									<DataTableCell className="font-medium">
										{user.name || user.email}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-muted-foreground">
										{user.email}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										{getUserTypeBadge(user.is_superuser)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
										{user.created_at
											? new Date(
													user.created_at,
												).toLocaleDateString()
											: "N/A"}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<UserStatusBadge
											status={user.invite_status ?? "active"}
										/>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
										{user.last_login
											? new Date(
													user.last_login,
												).toLocaleDateString()
											: "Never"}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right">
										<div
											className="flex items-center justify-end gap-2"
											onClick={(e) => e.stopPropagation()}
										>
											<Tooltip>
												<TooltipTrigger asChild>
													<div className="w-fit">
														<Switch
															checked={
																user.is_active
															}
															onCheckedChange={() =>
																handleToggleActive(
																	user,
																)
															}
															disabled={
																isSelf(
																	user,
																) ||
																updateMutation.isPending
															}
														/>
													</div>
												</TooltipTrigger>
												<TooltipContent>
													{isSelf(user)
														? "You cannot disable your own account"
														: user.is_active
															? "Enabled — click to disable"
															: "Disabled — click to enable"}
												</TooltipContent>
											</Tooltip>
											<InviteActionsMenu
												userId={user.id}
												status={user.invite_status ?? "active"}
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
											/>
											<Button
												variant="ghost"
												size="icon"
												onClick={() =>
													handleEditUser(user)
												}
												title="Edit user"
											>
												<Edit className="h-4 w-4" />
											</Button>
											<Button
												variant="ghost"
												size="icon"
												onClick={() =>
													handleDeleteUser(user)
												}
												title="Permanently delete"
												disabled={isSelf(user)}
											>
												<Trash2 className="h-4 w-4 text-destructive" />
											</Button>
										</div>
									</DataTableCell>
								</DataTableRow>
							))}
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
