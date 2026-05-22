import { useEffect, useState, useMemo } from "react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import { Shield, AlertCircle, Loader2, AlertTriangle, Check, ChevronsUpDown, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";
import { useUpdateUser, useUserRoles } from "@/hooks/useUsers";
import { useRoles, useAssignUsersToRole, useRemoveUserFromRole } from "@/hooks/useRoles";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useAuth } from "@/contexts/AuthContext";
import { toast } from "sonner";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];
type Organization = components["schemas"]["OrganizationPublic"];
type Role = components["schemas"]["RolePublic"];
type UserRolesResponse = components["schemas"]["UserRolesResponse"];

interface EditUserDialogProps {
	user: User | undefined;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

// Extract dialog content to separate component for key-based remounting
function EditUserDialogContent({
	user,
	onOpenChange,
}: {
	user: User;
	onOpenChange: (open: boolean) => void;
}) {
	const [displayName, setDisplayName] = useState(user.name || "");
	const [isActive, setIsActive] = useState(user.is_active);
	const [isPlatformAdmin, setIsPlatformAdmin] = useState(
		user.is_superuser,
	);
	const [orgId, setOrgId] = useState<string>(user.organization_id || "");
	const [validationError, setValidationError] = useState<string | null>(null);
	const [rolesPopoverOpen, setRolesPopoverOpen] = useState(false);
	const [rolesInitialized, setRolesInitialized] = useState(false);

	const queryClient = useQueryClient();
	const updateMutation = useUpdateUser();
	const assignUsersToRole = useAssignUsersToRole();
	const removeUserFromRole = useRemoveUserFromRole();
	const { data: organizations, isLoading: orgsLoading } = useOrganizations();
	const { data: allRoles } = useRoles();
	const { data: userRolesData } = useUserRoles(user.id);
	const { user: currentUser } = useAuth();

	// Derive initial role IDs from query data (stable reference)
	const initialRoleIds = useMemo(() => {
		return new Set((userRolesData as UserRolesResponse)?.role_ids ?? []);
	}, [userRolesData]);

	// selectedRoleIds tracks the user's current selection; initialized from API on first data load
	const [selectedRoleIds, setSelectedRoleIds] = useState<Set<string>>(new Set());

	// Initialize once when role data first arrives
	if (userRolesData && !rolesInitialized) {
		setRolesInitialized(true);
		setSelectedRoleIds(initialRoleIds);
	}

	const roles = useMemo(() => (allRoles ?? []) as Role[], [allRoles]);

	// Find the provider org (for auto-selecting when promoting to platform admin)
	const providerOrg = organizations?.find((org: Organization) => org.is_provider);
	const providerOrgId =
		providerOrg?.id ??
		(currentUser?.isSuperuser ? currentUser.organizationId : null);

	useEffect(() => {
		if (isPlatformAdmin && providerOrgId && orgId !== providerOrgId) {
			setOrgId(providerOrgId);
		}
	}, [isPlatformAdmin, orgId, providerOrgId]);

	// Check if editing own account
	const isEditingSelf = !!(currentUser && user.id === currentUser.id);

	const isRoleChanging = user.is_superuser !== isPlatformAdmin;
	const isDemoting = user.is_superuser && !isPlatformAdmin;
	const isPromoting = !user.is_superuser && isPlatformAdmin;

	// Auto-select provider org when promoting to platform admin
	const handleUserTypeChange = (value: string) => {
		const isAdmin = value === "platform";
		setIsPlatformAdmin(isAdmin);
		if (isAdmin && providerOrgId) {
			setOrgId(providerOrgId);
		} else if (!isAdmin && orgId === providerOrgId) {
			// Clear provider org if switching to org user
			setOrgId("");
		}
	};

	const toggleRole = (roleId: string) => {
		setSelectedRoleIds((prev) => {
			const next = new Set(prev);
			if (next.has(roleId)) {
				next.delete(roleId);
			} else {
				next.add(roleId);
			}
			return next;
		});
	};

	const removeRole = (roleId: string) => {
		setSelectedRoleIds((prev) => {
			const next = new Set(prev);
			next.delete(roleId);
			return next;
		});
	};

	const selectedRoleNames = useMemo(() => {
		return roles
			.filter((r) => selectedRoleIds.has(r.id))
			.map((r) => ({ id: r.id, name: r.name }));
	}, [roles, selectedRoleIds]);

	const validateForm = (): boolean => {
		if (!displayName || displayName.trim().length === 0) {
			setValidationError("Please enter a display name");
			return false;
		}
		if (!(isPlatformAdmin ? providerOrgId || orgId : orgId)) {
			setValidationError("Please select an organization");
			return false;
		}
		setValidationError(null);
		return true;
	};

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();

		if (!validateForm()) {
			return;
		}

		const effectiveOrgId = isPlatformAdmin ? providerOrgId || orgId : orgId;

		// Build request body - only send changed fields
		const body = {
			name:
				displayName.trim() !== (user.name || "")
					? displayName.trim()
					: null,
			is_active:
				!isEditingSelf && isActive !== user.is_active ? isActive : null,
			is_superuser:
				!isEditingSelf && isRoleChanging
					? isPlatformAdmin
					: null,
			organization_id:
				!isEditingSelf && effectiveOrgId !== (user.organization_id || "")
					? effectiveOrgId || null
					: null,
		};

		// Compute role changes
		const rolesToAdd = [...selectedRoleIds].filter((id) => !initialRoleIds.has(id));
		const rolesToRemove = [...initialRoleIds].filter((id) => !selectedRoleIds.has(id));
		const hasRoleChanges = rolesToAdd.length > 0 || rolesToRemove.length > 0;

		// If no actual changes, just close
		if (
			body.name === null &&
			body.is_active === null &&
			body.is_superuser === null &&
			body.organization_id === null &&
			!hasRoleChanges
		) {
			toast.info("No changes to save");
			onOpenChange(false);
			return;
		}

		try {
			// Update user fields if changed
			if (
				body.name !== null ||
				body.is_active !== null ||
				body.is_superuser !== null ||
				body.organization_id !== null
			) {
				await updateMutation.mutateAsync({
					params: { path: { user_id: user.id } },
					body,
				});
			}

			// Update role assignments
			for (const roleId of rolesToAdd) {
				await assignUsersToRole.mutateAsync({
					params: { path: { role_id: roleId } },
					body: { user_ids: [user.id] },
				});
			}
			for (const roleId of rolesToRemove) {
				await removeUserFromRole.mutateAsync({
					params: { path: { role_id: roleId, user_id: user.id } },
				});
			}

			// Invalidate user roles cache so reopening reflects changes
			if (hasRoleChanges) {
				await queryClient.invalidateQueries({
					queryKey: ["get", "/api/users/{user_id}/roles"],
				});
			}

			toast.success("User updated successfully", {
				description: `Changes to ${user.name || user.email} have been saved`,
			});

			onOpenChange(false);
		} catch (error) {
			const errorMessage =
				error instanceof Error
					? error.message
					: "Unknown error occurred";
			toast.error("Failed to update user", {
				description: errorMessage,
			});
		}
	};

	const isSaving = updateMutation.isPending || assignUsersToRole.isPending || removeUserFromRole.isPending;

	return (
		<DialogContent className="sm:max-w-[500px]">
			<DialogHeader>
				<DialogTitle>Edit User</DialogTitle>
				<DialogDescription>
					Update user details and permissions for {user.email}
				</DialogDescription>
			</DialogHeader>

			<form onSubmit={handleSubmit} className="space-y-4 mt-4">
				{isEditingSelf && (
					<Alert>
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							You are editing your own account. You can only
							change your display name. Role and status changes
							must be made by another administrator.
						</AlertDescription>
					</Alert>
				)}

				{validationError && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{validationError}</AlertDescription>
					</Alert>
				)}

				<div className="space-y-2">
					<Label htmlFor="email-display">Email Address</Label>
					<Input
						id="email-display"
						type="email"
						value={user.email}
						disabled
						className="bg-muted"
					/>
					<p className="text-xs text-muted-foreground">
						Email address cannot be changed
					</p>
				</div>

				<div className="space-y-2">
					<Label htmlFor="displayName">Display Name</Label>
					<Input
						id="displayName"
						type="text"
						placeholder="John Doe"
						value={displayName}
						onChange={(e) => setDisplayName(e.target.value)}
						required
					/>
				</div>

				<div className="flex items-center justify-between rounded-lg border p-4">
					<div className="space-y-0.5">
						<Label htmlFor="active">Account Status</Label>
						<p className="text-xs text-muted-foreground">
							{isActive
								? "User can access the platform"
								: "User access is disabled"}
						</p>
					</div>
					<Switch
						id="active"
						checked={isActive}
						onCheckedChange={setIsActive}
						disabled={isEditingSelf}
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="userType">User Type</Label>
					<Combobox
						id="userType"
						value={isPlatformAdmin ? "platform" : "org"}
						onValueChange={handleUserTypeChange}
						disabled={isEditingSelf}
						options={[
							{
								value: "platform",
								label: "Platform Administrator",
								description:
									"Full access to all organizations and settings",
							},
							{
								value: "org",
								label: "Organization User",
								description:
									"Access limited to specific organization",
							},
						]}
						placeholder="Select user type"
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="organization">Organization</Label>
					<Combobox
						id="organization"
						value={orgId}
						onValueChange={setOrgId}
						disabled={isPlatformAdmin || isEditingSelf}
						options={
							organizations?.map((org: Organization) => {
								const option: {
									value: string;
									label: string;
									description?: string;
								} = {
									value: org.id,
									label: org.is_provider
										? `${org.name} (Provider)`
										: org.name,
								};
								if (org.domain) {
									option.description = `@${org.domain}`;
								}
								return option;
							}) ?? []
						}
						placeholder="Select an organization..."
						searchPlaceholder="Search organizations..."
						emptyText="No organizations found."
						isLoading={orgsLoading}
					/>
					<p className="text-xs text-muted-foreground">
						{isPlatformAdmin
							? "Platform administrators are assigned to the provider organization"
							: "The organization this user belongs to"}
					</p>
				</div>

				{/* Roles multi-select */}
				{!isPlatformAdmin && !isEditingSelf && (
					<div className="space-y-2">
						<Label>Roles</Label>
						<Popover open={rolesPopoverOpen} onOpenChange={setRolesPopoverOpen}>
							<PopoverTrigger asChild>
								<Button
									variant="outline"
									role="combobox"
									aria-expanded={rolesPopoverOpen}
									className="w-full justify-between font-normal"
								>
									<span className={cn(
										"truncate",
										selectedRoleIds.size === 0 && "text-muted-foreground",
									)}>
										{selectedRoleIds.size === 0
											? "Select roles..."
											: `${selectedRoleIds.size} role${selectedRoleIds.size === 1 ? "" : "s"} selected`}
									</span>
									<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
								</Button>
							</PopoverTrigger>
							<PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
								<Command>
									<CommandInput placeholder="Search roles..." />
									<CommandList className="max-h-48 overflow-y-auto">
										<CommandEmpty>No roles found.</CommandEmpty>
										<CommandGroup>
											{roles.map((role) => (
												<CommandItem
													key={role.id}
													value={role.id}
													keywords={[role.name]}
													onSelect={() => toggleRole(role.id)}
												>
													<div className="flex flex-col flex-1">
														<span className="font-medium">{role.name}</span>
														{role.description && (
															<span className="text-xs text-muted-foreground">
																{role.description}
															</span>
														)}
													</div>
													<Check
														className={cn(
															"ml-auto h-4 w-4",
															selectedRoleIds.has(role.id)
																? "opacity-100"
																: "opacity-0",
														)}
													/>
												</CommandItem>
											))}
										</CommandGroup>
									</CommandList>
								</Command>
							</PopoverContent>
						</Popover>
						{selectedRoleNames.length > 0 && (
							<div className="flex flex-wrap gap-1 mt-1">
								{selectedRoleNames.map(({ id, name }) => (
									<Badge key={id} variant="secondary" className="text-xs">
										{name}
										<button
											type="button"
											className="ml-1 rounded-full outline-none hover:bg-muted"
											onClick={() => removeRole(id)}
										>
											<X className="h-3 w-3" />
										</button>
									</Badge>
								))}
							</div>
						)}
						<p className="text-xs text-muted-foreground">
							Roles determine which forms this user can access
						</p>
					</div>
				)}

				{isPlatformAdmin && isPromoting && (
					<Alert>
						<Shield className="h-4 w-4" />
						<AlertDescription>
							You are promoting this user to Platform
							Administrator. They will gain unrestricted access
							to all features, organizations, and settings.
						</AlertDescription>
					</Alert>
				)}

				{isDemoting && (
					<Alert variant="destructive">
						<AlertTriangle className="h-4 w-4" />
						<AlertDescription>
							You are demoting this user from Platform
							Administrator to Organization User. They will
							lose access to all other organizations and
							platform settings.
						</AlertDescription>
					</Alert>
				)}

				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={isSaving}
					>
						Cancel
					</Button>
					<Button type="submit" disabled={isSaving}>
						{isSaving && (
							<Loader2 className="mr-2 h-4 w-4 animate-spin" />
						)}
						Save Changes
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	);
}

export function EditUserDialog({
	user,
	open,
	onOpenChange,
}: EditUserDialogProps) {
	if (!user) return null;

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			{open && (
				<EditUserDialogContent
					user={user}
					onOpenChange={onOpenChange}
				/>
			)}
		</Dialog>
	);
}
