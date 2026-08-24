import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { RegistrationLinkDialog } from "@/components/users/RegistrationLinkDialog";
import {
	RoleAssignmentEditor,
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
import { useOrganizationGroups, useOrganizations } from "@/hooks/useOrganizations";
import { useAssignUsersToRole, useRoles } from "@/hooks/useRoles";
import { useCreateUser } from "@/hooks/useUsers";
import { useSendInvite } from "@/hooks/useUserInvites";
import type { components } from "@/lib/v1";
import { useEventSources } from "@/services/events";
import {
	authorizationHeaders,
	organizationBoundary,
	useAdministrativeBoundary,
} from "@/hooks/useAdministrativeBoundary";

type Organization = components["schemas"]["OrganizationPublic"];

interface CreateUserDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

function CreateUserDialogContent({
	onOpenChange,
	onRegistrationLinkCreated,
}: {
	onOpenChange: (open: boolean) => void;
	onRegistrationLinkCreated: (userId: string, email: string, url: string) => void;
}) {
	const [email, setEmail] = useState("");
	const [displayName, setDisplayName] = useState("");
	const [isExternal, setIsExternal] = useState(false);
	const [orgId, setOrgId] = useState("");
	const [assignments, setAssignments] = useState<RoleAssignmentDraft[]>([]);
	const [validationError, setValidationError] = useState<string | null>(null);

	const queryClient = useQueryClient();
	const administrativeBoundary =
		useAdministrativeBoundary("organizations.read");
	const createMutation = useCreateUser();
	const assignMutation = useAssignUsersToRole();
	const { data: organizations = [], isLoading: organizationsLoading } = useOrganizations({ boundary: administrativeBoundary });
	const { data: organizationGroups = [], isLoading: groupsLoading } = useOrganizationGroups({ boundary: administrativeBoundary });
	const { data: roles = [], isLoading: rolesLoading } = useRoles(administrativeBoundary);
	const platformAdminRoleIds = useMemo(
		() => roles.filter((role) => role.name === "Platform Admin").map((role) => role.id),
		[roles],
	);
	const hasInvalidAssignment = assignments.some((assignment) =>
		Boolean(
			validateBoundaries(
				assignment.boundaries,
				platformAdminRoleIds.includes(assignment.role_id),
			),
		),
	);

	const validateForm = () => {
		if (!email.includes("@")) {
			setValidationError("Please enter a valid email address");
			return false;
		}
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

		try {
			const result = await createMutation.mutateAsync({
				headers: authorizationHeaders(organizationBoundary(orgId)),
				body: {
					email: email.trim(),
					name: displayName.trim(),
					is_active: true,
					is_superuser: false,
					is_external: isExternal,
					organization_id: orgId,
					invite: true,
					trigger_automation: false,
				},
			});

			if (result?.id) {
				for (const assignment of assignments) {
					await assignMutation.mutateAsync({
						params: { path: { role_id: assignment.role_id } },
						body: { user_ids: [result.id], boundaries: assignment.boundaries },
					});
				}
				await queryClient.invalidateQueries({
					queryKey: ["get", "/api/users/{user_id}/role-assignments"],
				});
			}

			toast.success("User created successfully", {
				description: `${displayName.trim()} (${email.trim()}) has been added to the platform`,
			});
			onOpenChange(false);
			if (result?.id && result.registration_url) {
				onRegistrationLinkCreated(result.id, email.trim(), result.registration_url);
			}
		} catch (error) {
			toast.error("Failed to create user", {
				description: error instanceof Error ? error.message : "Unknown error occurred",
			});
		}
	};

	const isSaving = createMutation.isPending || assignMutation.isPending;
	const roleDataLoading = rolesLoading || organizationsLoading || groupsLoading;

	return (
		<DialogContent className="max-h-[min(90vh,56rem)] overflow-y-auto sm:max-w-3xl">
			<DialogHeader>
				<DialogTitle>Create user</DialogTitle>
				<DialogDescription>
					Add an account, then choose its roles and access boundaries.
				</DialogDescription>
			</DialogHeader>

			<form onSubmit={handleSubmit} className="mt-2 space-y-5">
				{validationError ? (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{validationError}</AlertDescription>
					</Alert>
				) : null}

				<div className="grid gap-4 sm:grid-cols-2">
					<div className="space-y-2">
						<Label htmlFor="email">Email address</Label>
						<Input
							id="email"
							type="email"
							placeholder="user@example.com"
							value={email}
							onChange={(event) => setEmail(event.target.value)}
							required
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="displayName">Display name</Label>
						<Input
							id="displayName"
							placeholder="John Doe"
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
				</div>

				<div className="flex items-center justify-between rounded-lg border p-4">
					<div className="space-y-0.5">
						<Label htmlFor="external">External user</Label>
						<p className="text-xs text-muted-foreground">
							External users require explicit resource access in addition to their roles.
						</p>
					</div>
					<Switch id="external" checked={isExternal} onCheckedChange={setIsExternal} />
				</div>

				<div className="border-t pt-5">
					<h3 className="font-medium">Roles and access</h3>
					<p className="mb-4 mt-1 text-sm text-muted-foreground">
						New roles default to the home organization. You can select multiple
						organizations, organization groups, Managed organizations, or Platform.
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
						disabled={isSaving}
						isLoading={roleDataLoading}
						onChange={setAssignments}
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
						Create user
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	);
}

export function CreateUserDialog({ open, onOpenChange }: CreateUserDialogProps) {
	const [registrationLink, setRegistrationLink] = useState<{
		userId: string;
		email: string;
		url: string;
	} | null>(null);
	const sendInvite = useSendInvite();
	const { data: eventSources } = useEventSources({ sourceType: "topic", limit: 100 });
	const inviteAutomationConfigured =
		eventSources?.items?.some(
			(source) =>
				source.is_active &&
				source.event_type === "user.invited" &&
				source.subscription_count > 0,
		) ?? false;

	return (
		<>
			<Dialog open={open} onOpenChange={onOpenChange}>
				{open ? (
					<CreateUserDialogContent
						onOpenChange={onOpenChange}
						onRegistrationLinkCreated={(userId, userEmail, url) =>
							setRegistrationLink({ userId, email: userEmail, url })
						}
					/>
				) : null}
			</Dialog>
			<RegistrationLinkDialog
				open={registrationLink !== null}
				email={registrationLink?.email}
				url={registrationLink?.url}
				canSendEmail={inviteAutomationConfigured}
				isSendingEmail={sendInvite.isPending}
				onSendEmail={async () => {
					if (!registrationLink) return;
					try {
						await sendInvite.mutateAsync({
							userId: registrationLink.userId,
							registrationUrl: registrationLink.url,
						});
						toast.success("Registration email sent");
						setRegistrationLink(null);
					} catch (error) {
						toast.error("Failed to send registration email", {
							description:
								error instanceof Error ? error.message : "Unknown error occurred",
						});
					}
				}}
				onOpenChange={(nextOpen) => {
					if (!nextOpen) setRegistrationLink(null);
				}}
			/>
		</>
	);
}
