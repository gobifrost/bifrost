import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
	RoleAssignmentEditor,
	areRoleAssignmentsEqual,
	roleAssignmentKey,
	toRoleAssignmentDrafts,
	validateBoundaries,
	type RoleAssignmentDraft,
} from "@/components/users/RoleAssignmentEditor";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/contexts/AuthContext";
import {
	authorizationHeaders,
	organizationBoundary,
	useAdministrativeBoundary,
} from "@/hooks/useAdministrativeBoundary";
import { useOrganizationGroups, useOrganizations } from "@/hooks/useOrganizations";
import {
	useAssignUsersToRole,
	useRemoveUserFromRole,
	useRoles,
} from "@/hooks/useRoles";
import { useUpdateUser, useUserRoles } from "@/hooks/useUsers";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];
type Organization = components["schemas"]["OrganizationPublic"];

interface EditUserDialogProps {
	user: User | undefined;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

function EditUserDialogContent({
	user,
	onOpenChange,
}: {
	user: User;
	onOpenChange: (open: boolean) => void;
}) {
	const [displayName, setDisplayName] = useState(user.name || "");
	const [isActive, setIsActive] = useState(user.is_active);
	const [isExternal, setIsExternal] = useState(user.is_external);
	const [orgId, setOrgId] = useState(user.organization_id || "");
	const [validationError, setValidationError] = useState<string | null>(null);
	const [assignmentEdits, setAssignmentEdits] = useState<RoleAssignmentDraft[] | null>(null);

	const queryClient = useQueryClient();
	const administrativeBoundary =
		useAdministrativeBoundary("organizations.read");
	const userBoundary = organizationBoundary(user.organization_id);
	const updateMutation = useUpdateUser();
	const assignMutation = useAssignUsersToRole();
	const removeMutation = useRemoveUserFromRole();
	const { data: organizations = [], isLoading: organizationsLoading } = useOrganizations({ boundary: administrativeBoundary });
	const { data: organizationGroups = [], isLoading: groupsLoading } = useOrganizationGroups({ boundary: administrativeBoundary });
	const { data: roles = [], isLoading: rolesLoading } = useRoles(administrativeBoundary);
	const { data: userRoles, isLoading: assignmentsLoading } = useUserRoles(user.id, userBoundary);
	const { user: currentUser } = useAuth();

	const initialAssignments = useMemo(
		() => toRoleAssignmentDrafts(userRoles),
		[userRoles],
	);
	const platformAdminRoleIds = useMemo(
		() => roles.filter((role) => role.name === "Platform Admin").map((role) => role.id),
		[roles],
	);

	const assignments = assignmentEdits ?? initialAssignments;

	const isEditingSelf = currentUser?.id === user.id;
	const isLoadingAssignments =
		assignmentsLoading || rolesLoading || organizationsLoading || groupsLoading;
	const isSaving =
		updateMutation.isPending || assignMutation.isPending || removeMutation.isPending;
	const hasInvalidAssignment = assignments.some((assignment) =>
		Boolean(
			validateBoundaries(
				assignment.boundaries,
				platformAdminRoleIds.includes(assignment.role_id),
			),
		),
	);

	const validateForm = () => {
		if (!displayName.trim()) {
			setValidationError("Please enter a display name");
			return false;
		}
		if (!orgId) {
			setValidationError("Please select an organization");
			return false;
		}
		if (hasInvalidAssignment) {
			setValidationError("Choose access for every assigned role");
			return false;
		}
		setValidationError(null);
		return true;
	};

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!validateForm()) return;

		const body = {
			name: displayName.trim() !== (user.name || "") ? displayName.trim() : null,
			is_active: !isEditingSelf && isActive !== user.is_active ? isActive : null,
			is_superuser: null,
			organization_id:
				!isEditingSelf && orgId !== (user.organization_id || "") ? orgId : null,
			is_external:
				!isEditingSelf && isExternal !== user.is_external ? isExternal : null,
		};
		const hasRoleChanges = !areRoleAssignmentsEqual(initialAssignments, assignments);
		const hasUserChanges = Object.values(body).some((value) => value !== null);

		if (!hasUserChanges && !hasRoleChanges) {
			toast.info("No changes to save");
			onOpenChange(false);
			return;
		}

		try {
			if (hasUserChanges) {
				await updateMutation.mutateAsync({
					headers: authorizationHeaders(userBoundary),
					params: { path: { user_id: user.id } },
					body,
				});
			}

			if (hasRoleChanges) {
				const initialByRole = new Map(
					initialAssignments.map((assignment) => [assignment.role_id, assignment]),
				);
				const nextRoleIds = new Set(assignments.map((assignment) => assignment.role_id));
				for (const assignment of assignments) {
					const initial = initialByRole.get(assignment.role_id);
					if (
						initial &&
						roleAssignmentKey(initial) === roleAssignmentKey(assignment)
					) continue;
					await assignMutation.mutateAsync({
						params: { path: { role_id: assignment.role_id } },
						body: { user_ids: [user.id], boundaries: assignment.boundaries },
					});
				}
				for (const assignment of initialAssignments) {
					if (nextRoleIds.has(assignment.role_id)) continue;
					await removeMutation.mutateAsync({
						params: { path: { role_id: assignment.role_id, user_id: user.id } },
					});
				}
				await queryClient.invalidateQueries({
					queryKey: ["get", "/api/users/{user_id}/role-assignments"],
				});
			}

			toast.success("User updated successfully", {
				description: `Changes to ${user.name || user.email} have been saved`,
			});
			onOpenChange(false);
		} catch (error) {
			toast.error("Failed to update user", {
				description: error instanceof Error ? error.message : "Unknown error occurred",
			});
		}
	};

	return (
		<DialogContent className="max-h-[min(90vh,56rem)] overflow-y-auto sm:max-w-3xl">
			<DialogHeader>
				<DialogTitle>Edit user</DialogTitle>
				<DialogDescription>
					Update account details, roles, and the boundaries where each role applies.
				</DialogDescription>
			</DialogHeader>

			<form onSubmit={handleSubmit} className="mt-2 space-y-5">
				{isEditingSelf ? (
					<Alert>
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							You are editing your own account. Another administrator must change your
							status, organization, or role access.
						</AlertDescription>
					</Alert>
				) : null}

				{validationError ? (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{validationError}</AlertDescription>
					</Alert>
				) : null}

				<div className="grid gap-4 sm:grid-cols-2">
					<div className="space-y-2">
						<Label htmlFor="email-display">Email address</Label>
						<Input id="email-display" type="email" value={user.email} disabled />
					</div>
					<div className="space-y-2">
						<Label htmlFor="displayName">Display name</Label>
						<Input
							id="displayName"
							value={displayName}
							onChange={(event) => setDisplayName(event.target.value)}
							required
						/>
					</div>
				</div>

				<div className="space-y-2">
					<Label htmlFor="organization">Home organization</Label>
					<Combobox
						id="organization"
						value={orgId}
						onValueChange={setOrgId}
						disabled={isEditingSelf}
						options={organizations.map((org: Organization) => ({
							value: org.id,
							label: org.is_provider ? `${org.name} (Provider)` : org.name,
							description: org.domain ? `@${org.domain}` : undefined,
						}))}
						placeholder="Select an organization"
						searchPlaceholder="Search organizations..."
						emptyText="No organizations found."
						isLoading={organizationsLoading}
					/>
					<p className="text-xs text-muted-foreground">
						Changing the home organization does not rewrite existing role boundaries.
					</p>
				</div>

				<div className="grid gap-3 sm:grid-cols-2">
					<div className="flex items-center justify-between rounded-lg border p-4">
						<div className="space-y-0.5">
							<Label htmlFor="active">Account active</Label>
							<p className="text-xs text-muted-foreground">Controls sign-in access.</p>
						</div>
						<Switch
							id="active"
							checked={isActive}
							onCheckedChange={setIsActive}
							disabled={isEditingSelf}
						/>
					</div>
					<div className="flex items-center justify-between rounded-lg border p-4">
						<div className="space-y-0.5">
							<Label htmlFor="external">External user</Label>
							<p className="text-xs text-muted-foreground">Requires explicit access grants.</p>
						</div>
						<Switch
							id="external"
							checked={isExternal}
							onCheckedChange={setIsExternal}
							disabled={isEditingSelf}
						/>
					</div>
				</div>

				<div className="border-t pt-5">
					<h3 className="font-medium">Roles and access</h3>
					<p className="mb-4 mt-1 text-sm text-muted-foreground">
						A role defines what the user can do. Access selections define where it applies.
					</p>
					<RoleAssignmentEditor
						roles={roles}
						value={assignments}
						organizations={organizations}
						organizationGroups={organizationGroups}
						platformAdminRoleIds={platformAdminRoleIds}
						defaultBoundary={
							orgId ? { boundary_kind: "organization", organization_id: orgId } : null
						}
						disabled={isEditingSelf || isSaving}
						isLoading={isLoadingAssignments}
						onChange={setAssignmentEdits}
					/>
				</div>

				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={isSaving}
					>
						Cancel
					</Button>
					<Button type="submit" disabled={isSaving || hasInvalidAssignment}>
						{isSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
						Save changes
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	);
}

export function EditUserDialog({ user, open, onOpenChange }: EditUserDialogProps) {
	if (!user) return null;
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			{open ? <EditUserDialogContent user={user} onOpenChange={onOpenChange} /> : null}
		</Dialog>
	);
}
