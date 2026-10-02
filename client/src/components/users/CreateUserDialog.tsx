import { UserLookupNotice } from "./UserLookupNotice";
import { useState, useMemo, useEffect, useRef } from "react";
import { getErrorMessage } from "@/lib/api-error";
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
import { Switch } from "@/components/ui/switch";
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
import { Shield, AlertCircle, Loader2, ChevronsUpDown, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useCreateUser } from "@/hooks/useUsers";
import { useRoles, useAssignUsersToRole } from "@/hooks/useRoles";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useAuthorization } from "@/services/authorization";
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
	const [isExternal, setIsExternal] = useState(false);
	const [orgId, setOrgId] = useState<string>("");
	const [validationError, setValidationError] = useState<string | null>(null);
	const errorRef = useRef<HTMLDivElement>(null);
	const submitBusy = useRef(false);
	const [submitting, setSubmitting] = useState(false);
	useEffect(() => {
		if (validationError) {
			errorRef.current?.focus();
			errorRef.current?.scrollIntoView?.({ block: "nearest" });
		}
	}, [validationError]);
	const [selectedRoleIds, setSelectedRoleIds] = useState<Set<string>>(
		new Set(),
	);
	const [rolesPopoverOpen, setRolesPopoverOpen] = useState(false);

	const createMutation = useCreateUser();
	const [createdUser, setCreatedUser] = useState<Awaited<
		ReturnType<typeof createMutation.mutateAsync>
	> | null>(null);
	const completedRoles = useRef(new Set<string>());
	const assignUsersToRole = useAssignUsersToRole({ toast: false });
	const authorization = useAuthorization();
	// Platform Admins, and the roles granted at invite, are for Platform
	// Admins to set; anyone else invites ordinary users and assigns roles
	// afterwards from Roles & access.
	const callerIsAdmin = authorization.isPlatformAdmin;
	const assignsRoles = callerIsAdmin && !isPlatformAdmin;
	const organizationQuery = useOrganizations();
	const { data: allOrganizations, isLoading: orgsLoading } =
		organizationQuery;
	const organizations = allOrganizations?.filter((org: Organization) =>
		authorization.canAt("users.readwrite", { kind: "org", id: org.id }),
	);
	const roleCatalog = useRoles({ enabled: callerIsAdmin });
	const { data: allRoles } = roleCatalog;
	const lookupsReady =
		organizations !== undefined &&
		!organizationQuery.isError &&
		(!assignsRoles || (allRoles !== undefined && !roleCatalog.isError));

	const roles = useMemo(() => (allRoles ?? []) as Role[], [allRoles]);

	// Find the provider org (for auto-selecting when platform admin is chosen)
	const providerOrg = allOrganizations?.find(
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
		if (submitBusy.current || (!createdUser && !lookupsReady)) return;

		if (!validateForm()) {
			return;
		}

		submitBusy.current = true;
		setSubmitting(true);
		let result = createdUser;
		try {
			if (!result) {
				result = await createMutation.mutateAsync({
					body: {
						email: email.trim(),
						name: displayName.trim(),
						is_active: true,
						is_superuser: isPlatformAdmin,
						is_external: !isPlatformAdmin && isExternal,
						organization_id: orgId || null,
						invite: true,
						trigger_automation: false,
					},
				});
				setCreatedUser(result);
			}

			// Assign roles if any selected
			if (assignsRoles && selectedRoleIds.size > 0 && result?.id) {
				for (const roleId of selectedRoleIds) {
					if (completedRoles.current.has(roleId)) continue;
					await assignUsersToRole.mutateAsync({
						params: { path: { role_id: roleId } },
						body: { user_ids: [result.id] },
					});
					completedRoles.current.add(roleId);
				}
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
			const errorMessage = getErrorMessage(
				error,
				"Failed to create user",
			);
			setValidationError(
				result?.id
					? `The user was created, but some role assignments are incomplete. Retry to finish assigning roles. ${errorMessage}`
					: errorMessage,
			);
		} finally {
			submitBusy.current = false;
			setSubmitting(false);
		}
	};

	const isSaving =
		submitting || createMutation.isPending || assignUsersToRole.isPending;

	return (
		<DialogContent
			onEscapeKeyDown={(event) => {
				if (submitBusy.current) event.preventDefault();
			}}
			onInteractOutside={(event) => {
				if (submitBusy.current) event.preventDefault();
			}}
			showCloseButton={false}
			className="flex h-[100dvh] w-full max-w-none flex-col gap-0 overflow-hidden rounded-none border-border/70 p-0 shadow-xl motion-reduce:transition-none motion-reduce:animate-none sm:h-auto sm:max-h-[min(90dvh,44rem)] sm:w-[min(92vw,500px)] sm:rounded-[var(--bf-radius-feature)]"
		>
			<DialogHeader className="shrink-0 border-b border-border/70 px-4 pb-4 pt-[max(1rem,env(safe-area-inset-top))] text-left sm:px-6">
				<div className="flex items-start gap-3">
					<div className="min-w-0 flex-1">
						<DialogTitle className="text-pretty break-words">
							Create New User
						</DialogTitle>
						<DialogDescription className="mt-1.5 text-sm leading-5">
							Add a new user to the platform before they log in
							for the first time
						</DialogDescription>
					</div>
					<Button
						type="button"
						variant="ghost"
						size="icon-lg"
						onClick={() => onOpenChange(false)}
						aria-label="Close dialog"
						disabled={isSaving}
						className="h-11 w-11 shrink-0 rounded-[var(--bf-radius-control)] border border-border/70 bg-background/90 text-foreground hover:bg-muted motion-reduce:transition-none"
					>
						<X className="h-5 w-5" />
					</Button>
				</div>
			</DialogHeader>

			<form
				onSubmit={handleSubmit}
				className="flex min-h-0 flex-1 flex-col overflow-hidden"
			>
				<div
					inert={isSaving}
					aria-busy={isSaving}
					className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-6"
				>
					{validationError && (
						<Alert
							variant="destructive"
							ref={errorRef}
							tabIndex={-1}
							className="outline-none"
						>
							<AlertCircle className="h-4 w-4" />
							<AlertDescription>
								{validationError}
							</AlertDescription>
						</Alert>
					)}

					<fieldset
						disabled={Boolean(createdUser)}
						inert={Boolean(createdUser)}
						className="space-y-4"
					>
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

						{callerIsAdmin && (
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
						)}

						<div className="space-y-2">
							<Label htmlFor="organization">Organization</Label>
							<UserLookupNotice
								resource="organizations"
								loading={orgsLoading}
								failed={Boolean(organizationQuery.isError)}
								retrying={organizationQuery.isFetching}
								onRetry={() => void organizationQuery.refetch()}
							/>
							<Combobox
								id="organization"
								value={orgId}
								onValueChange={setOrgId}
								disabled={
									isPlatformAdmin ||
									orgsLoading ||
									organizationQuery.isError
								}
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
									: callerIsAdmin
										? "The organization this user belongs to"
										: "Organizations where you can invite users"}
							</p>
						</div>

						{!isPlatformAdmin && (
							<div className="flex items-start justify-between gap-4 rounded-[var(--bf-radius-surface)] border border-border/70 p-4">
								<div className="space-y-0.5">
									<Label htmlFor="external">
										External user
									</Label>
									<p className="text-xs leading-5 text-muted-foreground">
										Sees only what the Everyone tier or an
										explicit role grant allows — excluded
										from &ldquo;Everyone except external
										users&rdquo; content
									</p>
								</div>
								<Switch
									id="external"
									checked={isExternal}
									onCheckedChange={setIsExternal}
								/>
							</div>
						)}

						{assignsRoles && (
							<div className="space-y-2">
								<Label htmlFor="create-user-roles">Roles</Label>
								<UserLookupNotice
									resource="roles"
									loading={allRoles === undefined}
									failed={Boolean(roleCatalog.isError)}
									retrying={roleCatalog.isFetching}
									onRetry={() => void roleCatalog.refetch()}
								/>
								<Popover
									open={rolesPopoverOpen}
									onOpenChange={setRolesPopoverOpen}
								>
									<PopoverTrigger asChild>
										<Button
											variant="outline"
											role="combobox"
											id="create-user-roles"
											aria-label="Roles"
											disabled={
												allRoles === undefined ||
												roleCatalog.isError
											}
											aria-expanded={rolesPopoverOpen}
											className="h-11 w-full justify-between font-normal"
										>
											<span
												className={cn(
													"truncate",
													selectedRoleIds.size ===
														0 &&
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
										variant="picker"
										className="p-0"
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
															keywords={[
																role.name,
															]}
															data-checked={selectedRoleIds.has(
																role.id,
															)}
															onSelect={() =>
																toggleRole(
																	role.id,
																)
															}
														>
															<div className="flex flex-1 flex-col">
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
														</CommandItem>
													))}
												</CommandGroup>
											</CommandList>
										</Command>
									</PopoverContent>
								</Popover>
								{selectedRoleNames.length > 0 && (
									<div className="mt-1 flex flex-wrap gap-1">
										{selectedRoleNames.map(
											({ id, name }) => (
												<Badge
													key={id}
													variant="secondary"
													className="h-auto min-h-11 max-w-full pr-0 text-xs whitespace-normal [overflow-wrap:anywhere]"
												>
													{name}
													<button
														type="button"
														className="ml-1 flex size-11 shrink-0 items-center justify-center rounded-[var(--bf-radius-control)] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
														aria-label={`Remove ${name} role`}
														onClick={() =>
															removeRole(id)
														}
													>
														<X className="h-3 w-3" />
													</button>
												</Badge>
											),
										)}
									</div>
								)}
								<p className="text-xs text-muted-foreground">
									Roles determine which forms this user can
									access
								</p>
							</div>
						)}

						{isPlatformAdmin && (
							<Alert>
								<Shield className="h-4 w-4" />
								<AlertDescription>
									Platform administrators have unrestricted
									access to all features, organizations, and
									settings. Use this role carefully.
								</AlertDescription>
							</Alert>
						)}
					</fieldset>
				</div>

				<DialogFooter className="shrink-0 border-t border-border/70 px-4 py-4 sm:px-6">
					<Button
						type="button"
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={isSaving}
						className="h-11"
					>
						{createdUser ? "Close" : "Cancel"}
					</Button>
					<Button
						type="submit"
						disabled={isSaving || (!createdUser && !lookupsReady)}
						className="h-11"
					>
						{isSaving && (
							<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
						)}
						{createdUser ? "Retry role assignments" : "Create User"}
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
				key={registrationLink?.url ?? "closed"}
				open={registrationLink !== null}
				email={registrationLink?.email}
				url={registrationLink?.url}
				canSendEmail={inviteAutomationConfigured}
				isSendingEmail={sendInvite.isPending}
				onSendEmail={async () => {
					if (!registrationLink) return;
					await sendInvite.mutateAsync({
						userId: registrationLink.userId,
						registrationUrl: registrationLink.url,
					});
					toast.success("Registration email sent");
					setRegistrationLink(null);
				}}
				onOpenChange={(nextOpen) => {
					if (!nextOpen) setRegistrationLink(null);
				}}
			/>
		</>
	);
}
