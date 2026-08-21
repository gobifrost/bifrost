import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Shield } from "lucide-react";
import { toast } from "sonner";

import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
	RoleAssignmentEditor,
	areRoleAssignmentsEqual,
	roleAssignmentKey,
	toRoleAssignmentDrafts,
	validateBoundaries,
	type RoleAssignmentDraft,
} from "@/components/users/RoleAssignmentEditor";
import { useOrganizationGroups, useOrganizations } from "@/hooks/useOrganizations";
import {
	useAssignUsersToRole,
	useRemoveUserFromRole,
	useRoles,
} from "@/hooks/useRoles";
import { useUserRoles } from "@/hooks/useUsers";
import {
	organizationBoundary,
	useAdministrativeBoundary,
} from "@/hooks/useAdministrativeBoundary";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];

interface UserRolesDialogProps {
	user: User | undefined;
	open: boolean;
	onClose: () => void;
}

function UserRolesDialogContent({
	user,
	onClose,
}: {
	user: User;
	onClose: () => void;
}) {
	const queryClient = useQueryClient();
	const administrativeBoundary =
		useAdministrativeBoundary("organizations.read");
	const { data: userRoles, isLoading: assignmentsLoading } = useUserRoles(user.id, organizationBoundary(user.organization_id));
	const { data: roles = [], isLoading: rolesLoading } = useRoles(administrativeBoundary);
	const { data: organizations = [], isLoading: organizationsLoading } = useOrganizations({ boundary: administrativeBoundary });
	const { data: organizationGroups = [], isLoading: groupsLoading } = useOrganizationGroups({ boundary: administrativeBoundary });
	const assignMutation = useAssignUsersToRole();
	const removeMutation = useRemoveUserFromRole();
	const initialAssignments = useMemo(() => toRoleAssignmentDrafts(userRoles), [userRoles]);
	const platformAdminRoleIds = useMemo(
		() => roles.filter((role) => role.name === "Platform Admin").map((role) => role.id),
		[roles],
	);
	const [assignmentEdits, setAssignmentEdits] = useState<RoleAssignmentDraft[] | null>(null);
	const assignments = assignmentEdits ?? initialAssignments;

	const isLoading =
		assignmentsLoading || rolesLoading || organizationsLoading || groupsLoading;
	const isSaving = assignMutation.isPending || removeMutation.isPending;
	const hasValidationErrors = assignments.some((assignment) => {
		const role = roles.find((candidate) => candidate.id === assignment.role_id);
		return Boolean(
			validateBoundaries(
				assignment.boundaries,
				role ? platformAdminRoleIds.includes(role.id) : false,
			),
		);
	});
	const hasChanges =
		assignmentEdits !== null &&
		!areRoleAssignmentsEqual(initialAssignments, assignments);

	const handleSave = async () => {
		if (hasValidationErrors) {
			toast.error("Choose access for every role");
			return;
		}

		const initialByRole = new Map(
			initialAssignments.map((assignment) => [assignment.role_id, assignment]),
		);
		const nextByRole = new Map(
			assignments.map((assignment) => [assignment.role_id, assignment]),
		);

		try {
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
			for (const initial of initialAssignments) {
				if (nextByRole.has(initial.role_id)) continue;
				await removeMutation.mutateAsync({
					params: { path: { role_id: initial.role_id, user_id: user.id } },
				});
			}

			await queryClient.invalidateQueries({
				queryKey: ["get", "/api/users/{user_id}/role-assignments"],
			});
			toast.success("Role access updated");
			onClose();
		} catch (error) {
			toast.error("Failed to update role access", {
				description: error instanceof Error ? error.message : undefined,
			});
		}
	};

	return (
		<DialogContent className="max-h-[min(90vh,52rem)] overflow-y-auto sm:max-w-3xl">
			<DialogHeader>
				<DialogTitle className="flex items-center gap-2">
					<Shield className="h-5 w-5" />
					Roles and access
				</DialogTitle>
				<DialogDescription>
					Choose what {user.name || user.email} can do and where each role applies.
				</DialogDescription>
			</DialogHeader>

			{isLoading ? (
				<div className="space-y-3 py-2">
					<Skeleton className="h-10 w-full" />
					<Skeleton className="h-24 w-full" />
				</div>
			) : (
				<RoleAssignmentEditor
					roles={roles}
					value={assignments}
					organizations={organizations}
					organizationGroups={organizationGroups}
					platformAdminRoleIds={platformAdminRoleIds}
					defaultBoundary={
						user.organization_id
							? { boundary_kind: "organization", organization_id: user.organization_id }
							: null
					}
					disabled={isSaving}
					onChange={setAssignmentEdits}
				/>
			)}

			<DialogFooter>
				<Button type="button" variant="outline" onClick={onClose} disabled={isSaving}>
					Cancel
				</Button>
				<Button
					type="button"
					onClick={handleSave}
					disabled={isLoading || isSaving || hasValidationErrors || !hasChanges}
				>
					{isSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
					Save access
				</Button>
			</DialogFooter>
		</DialogContent>
	);
}

export function UserRolesDialog({ user, open, onClose }: UserRolesDialogProps) {
	if (!user) return null;

	return (
		<Dialog open={open} onOpenChange={(next) => !next && onClose()}>
			{open ? <UserRolesDialogContent user={user} onClose={onClose} /> : null}
		</Dialog>
	);
}
