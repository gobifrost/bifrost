import { useIsMutating } from "@tanstack/react-query";
import { useState } from "react";
import { getErrorMessage } from "@/lib/api-error";
import { Badge } from "@/components/ui/badge";
import {
	useOAuthConfigs,
	useUpdateOAuthLoginPreference,
} from "@/services/oauth-config";
import { useUsersPage } from "@/hooks/useUsers";
import { useOrganizations } from "@/hooks/useOrganizations";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
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

export function UserSsoExclusion({ user }: { user: User }) {
	const config = useOAuthConfigs();
	const update = useUpdateOAuthLoginPreference();
	const policyPending =
		useIsMutating({
			mutationKey: ["put", "/api/settings/oauth/login-preference"],
		}) > 0;
	const [error, setError] = useState<string | null>(null);
	const preference = config.data?.login_preference;
	const excluded =
		preference?.excluded_user_ids?.includes(user.id) ?? user.is_superuser;
	return (
		<div className="space-y-2 rounded-md border p-4">
			<div className="flex items-center justify-between gap-4">
				<Label htmlFor={`sso-exclusion-${user.id}`}>
					Exclude From SSO Enforcement
				</Label>
				<Switch
					id={`sso-exclusion-${user.id}`}
					checked={excluded}
					disabled={
						!preference ||
						update.isPending ||
						policyPending ||
						config.isError
					}
					onCheckedChange={async (checked) => {
						if (!preference) return;
						setError(null);
						const ids = preference.excluded_user_ids ?? [];
						try {
							await update.mutateAsync({
								body: {
									...preference,
									excluded_user_ids: checked
										? [...new Set([...ids, user.id])]
										: ids.filter((id) => id !== user.id),
								},
							});
						} catch (failure) {
							setError(
								getErrorMessage(
									failure,
									"Could not update the exclusion.",
								),
							);
						}
					}}
				/>
			</div>
			{update.isPending && (
				<p role="status" className="text-sm text-muted-foreground">
					Saving exclusion…
				</p>
			)}
			<p className="text-sm text-muted-foreground">
				Excluded accounts can use password and passkey sign-in. Changes
				apply immediately.
			</p>
			{(error || config.isError) && (
				<p role="alert" className="text-sm text-destructive">
					{error ?? "Could not load the login policy."}
				</p>
			)}
		</div>
	);
}

export function SsoExclusions() {
	const [search, setSearch] = useState("");
	const [offset, setOffset] = useState(0);
	const users = useUsersPage({
		search,
		offset,
		limit: 10,
		sortBy: "email",
		sortDirection: "asc",
	});
	const organizations = useOrganizations();
	const config = useOAuthConfigs();
	const update = useUpdateOAuthLoginPreference();
	const policyPending =
		useIsMutating({
			mutationKey: ["put", "/api/settings/oauth/login-preference"],
		}) > 0;
	const [error, setError] = useState<string | null>(null);
	const preference = config.data?.login_preference;
	return (
		<Card>
			<CardHeader>
				<CardTitle role="heading" aria-level={3}>
					SSO Exclusions
				</CardTitle>
				<CardDescription>
					Keep at least one active platform admin excluded for
					recovery. User exclusions also appear on the user screen.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				<Label htmlFor="sso-user-search">Find a User</Label>
				<Input
					id="sso-user-search"
					value={search}
					onChange={(event) => {
						setSearch(event.target.value);
						setOffset(0);
					}}
				/>
				{users.isError && <p role="alert">Could not load users.</p>}
				{users.isLoading && <p>Loading users…</p>}
				{users.data?.items.map((user) => (
					<div key={user.id} className="space-y-2">
						<p className="flex items-center gap-2 font-medium">
							{user.name || user.email}
							{user.is_superuser && (
								<Badge variant="secondary">
									Platform Admin
								</Badge>
							)}
							{!user.is_active && (
								<Badge variant="outline">Inactive</Badge>
							)}
						</p>
						<UserSsoExclusion user={user} />
					</div>
				))}
				{users.data?.total === 0 && <p>No users match this search.</p>}
				<div className="flex gap-2">
					<Button
						type="button"
						variant="outline"
						disabled={offset === 0}
						onClick={() => setOffset(Math.max(0, offset - 10))}
					>
						Previous
					</Button>
					<Button
						type="button"
						variant="outline"
						disabled={
							!users.data || offset + 10 >= users.data.total
						}
						onClick={() => setOffset(offset + 10)}
					>
						Next
					</Button>
				</div>
				<h3 className="font-medium">Organization Enforcement</h3>
				<p className="text-sm text-muted-foreground">
					Turn enforcement off to exclude all accounts in an
					organization. Individual exclusions still apply when
					enforcement is on.
				</p>
				{organizations.isError && (
					<p role="alert">Could not load organizations.</p>
				)}
				{organizations.data?.map((org) => (
					<div
						key={org.id}
						className="flex items-center justify-between gap-4"
					>
						<Label htmlFor={`sso-org-${org.id}`}>{org.name}</Label>
						<Switch
							id={`sso-org-${org.id}`}
							checked={
								!(
									preference?.excluded_organization_ids ?? []
								).includes(org.id)
							}
							disabled={
								!preference || update.isPending || policyPending
							}
							onCheckedChange={async (checked) => {
								if (!preference) return;
								setError(null);
								const ids =
									preference.excluded_organization_ids ?? [];
								try {
									await update.mutateAsync({
										body: {
											...preference,
											excluded_organization_ids: checked
												? ids.filter(
														(id) => id !== org.id,
													)
												: [
														...new Set([
															...ids,
															org.id,
														]),
													],
										},
									});
								} catch {
									setError(
										"Could not update organization enforcement.",
									);
								}
							}}
						/>
					</div>
				))}
				{error && (
					<p role="alert" className="text-sm text-destructive">
						{error}
					</p>
				)}
			</CardContent>
		</Card>
	);
}
