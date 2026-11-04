import { useIsMutating } from "@tanstack/react-query";
import { useState } from "react";
import { getErrorMessage } from "@/lib/api-error";
import {
	useOAuthConfigs,
	useUpdateOAuthLoginPreference,
} from "@/services/oauth-config";
import { useUsersFiltered } from "@/hooks/useUsers";
import { useOrganizations } from "@/hooks/useOrganizations";
import { MultiCombobox } from "@/components/forms/MultiCombobox";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import {
	Card,
	CardContent,
	CardHeader,
	CardTitle,
	CardDescription,
} from "@/components/ui/card";
import type { components } from "@/lib/v1";

type User = components["schemas"]["UserPublic"];

function PolicyToggle({
	id,
	kind,
	adminDefault = false,
	disabled = false,
}: {
	id: string;
	kind: "user" | "organization";
	adminDefault?: boolean;
	disabled?: boolean;
}) {
	const config = useOAuthConfigs();
	const update = useUpdateOAuthLoginPreference();
	const pending =
		useIsMutating({
			mutationKey: ["put", "/api/settings/oauth/login-preference"],
		}) > 0;
	const [error, setError] = useState<string | null>(null);
	const preference = config.data?.login_preference;
	const field =
		kind === "user" ? "excluded_user_ids" : "excluded_organization_ids";
	const ids = preference?.[field] ?? [];
	const excluded =
		ids.includes(id) ||
		(kind === "user" &&
			preference?.excluded_user_ids == null &&
			adminDefault);
	const label = "Exclude From SSO Enforcement";
	return (
		<div className="space-y-2 rounded-md border p-4">
			<div className="flex items-center justify-between gap-4">
				<Label htmlFor={`sso-${kind}-${id}`}>{label}</Label>
				<Switch
					id={`sso-${kind}-${id}`}
					checked={excluded}
					disabled={
						disabled ||
						!preference ||
						update.isPending ||
						pending ||
						config.isError
					}
					onCheckedChange={async (checked) => {
						if (!preference) return;
						setError(null);
						const exclude = checked;
						try {
							await update.mutateAsync({
								body: {
									...preference,
									[field]: exclude
										? [...new Set([...ids, id])]
										: ids.filter((value) => value !== id),
								},
							});
						} catch (failure) {
							setError(
								getErrorMessage(
									failure,
									"Could not update the login policy.",
								),
							);
						}
					}}
				/>
			</div>
			<p className="text-sm text-muted-foreground">
				Excluded accounts can use password and passkey sign-in. Changes
				apply immediately.
			</p>
			{update.isPending && (
				<p role="status" className="text-sm text-muted-foreground">
					Saving…
				</p>
			)}
			{(error || config.isError) && (
				<p role="alert" className="text-sm text-destructive">
					{error ?? "Could not load the login policy."}
				</p>
			)}
		</div>
	);
}

export function UserSsoExclusion({ user }: { user: User }) {
	return (
		<PolicyToggle
			id={user.id}
			kind="user"
			adminDefault={user.is_superuser}
		/>
	);
}

export function OrganizationSsoExclusion({
	organizationId,
	disabled,
}: {
	organizationId: string;
	disabled?: boolean;
}) {
	return (
		<PolicyToggle
			id={organizationId}
			kind="organization"
			disabled={disabled}
		/>
	);
}

export function SsoExclusions() {
	const users = useUsersFiltered(undefined, true);
	const organizations = useOrganizations({ includeInactive: true });
	const config = useOAuthConfigs();
	const update = useUpdateOAuthLoginPreference();
	const pending =
		useIsMutating({
			mutationKey: ["put", "/api/settings/oauth/login-preference"],
		}) > 0;
	const [userDraft, setUserDraft] = useState<string[] | null>(null);
	const [orgDraft, setOrgDraft] = useState<string[] | null>(null);
	const [error, setError] = useState<string | null>(null);
	const preference = config.data?.login_preference;
	const blocked =
		!preference ||
		config.isError ||
		users.isError ||
		organizations.isError ||
		pending;
	return (
		<Card>
			<CardHeader>
				<CardTitle role="heading" aria-level={3}>
					SSO Exclusions
				</CardTitle>
				<CardDescription>
					Excluded users and organizations can use password and
					passkey sign-in. Keep at least one active platform admin
					excluded for recovery.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				<div className="space-y-2">
					<Label htmlFor="sso-excluded-users">Excluded Users</Label>
					<MultiCombobox
						id="sso-excluded-users"
						value={userDraft ?? preference?.excluded_user_ids ?? []}
						onValueChange={setUserDraft}
						disabled={blocked}
						isLoading={users.isLoading}
						placeholder="Select users…"
						searchPlaceholder="Search users…"
						emptyText="No users found."
						options={(users.data ?? []).map((user) => ({
							value: user.id,
							label: user.name
								? `${user.name} (${user.email})`
								: user.email,
							description: [
								user.is_superuser ? "Platform Admin" : "User",
								user.is_active ? "Active" : "Inactive",
							].join(" · "),
						}))}
					/>
				</div>
				<div className="space-y-2">
					<Label htmlFor="sso-excluded-organizations">
						Excluded Organizations
					</Label>
					<MultiCombobox
						id="sso-excluded-organizations"
						value={
							orgDraft ??
							preference?.excluded_organization_ids ??
							[]
						}
						onValueChange={setOrgDraft}
						disabled={blocked}
						isLoading={organizations.isLoading}
						placeholder="Select organizations…"
						searchPlaceholder="Search organizations…"
						emptyText="No organizations found."
						options={(organizations.data ?? []).map((org) => ({
							value: org.id,
							label: org.name,
							description: org.domain ?? undefined,
						}))}
					/>
				</div>
				{(users.isError || organizations.isError) && (
					<p role="alert" className="text-sm text-destructive">
						Could not load exclusion choices. Refresh the page to
						try again.
					</p>
				)}
				{error && (
					<p role="alert" className="text-sm text-destructive">
						{error}
					</p>
				)}
				<Button
					type="button"
					disabled={
						blocked || (userDraft === null && orgDraft === null)
					}
					onClick={async () => {
						if (!preference) return;
						setError(null);
						try {
							await update.mutateAsync({
								body: {
									...preference,
									excluded_user_ids:
										userDraft ??
										preference.excluded_user_ids,
									excluded_organization_ids:
										orgDraft ??
										preference.excluded_organization_ids,
								},
							});
							setUserDraft(null);
							setOrgDraft(null);
						} catch (failure) {
							setError(
								getErrorMessage(
									failure,
									"Could not save exclusions.",
								),
							);
						}
					}}
				>
					{pending ? "Saving…" : "Save Exclusions"}
				</Button>
			</CardContent>
		</Card>
	);
}
