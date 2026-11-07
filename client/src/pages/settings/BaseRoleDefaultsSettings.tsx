import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Save } from "lucide-react";
import { toast } from "sonner";

import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { SettingsLoadError } from "@/components/shared/SettingsLoadError";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { BaseRoleSelect } from "@/components/users/BaseRoleSelect";
import { $api } from "@/lib/api-client";
import { GLOBAL_TARGET, orgTarget } from "@/lib/authorization";
import { getErrorMessage } from "@/lib/api-error";
import { useAuthorization } from "@/services/authorization";

export function BaseRoleDefaultsSettings() {
	const authorization = useAuthorization();
	const queryClient = useQueryClient();
	const [organizationId, setOrganizationId] = useState<string | null>(null);
	const [draft, setDraft] = useState<string | null | undefined>(undefined);
	const [loadedScope, setLoadedScope] = useState<string | null | undefined>(
		undefined,
	);
	const [saveError, setSaveError] = useState<string | null>(null);
	const scopeTarget = organizationId
		? orgTarget(organizationId)
		: GLOBAL_TARGET;
	const canRead =
		organizationId === null
			? authorization.meets({
					allOf: [
						{ permission: "settings.read", at: "global" },
						{ permission: "roleassignments.read", at: "global" },
					],
				})
			: authorization.canAt("organizations.read", scopeTarget) &&
				authorization.canAt("roleassignments.read", scopeTarget);
	const query = $api.useQuery(
		"get",
		"/api/base-role-defaults",
		{
			params: {
				query: organizationId
					? { organization_id: organizationId }
					: {},
			},
		},
		{ enabled: canRead },
	);
	const update = $api.useMutation("put", "/api/base-role-defaults");

	if (loadedScope !== organizationId && query.data) {
		setLoadedScope(organizationId);
		setDraft(query.data.default_base_role_id);
		setSaveError(null);
	}

	const value =
		draft === undefined
			? (query.data?.default_base_role_id ?? null)
			: draft;
	const canReadOrganization = (id: string) =>
		authorization.canAt("organizations.read", orgTarget(id));
	const canEdit =
		organizationId === null
			? authorization.meets({
					allOf: [
						{ permission: "settings.readwrite", at: "global" },
						{ permission: "userlifecycle.readwrite", at: "global" },
					],
				})
			: authorization.canAt("organizations.readwrite", scopeTarget) &&
				authorization.canAt("userlifecycle.readwrite", scopeTarget);
	const effectiveSource =
		organizationId === null
			? "Global"
			: query.data?.source === "organization"
				? "this organization"
				: "Global";
	const dirty = value !== (query.data?.default_base_role_id ?? null);

	const save = async () => {
		if (!canEdit || !dirty || update.isPending) return;
		setSaveError(null);
		try {
			const data = await update.mutateAsync({
				params: {
					query: organizationId
						? { organization_id: organizationId }
						: {},
				},
				body: { default_base_role_id: value },
			});
			queryClient.setQueryData(
				$api.queryOptions("get", "/api/base-role-defaults", {
					params: {
						query: organizationId
							? { organization_id: organizationId }
							: {},
					},
				}).queryKey,
				data,
			);
			setDraft(data.default_base_role_id);
			toast.success("Base role default saved");
		} catch (error) {
			setSaveError(
				getErrorMessage(error, "Could not save the base role default."),
			);
		}
	};

	return (
		<div className="min-w-0 w-full space-y-6">
			<div>
				<h2 className="font-display text-2xl font-semibold tracking-tight">
					Base Role Defaults
				</h2>
				<p className="mt-1 text-sm text-muted-foreground">
					Choose the base role new people and custom identities start
					with.
				</p>
			</div>
			<Card className="min-w-0">
				<CardHeader>
					<CardTitle className="text-base">
						Provisioning Default
					</CardTitle>
					<CardDescription>
						Organization settings can override the Global default.
						Resetting an organization inherits Global again.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-5">
					<div className="space-y-2">
						<Label htmlFor="base-role-default-organization">
							Scope
						</Label>
						<OrganizationSelect
							id="base-role-default-organization"
							value={organizationId}
							onChange={(next) => {
								if (next === undefined) return;
								setOrganizationId(next);
								setDraft(undefined);
								setLoadedScope(undefined);
							}}
							showGlobal
							filterOrganizations={(organization) =>
								canReadOrganization(organization.id)
							}
							placeholder="Select scope..."
							disabled={
								update.isPending ||
								(query.isFetching && !query.data)
							}
						/>
					</div>

					{!canRead ? (
						<p className="text-sm text-muted-foreground">
							You do not have permission to view the base role
							default for this scope.
						</p>
					) : query.isError && !query.data ? (
						<SettingsLoadError
							name="base role defaults"
							onRetry={() => void query.refetch()}
						/>
					) : query.isLoading || !query.data ? (
						<p
							role="status"
							className="text-sm text-muted-foreground"
						>
							Loading base role default…
						</p>
					) : (
						<>
							<div className="space-y-2">
								<Label htmlFor="base-role-default">
									Base Role
								</Label>
								<BaseRoleSelect
									id="base-role-default"
									value={value}
									onChange={setDraft}
									disabled={!canEdit || update.isPending}
									defaultLabel={
										organizationId === null
											? "Use User Base Role"
											: "Inherit Global Default"
									}
									defaultDescription={
										organizationId === null
											? "Reset to the built-in User role."
											: `Currently effective: ${query.data.effective_base_role_name}`
									}
								/>
							</div>
							<p className="rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/30 p-3 text-sm">
								New accounts currently receive{" "}
								<strong>
									{query.data.effective_base_role_name}
								</strong>{" "}
								from <strong>{effectiveSource}</strong>.
							</p>
							{!canEdit && (
								<p className="text-sm text-muted-foreground">
									You can view this default but do not have
									permission to change it.
								</p>
							)}
							{saveError && (
								<p
									role="alert"
									className="text-sm text-destructive"
								>
									{saveError}
								</p>
							)}
							<div className="flex justify-end">
								<Button
									type="button"
									className="min-h-11"
									disabled={
										!canEdit || !dirty || update.isPending
									}
									onClick={() => void save()}
								>
									{update.isPending && (
										<Loader2 className="mr-2 size-4 animate-spin motion-reduce:animate-none" />
									)}
									{update.isPending ? (
										"Saving..."
									) : (
										<>
											<Save className="mr-2 size-4" />
											Save Default
										</>
									)}
								</Button>
							</div>
						</>
					)}
				</CardContent>
			</Card>
		</div>
	);
}
