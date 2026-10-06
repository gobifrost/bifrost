import { useState, type ComponentProps, type RefObject } from "react";
import { toast } from "sonner";

import { RegistrationLinkDialog } from "@/components/users/RegistrationLinkDialog";
import { UserAccountActionDialog } from "@/components/users/UserAccountActionDialog";
import type { UserActionsMenu } from "@/components/users/UserActionsMenu";
import { useAuth } from "@/contexts/AuthContext";
import {
	useDeleteUser,
	useResetUserMfa,
	useSignOutUserEverywhere,
	useUpdateUser,
} from "@/hooks/useUsers";
import {
	useRegenerateInvite,
	useResendInvite,
	useRevokeInvite,
	useSendInvite,
} from "@/hooks/useUserInvites";
import { orgTarget } from "@/lib/authorization";
import type { components } from "@/lib/v1";
import { useAuthorization } from "@/services/authorization";
import { useEventSources } from "@/services/events";

type User = components["schemas"]["UserPublic"];
type UserMfaReset = components["schemas"]["UserMfaResetResponse"];
type MenuProps = ComponentProps<typeof UserActionsMenu>;
type ConfirmMode = "disable" | "delete" | "reset-mfa" | "sign-out";

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

function describeMfaReset(result: UserMfaReset): string {
	const removed = [
		result.totp_removed ? "the authenticator app" : null,
		result.recovery_codes_removed > 0
			? count(result.recovery_codes_removed, "recovery code")
			: null,
		result.passkeys_removed > 0
			? count(result.passkeys_removed, "passkey")
			: null,
		result.trusted_devices_revoked > 0
			? count(result.trusted_devices_revoked, "remembered device")
			: null,
	].filter((part) => part !== null);
	return [
		removed.length > 0
			? `Removed ${removed.join(", ")}.`
			: "They had no MFA set up.",
		`Ended ${count(result.sessions_revoked, "session")}.`,
		"They'll set up MFA at their next sign-in.",
	].join(" ");
}

const errorText = (error: unknown, fallback: string) =>
	error instanceof Error ? error.message : fallback;

/**
 * The account actions on a user — invites, registration links, MFA reset,
 * signing out, enabling and disabling, deleting — with their permission
 * rules and confirmation dialogs, for every place that offers them (the
 * Users list rows and a person's page). Spread `menuPropsFor(user)` onto a
 * `UserActionsMenu` and render `dialogs` once.
 */
export function useUserAccountActions({
	returnFocusRef,
	onDeleted,
}: {
	/** Where focus goes when a confirmation closes; by default, where it was. */
	returnFocusRef?: RefObject<HTMLElement | null>;
	onDeleted?: (user: User) => void;
}) {
	const { user: currentUser } = useAuth();
	const authorization = useAuthorization();
	const [confirm, setConfirm] = useState<{
		mode: ConfirmMode;
		user: User;
	} | null>(null);
	const [registrationLink, setRegistrationLink] = useState<{
		userId: string;
		email: string;
		url: string;
	} | null>(null);

	const updateMutation = useUpdateUser();
	const deleteMutation = useDeleteUser();
	const resetMfaMutation = useResetUserMfa();
	const signOutMutation = useSignOutUserEverywhere();
	const resendMutation = useResendInvite();
	const regenerateMutation = useRegenerateInvite();
	const revokeMutation = useRevokeInvite();
	const sendInviteMutation = useSendInvite();
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

	const enable = async (user: User) => {
		try {
			await updateMutation.mutateAsync({
				params: { path: { user_id: user.id } },
				body: { is_active: true },
			});
			toast.success("User enabled", {
				description: `${user.name || user.email} has been re-enabled`,
			});
		} catch (error) {
			toast.error("Failed to enable user", {
				description: errorText(error, "Unknown error occurred"),
			});
		}
	};

	const showRegistrationLink = (user: User, failure: string) =>
		regenerateMutation.mutate(user.id, {
			onSuccess: (res) =>
				setRegistrationLink({
					userId: user.id,
					email: user.email,
					url: res.registration_url,
				}),
			onError: (error: unknown) => toast.error(errorText(error, failure)),
		});

	const handleConfirm = async () => {
		if (!confirm) return;
		const { mode, user } = confirm;
		const name = user.name || user.email;
		switch (mode) {
			case "disable":
				await updateMutation.mutateAsync({
					params: { path: { user_id: user.id } },
					body: { is_active: false },
				});
				toast.success("User disabled", {
					description: `${name} has been disabled`,
				});
				break;
			case "delete":
				await deleteMutation.mutateAsync({
					params: { path: { user_id: user.id } },
				});
				toast.success("User permanently deleted", {
					description: `${name} has been permanently removed`,
				});
				onDeleted?.(user);
				break;
			case "reset-mfa": {
				const result = await resetMfaMutation.mutateAsync({
					params: { path: { user_id: user.id } },
				});
				toast.success(`MFA reset for ${name}`, {
					description: describeMfaReset(result),
				});
				break;
			}
			case "sign-out": {
				const result = await signOutMutation.mutateAsync({
					body: { user_id: user.id },
				});
				toast.success(`${name} signed out`, {
					description: `Ended ${count(result.sessions_revoked, "session")}.`,
				});
				break;
			}
		}
		setConfirm(null);
	};

	// Actions follow the user's organization; a protected user can be changed
	// only by a Platform Admin.
	const menuPropsFor = (
		user: User,
	): Omit<MenuProps, "label" | "onEditProfile"> => {
		const target = orgTarget(user.organization_id);
		return {
			status: user.invite_status ?? "active",
			isActive: user.is_active,
			isSelf: !!currentUser && user.id === currentUser.id,
			canSupport: authorization.canAt("users.readwrite", target),
			canDelete: authorization.canAt("users.lifecycle.readwrite", target),
			isProtected: user.is_protected && !authorization.isPlatformAdmin,
			onResend: () =>
				resendMutation.mutate(user.id, {
					onSuccess: (res) =>
						toast.success(
							res.event_emitted
								? `Invite automation triggered for ${user.email}`
								: "Invite regenerated (no automations — copy link from regenerate)",
						),
					onError: (error: unknown) =>
						toast.error(
							errorText(error, "Failed to resend invite"),
						),
				}),
			onRegenerate: () =>
				showRegistrationLink(user, "Failed to regenerate link"),
			onCopyLink: () => showRegistrationLink(user, "Failed to copy link"),
			onRevoke: () =>
				revokeMutation.mutate(user.id, {
					onSuccess: () => toast.success("Invite revoked"),
					onError: (error: unknown) =>
						toast.error(
							errorText(error, "Failed to revoke invite"),
						),
				}),
			onResetMfa: () => setConfirm({ mode: "reset-mfa", user }),
			onSignOut: () => setConfirm({ mode: "sign-out", user }),
			onToggleActive: () =>
				user.is_active
					? setConfirm({ mode: "disable", user })
					: void enable(user),
			onDelete: () => setConfirm({ mode: "delete", user }),
		};
	};

	const dialogs = (
		<>
			<RegistrationLinkDialog
				title="Registration Link Ready"
				key={registrationLink?.url ?? "closed"}
				open={registrationLink !== null}
				email={registrationLink?.email}
				url={registrationLink?.url}
				canSendEmail={inviteAutomationConfigured}
				isSendingEmail={sendInviteMutation.isPending}
				onSendEmail={async () => {
					if (!registrationLink) return;
					await sendInviteMutation.mutateAsync({
						userId: registrationLink.userId,
						registrationUrl: registrationLink.url,
					});
					toast.success("Registration email sent");
					setRegistrationLink(null);
				}}
				onOpenChange={(open) => {
					if (!open) setRegistrationLink(null);
				}}
			/>
			{confirm && (
				<UserAccountActionDialog
					mode={confirm.mode}
					returnFocusRef={returnFocusRef}
					name={confirm.user.name || confirm.user.email}
					onOpenChange={(open) => {
						if (!open) setConfirm(null);
					}}
					onConfirm={handleConfirm}
				/>
			)}
		</>
	);

	return { menuPropsFor, dialogs };
}
