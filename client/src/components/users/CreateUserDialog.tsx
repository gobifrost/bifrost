import { useState, useMemo } from "react";
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
import {
	Shield,
	AlertCircle,
	Loader2,
	Check,
	ChevronsUpDown,
	X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";
import { useCreateUser } from "@/hooks/useUsers";
import { useRoles, useAssignUsersToRole } from "@/hooks/useRoles";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useEventSources } from "@/services/events";
import { useSendInvite } from "@/hooks/useUserInvites";
import { RegistrationLinkDialog } from "@/components/users/RegistrationLinkDialog";
import { toast } from "sonner";
import type { components } from "@/lib/v1";

type Organization = components["schemas"]["OrganizationPublic"];
type Role = components["schemas"]["RolePublic"];

interface CreateUserDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

// Extract dialog content to separate component for key-based remounting
function CreateUserDialogContent({
	onOpenChange,
	onRegistrationLinkCreated,
}: {
	onOpenChange: (open: boolean) => void;
	onRegistrationLinkCreated: (
		userId: string,
		email: string,
		url: string,
	) => void;
}) {
	const [email, setEmail] = useState("");
	const [displayName, setDisplayName] = useState("");
	const [isPlatformAdmin, setIsPlatformAdmin] = useState(false);
	const [orgId, setOrgId] = useState<string>("");
	const [validationError, setValidationError] = useState<string | null>(null);
	const [selectedRoleIds, setSelectedRoleIds] = useState<Set<string>>(
		new Set(),
	);
	const [rolesPopoverOpen, setRolesPopoverOpen] = useState(false);

	const queryClient = useQueryClient();
	const createMutation = useCreateUser();
	const assignUsersToRole = useAssignUsersToRole();
	const { data: organizations, isLoading: orgsLoading } = useOrganizations();
	const { data: allRoles } = useRoles();

	const roles = useMemo(() => (allRoles ?? []) as Role[], [allRoles]);

	// Find the provider org (for auto-selecting when platform admin is chosen)
	const providerOrg = organizations?.find(
		(org: Organization) => org.is_provider,
	);

	// Auto-select provider org when switching to platform admin
	const handleUserTypeChange = (value: string) => {
		const isAdmin = value === "platform";
		setIsPlatformAdmin(isAdmin);
		if (isAdmin && providerOrg) {
			setOrgId(providerOrg.id);
		} else if (!isAdmin && orgId === providerOrg?.id) {
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
		if (!email || !email.includes("@")) {
			setValidationError("Please enter a valid email address");
			return false;
		}
		if (!displayName || displayName.trim().length === 0) {
			setValidationError("Please enter a display name");
			return false;
		}
		if (!orgId) {
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

		try {
			const result = await createMutation.mutateAsync({
				body: {
					email: email.trim(),
					name: displayName.trim(),
					is_active: true,
					is_superuser: isPlatformAdmin,
					organization_id: orgId || null,
					invite: true,
					trigger_automation: false,
				},
			});

			// Assign roles if any selected
			if (selectedRoleIds.size > 0 && result?.id) {
				for (const roleId of selectedRoleIds) {
					await assignUsersToRole.mutateAsync({
						params: { path: { role_id: roleId } },
						body: { user_ids: [result.id] },
					});
				}
				await queryClient.invalidateQueries({
					queryKey: ["get", "/api/users/{user_id}/roles"],
				});
			}

			toast.success("User created successfully", {
				description: `${displayName.trim()} (${email.trim()}) has been added to the platform`,
			});

			onOpenChange(false);
			if (result?.id && result?.registration_url) {
				onRegistrationLinkCreated(
					result.id,
					email.trim(),
					result.registration_url,
				);
			}
		} catch (error) {
			const errorMessage =
				error instanceof Error
					? error.message
					: "Unknown error occurred";
			toast.error("Failed to create user", {
				description: errorMessage,
			});
		}
	};

	const isSaving = createMutation.isPending || assignUsersToRole.isPending;

	return (
		<DialogContent className="sm:max-w-[500px]">
			<DialogHeader>
				<DialogTitle>Create New User</DialogTitle>
				<DialogDescription>
					Add a new user to the platform before they log in for the
					first time
				</DialogDescription>
			</DialogHeader>

			<form onSubmit={handleSubmit} className="space-y-4 mt-4">
				{validationError && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>{validationError}</AlertDescription>
					</Alert>
				)}

				<div className="space-y-2">
					<Label htmlFor="email">Email Address</Label>
					<Input
						id="email"
						type="email"
						placeholder="user@example.com"
						value={email}
						onChange={(e) => setEmail(e.target.value)}
						required
					/>
					<p className="text-xs text-muted-foreground">
						The user's email address for authentication
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
					<p className="text-xs text-muted-foreground">
						The name that will be shown in the platform
					</p>
				</div>

				<div className="space-y-2">
					<Label htmlFor="userType">User Type</Label>
					<Combobox
						id="userType"
						value={isPlatformAdmin ? "platform" : "org"}
						onValueChange={handleUserTypeChange}
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
						disabled={isPlatformAdmin}
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
				{!isPlatformAdmin && (
					<div className="space-y-2">
						<Label>Roles</Label>
						<Popover
							open={rolesPopoverOpen}
							onOpenChange={setRolesPopoverOpen}
						>
							<PopoverTrigger asChild>
								<Button
									variant="outline"
									role="combobox"
									aria-expanded={rolesPopoverOpen}
									className="w-full justify-between font-normal"
								>
									<span
										className={cn(
											"truncate",
											selectedRoleIds.size === 0 &&
												"text-muted-foreground",
										)}
									>
										{selectedRoleIds.size === 0
											? "Select roles..."
											: `${selectedRoleIds.size} role${selectedRoleIds.size === 1 ? "" : "s"} selected`}
									</span>
									<ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
								</Button>
							</PopoverTrigger>
							<PopoverContent
								className="w-[var(--radix-popover-trigger-width)] p-0"
								align="start"
							>
								<Command>
									<CommandInput placeholder="Search roles..." />
									<CommandList className="max-h-48 overflow-y-auto">
										<CommandEmpty>
											No roles found.
										</CommandEmpty>
										<CommandGroup>
											{roles.map((role) => (
												<CommandItem
													key={role.id}
													value={role.id}
													keywords={[role.name]}
													onSelect={() =>
														toggleRole(role.id)
													}
												>
													<div className="flex flex-col flex-1">
														<span className="font-medium">
															{role.name}
														</span>
														{role.description && (
															<span className="text-xs text-muted-foreground">
																{
																	role.description
																}
															</span>
														)}
													</div>
													<Check
														className={cn(
															"ml-auto h-4 w-4",
															selectedRoleIds.has(
																role.id,
															)
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
									<Badge
										key={id}
										variant="secondary"
										className="text-xs"
									>
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

				{isPlatformAdmin && (
					<Alert>
						<Shield className="h-4 w-4" />
						<AlertDescription>
							Platform administrators have unrestricted access to
							all features, organizations, and settings. Use this
							role carefully.
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
						Create User
					</Button>
				</DialogFooter>
			</form>
		</DialogContent>
	);
}

export function CreateUserDialog({
	open,
	onOpenChange,
}: CreateUserDialogProps) {
	const [registrationLink, setRegistrationLink] = useState<{
		userId: string;
		email: string;
		url: string;
	} | null>(null);
	const sendInvite = useSendInvite();
	const { data: eventSources } = useEventSources({
		sourceType: "topic",
		limit: 100,
	});
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
				{open && (
					<CreateUserDialogContent
						onOpenChange={onOpenChange}
						onRegistrationLinkCreated={(userId, email, url) =>
							setRegistrationLink({ userId, email, url })
						}
					/>
				)}
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
								error instanceof Error
									? error.message
									: "Unknown error occurred",
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
