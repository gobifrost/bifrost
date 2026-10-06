import { UserLookupNotice } from "./UserLookupNotice";
import { useState, useEffect, useRef } from "react";
import { getErrorMessage } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Switch } from "@/components/ui/switch";
import { AlertCircle, Loader2 } from "lucide-react";
import { useUpdateUser } from "@/hooks/useUsers";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useAuth } from "@/contexts/AuthContext";
import { orgTarget } from "@/lib/authorization";
import { cn } from "@/lib/utils";
import { useAuthorization } from "@/services/authorization";
import { toast } from "sonner";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];
type Organization = components["schemas"]["OrganizationPublic"];

const SUPPORT_HINT =
	"Only people who can manage users in this organization can change this.";
const LIFECYCLE_HINT =
	"Only people who can create, move, or delete users in this organization can change this.";

/** Which profile fields the caller may change on this user. */
export function useUserProfileAbilities(user: User) {
	const authorization = useAuthorization();
	const { user: currentUser } = useAuth();

	const isEditingSelf = !!(currentUser && user.id === currentUser.id);
	const target = orgTarget(user.organization_id);
	const blockedByProtection =
		user.is_protected && !authorization.isPlatformAdmin;
	const canSupport =
		!blockedByProtection && authorization.canAt("users.readwrite", target);
	const canLifecycle =
		!blockedByProtection &&
		authorization.canAt("users.lifecycle.readwrite", target);
	const canEditName = isEditingSelf || canSupport;
	const canEditStatus = !isEditingSelf && canSupport;
	const canEditLifecycle = !isEditingSelf && canLifecycle;
	// Platform Admins stay in the provider organization.
	const canEditOrg =
		canEditLifecycle &&
		!user.is_superuser &&
		authorization.canAnywhere("organizations.read");
	return {
		isEditingSelf,
		blockedByProtection,
		canEditName,
		canEditStatus,
		canEditLifecycle,
		canEditOrg,
		canSave: canEditName || canEditStatus || canEditLifecycle,
	};
}

/**
 * A user's profile: display name, status, organization and external flag,
 * each editable only where the caller's permissions allow. Used inside the
 * Edit profile dialog and on the person page's Profile tab.
 */
export function UserProfileForm({
	user,
	variant,
	onDone,
	onCancel,
	onBusyChange,
}: {
	user: User;
	/** The dialog scrolls its fields above a pinned footer; the page flows. */
	variant: "dialog" | "page";
	/** After a save, or when there was nothing to save. */
	onDone?: () => void;
	/** Shows Cancel (Close when nothing is editable). */
	onCancel?: () => void;
	onBusyChange?: (busy: boolean) => void;
}) {
	const [displayName, setDisplayName] = useState(user.name || "");
	const [isActive, setIsActive] = useState(user.is_active);
	const [isExternal, setIsExternal] = useState(user.is_external);
	const [orgId, setOrgId] = useState<string>(user.organization_id || "");
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

	const updateMutation = useUpdateUser();
	const authorization = useAuthorization();
	const organizationQuery = useOrganizations({
		enabled: authorization.canAnywhere("organizations.read"),
	});
	const { data: organizations, isLoading: orgsLoading } = organizationQuery;
	const {
		isEditingSelf,
		blockedByProtection,
		canEditName,
		canEditStatus,
		canEditLifecycle,
		canEditOrg,
		canSave,
	} = useUserProfileAbilities(user);

	const orgReady =
		!canEditOrg ||
		(organizations !== undefined && !organizationQuery.isError);
	// A move needs authority at the destination too.
	const destinationOrganizations = (organizations ?? []).filter(
		(org: Organization) =>
			org.id === user.organization_id ||
			authorization.canAt("users.lifecycle.readwrite", {
				kind: "org",
				id: org.id,
			}),
	);
	const currentOrgName = user.organization_id
		? (organizations?.find((org) => org.id === user.organization_id)
				?.name ?? "Unavailable")
		: "Global (no organization)";

	const validateForm = (): boolean => {
		if (!displayName || displayName.trim().length === 0) {
			setValidationError("Please enter a display name");
			return false;
		}
		setValidationError(null);
		return true;
	};

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (submitBusy.current || !orgReady || !canSave) return;

		if (!validateForm()) {
			return;
		}

		// Build request body - only send changed fields the caller may change
		const body = {
			name:
				canEditName && displayName.trim() !== (user.name || "")
					? displayName.trim()
					: null,
			is_active:
				canEditStatus && isActive !== user.is_active ? isActive : null,
			organization_id:
				canEditOrg && orgId !== (user.organization_id || "")
					? orgId || null
					: null,
			is_external:
				canEditLifecycle && isExternal !== user.is_external
					? isExternal
					: null,
		};

		if (
			body.name === null &&
			body.is_active === null &&
			body.organization_id === null &&
			body.is_external === null
		) {
			toast.info("No changes to save");
			onDone?.();
			return;
		}

		submitBusy.current = true;
		setSubmitting(true);
		try {
			await updateMutation.mutateAsync({
				params: { path: { user_id: user.id } },
				body,
			});

			toast.success("User updated successfully", {
				description: `Changes to ${user.name || user.email} have been saved`,
			});

			onDone?.();
		} catch (error) {
			const errorMessage = getErrorMessage(
				error,
				"Failed to update user",
			);
			setValidationError(errorMessage);
		} finally {
			submitBusy.current = false;
			setSubmitting(false);
		}
	};

	const isSaving = submitting || updateMutation.isPending;
	useEffect(() => {
		onBusyChange?.(isSaving);
	}, [isSaving, onBusyChange]);

	const inDialog = variant === "dialog";
	const showFooter = canSave || onCancel !== undefined;

	return (
		<form
			onSubmit={handleSubmit}
			className={cn(
				inDialog
					? "flex min-h-0 flex-1 flex-col overflow-hidden"
					: "max-w-2xl space-y-6",
			)}
		>
			<div
				inert={isSaving}
				aria-busy={isSaving}
				className={cn(
					"space-y-4",
					inDialog &&
						"min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6",
				)}
			>
				{isEditingSelf && (
					<Alert>
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							You are editing your own account. You can only
							change your display name. Status, organization, and
							role changes must be made by another administrator.
						</AlertDescription>
					</Alert>
				)}

				{validationError && (
					<Alert
						variant="destructive"
						ref={errorRef}
						tabIndex={-1}
						className="outline-none"
					>
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
						disabled={!canEditName}
						aria-describedby={
							canEditName ? undefined : "displayName-hint"
						}
						required
					/>
					{!canEditName && !blockedByProtection && (
						<p
							id="displayName-hint"
							className="text-xs text-muted-foreground"
						>
							{SUPPORT_HINT}
						</p>
					)}
				</div>

				<div className="space-y-2 rounded-[var(--bf-radius-surface)] bg-muted/50 p-4 ring-1 ring-foreground/5">
					<div className="flex items-center justify-between gap-4">
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
							disabled={!canEditStatus}
						/>
					</div>
					{!canEditStatus &&
						!isEditingSelf &&
						!blockedByProtection && (
							<p className="text-xs text-muted-foreground">
								{SUPPORT_HINT}
							</p>
						)}
				</div>

				<div className="space-y-2">
					<Label htmlFor="organization">Organization</Label>
					{canEditOrg ? (
						<>
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
									orgsLoading || organizationQuery.isError
								}
								options={destinationOrganizations.map(
									(org: Organization) => ({
										value: org.id,
										label: org.is_provider
											? `${org.name} (Provider)`
											: org.name,
										...(org.domain
											? {
													description: `@${org.domain}`,
												}
											: {}),
									}),
								)}
								placeholder="Select an organization..."
								searchPlaceholder="Search organizations..."
								emptyText="No organizations found."
								isLoading={orgsLoading}
							/>
							<p className="text-xs text-muted-foreground">
								The organization this user belongs to
							</p>
						</>
					) : (
						<>
							<Input
								id="organization"
								value={currentOrgName}
								disabled
								className="bg-muted"
							/>
							{user.is_superuser ? (
								<p className="text-xs text-muted-foreground">
									Platform Admins belong to the provider
									organization
								</p>
							) : (
								!isEditingSelf &&
								!blockedByProtection && (
									<p className="text-xs text-muted-foreground">
										{LIFECYCLE_HINT}
									</p>
								)
							)}
						</>
					)}
				</div>

				{!user.is_superuser && (
					<div className="space-y-2 rounded-[var(--bf-radius-surface)] border p-4">
						<div className="flex items-center justify-between gap-4">
							<div className="space-y-0.5">
								<Label htmlFor="external">External user</Label>
								<p className="text-xs text-muted-foreground">
									Sees only what the Everyone tier or an
									explicit role grant allows — excluded from
									&ldquo;Everyone except external users&rdquo;
									content
								</p>
							</div>
							<Switch
								id="external"
								checked={isExternal}
								onCheckedChange={setIsExternal}
								disabled={!canEditLifecycle}
							/>
						</div>
						{!canEditLifecycle &&
							!isEditingSelf &&
							!blockedByProtection && (
								<p className="text-xs text-muted-foreground">
									{LIFECYCLE_HINT}
								</p>
							)}
					</div>
				)}
			</div>
			{showFooter && (
				<div
					className={cn(
						"flex flex-col-reverse gap-2 sm:flex-row sm:justify-end",
						inDialog &&
							"shrink-0 border-t border-border/70 px-4 py-4 sm:px-6",
					)}
				>
					{onCancel && (
						<Button
							type="button"
							variant="outline"
							onClick={onCancel}
							disabled={isSaving}
							className="h-11"
						>
							{canSave ? "Cancel" : "Close"}
						</Button>
					)}
					{canSave && (
						<Button
							type="submit"
							disabled={isSaving || !orgReady}
							className="h-11"
						>
							{isSaving && (
								<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
							)}
							Save Changes
						</Button>
					)}
				</div>
			)}
		</form>
	);
}
