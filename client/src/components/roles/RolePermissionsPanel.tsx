import { useState } from "react";
import { AlertCircle, Loader2, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Skeleton } from "@/components/ui/skeleton";
import { useRolePermissions, useUpdateRolePermissions } from "@/hooks/useRoles";
import { getErrorMessage } from "@/lib/api-error";
import { PLATFORM_ADMIN_ROLE_ID } from "@/lib/builtin-roles";
import { useAuthorization } from "@/services/authorization";
import type { components } from "@/lib/v1";

type PermissionItem = components["schemas"]["RolePermissionItem"];

interface AreaCopy {
	title: string;
	description: string;
	read?: string;
	readwrite: string;
}

/** Identity areas in the order an editor reads them. */
const AREAS: [string, AreaCopy][] = [
	[
		"users",
		{
			title: "Users",
			description:
				"See people and support them: invite, change names, reset MFA, sign out, enable or disable.",
			read: "View",
			readwrite: "View & support",
		},
	],
	[
		"users.lifecycle",
		{
			title: "User lifecycle",
			description:
				"Create Global users, move people between organizations, change base roles, and delete users.",
			readwrite: "Allowed",
		},
	],
	[
		"organizations",
		{
			title: "Organizations",
			description:
				"See organizations; managing also creates, edits, disables, and deletes them.",
			read: "View",
			readwrite: "View & manage",
		},
	],
	[
		"roleassignments",
		{
			title: "Role assignments",
			description:
				"See who holds which roles; assigning grants and removes roles that carry no permissions.",
			read: "View",
			readwrite: "View & assign",
		},
	],
	[
		"roles",
		{
			title: "Role definitions",
			description:
				"See roles and what they allow; managing creates, edits, and deletes roles.",
			read: "View",
			readwrite: "View & manage",
		},
	],
];

type Level = "none" | "read" | "readwrite";

function levelOf(domain: string, held: Set<string>): Level {
	if (held.has(`${domain}.readwrite`)) return "readwrite";
	if (held.has(`${domain}.read`)) return "read";
	return "none";
}

function permissionsFor(domain: string, level: Level, vocabulary: Set<string>) {
	const read = `${domain}.read`;
	const readwrite = `${domain}.readwrite`;
	if (level === "none") return [];
	if (level === "read") return [read];
	// Changing includes viewing wherever the area has a view permission.
	return vocabulary.has(read) ? [read, readwrite] : [readwrite];
}

function PrivilegedMark() {
	return (
		<Badge variant="warning" className="gap-1">
			<ShieldAlert aria-hidden="true" className="size-3" />
			Privileged
		</Badge>
	);
}

/**
 * A role's identity permissions, as one choice per area, plus the other
 * permissions it holds (read-only here). Saving replaces only the identity
 * permissions; the server keeps the rest.
 */
export function RolePermissionsPanel({
	roleId,
	isBuiltin,
}: {
	roleId: string;
	isBuiltin: boolean;
}) {
	const authorization = useAuthorization();
	const query = useRolePermissions(roleId);
	const update = useUpdateRolePermissions();
	const data = query.data;
	const [draft, setDraft] = useState<Set<string> | null>(null);
	const [saveError, setSaveError] = useState<string | null>(null);

	const canEdit =
		!isBuiltin &&
		authorization.meets({ permission: "roles.readwrite", at: "global" });
	const isPlatformAdminRole = isBuiltin && roleId === PLATFORM_ADMIN_ROLE_ID;

	if (query.isError && !data) {
		return (
			<div className="space-y-3">
				<Alert variant="destructive">
					<AlertCircle className="h-4 w-4" />
					<AlertDescription>
						{getErrorMessage(
							query.error,
							"Permissions could not be loaded.",
						)}
					</AlertDescription>
				</Alert>
				<Button
					variant="outline"
					className="min-h-11"
					disabled={query.isFetching}
					onClick={() => void query.refetch()}
				>
					Retry permissions
				</Button>
			</div>
		);
	}
	if (!data) {
		return (
			<div
				role="status"
				aria-label="Loading permissions"
				className="space-y-3"
			>
				<Skeleton className="h-20 w-full" />
				<Skeleton className="h-20 w-full" />
			</div>
		);
	}

	const vocabulary = new Set(
		data.identity_permissions.map((p) => p.permission),
	);
	const privileged = new Set(
		[...data.permissions, ...data.identity_permissions]
			.filter((p) => p.privileged)
			.map((p) => p.permission),
	);
	const savedIdentity = new Set(
		data.permissions
			.map((p) => p.permission)
			.filter((permission) => vocabulary.has(permission)),
	);
	const selected = draft ?? savedIdentity;
	const others: PermissionItem[] = data.permissions.filter(
		(p) => !vocabulary.has(p.permission),
	);
	const areas = AREAS.filter(
		([domain]) =>
			vocabulary.has(`${domain}.read`) ||
			vocabulary.has(`${domain}.readwrite`),
	);
	const dirty =
		draft !== null &&
		(draft.size !== savedIdentity.size ||
			[...draft].some((permission) => !savedIdentity.has(permission)));

	const setLevel = (domain: string, level: Level) => {
		const next = new Set(
			[...selected].filter(
				(permission) =>
					permission !== `${domain}.read` &&
					permission !== `${domain}.readwrite`,
			),
		);
		for (const permission of permissionsFor(domain, level, vocabulary)) {
			next.add(permission);
		}
		setDraft(next);
	};

	const handleSave = async () => {
		if (!dirty || update.isPending) return;
		setSaveError(null);
		try {
			await update.mutateAsync({
				params: { path: { role_id: roleId } },
				body: { permissions: [...selected].sort() },
			});
			setDraft(null);
			toast.success("Permissions saved");
		} catch (error) {
			setSaveError(
				getErrorMessage(error, "Permissions could not be saved"),
			);
		}
	};

	return (
		<div className="space-y-6">
			{!isBuiltin && !canEdit && (
				<p className="text-sm text-muted-foreground">
					You can view this role's permissions but not change them.
				</p>
			)}
			<p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
				<PrivilegedMark />
				Anyone holding a privileged permission becomes a protected
				account: only a Platform Admin can change their profile,
				sign-in, or roles.
			</p>

			{isPlatformAdminRole ? (
				<p className="rounded-[var(--bf-radius-surface)] border border-border/70 p-4 text-sm">
					Platform Admin holds every permission except reading
					secrets, which always takes an explicit role.
				</p>
			) : (
				<section
					aria-labelledby="identity-permissions-heading"
					className="space-y-3"
				>
					<h2
						id="identity-permissions-heading"
						className="text-base font-semibold"
					>
						People and access
					</h2>
					<ul className="space-y-3">
						{areas.map(([domain, copy]) => {
							const level = levelOf(domain, selected);
							const choices: { level: Level; label: string }[] = [
								{ level: "none", label: "No access" },
								...(copy.read &&
								vocabulary.has(`${domain}.read`)
									? [
											{
												level: "read" as const,
												label: copy.read,
											},
										]
									: []),
								{ level: "readwrite", label: copy.readwrite },
							];
							const current = choices.find(
								(c) => c.level === level,
							);
							return (
								<li
									key={domain}
									className="rounded-[var(--bf-radius-surface)] border border-border/70 p-4"
								>
									<fieldset className="space-y-3">
										<legend className="text-sm font-medium">
											{copy.title}
										</legend>
										<p className="text-xs leading-5 text-muted-foreground">
											{copy.description}
										</p>
										{canEdit ? (
											<RadioGroup
												value={level}
												onValueChange={(value) =>
													setLevel(
														domain,
														value as Level,
													)
												}
												className="flex flex-wrap gap-x-6 gap-y-2"
												aria-label={copy.title}
											>
												{choices.map((choice) => {
													const id = `${domain}-${choice.level}`;
													const isPrivileged =
														permissionsFor(
															domain,
															choice.level,
															vocabulary,
														).some((p) =>
															privileged.has(p),
														);
													return (
														<div
															key={choice.level}
															className="flex min-h-11 items-center gap-2"
														>
															<RadioGroupItem
																id={id}
																value={
																	choice.level
																}
															/>
															<Label htmlFor={id}>
																{choice.label}
															</Label>
															{isPrivileged && (
																<PrivilegedMark />
															)}
														</div>
													);
												})}
											</RadioGroup>
										) : (
											<p className="flex flex-wrap items-center gap-2 text-sm">
												{current?.label ?? "No access"}
												{permissionsFor(
													domain,
													level,
													vocabulary,
												).some((p) =>
													privileged.has(p),
												) && <PrivilegedMark />}
											</p>
										)}
									</fieldset>
								</li>
							);
						})}
					</ul>
				</section>
			)}

			{others.length > 0 && (
				<section
					aria-labelledby="other-permissions-heading"
					className="space-y-2"
				>
					<h2
						id="other-permissions-heading"
						className="text-base font-semibold"
					>
						Other permissions
					</h2>
					<p className="text-xs text-muted-foreground">
						{isBuiltin
							? "Also part of this built-in role."
							: "Managed elsewhere — not editable here yet."}
					</p>
					<ul className="flex flex-wrap gap-2">
						{others.map((item) => (
							<li key={item.permission}>
								<Badge
									variant="secondary"
									className="h-auto gap-1.5 py-1 font-mono"
								>
									{item.permission}
									{item.privileged && (
										<ShieldAlert
											aria-label="Privileged"
											className="size-3"
										/>
									)}
								</Badge>
							</li>
						))}
					</ul>
				</section>
			)}

			{canEdit && (
				<div className="space-y-3">
					{saveError && (
						<Alert variant="destructive">
							<AlertCircle className="h-4 w-4" />
							<AlertDescription>{saveError}</AlertDescription>
						</Alert>
					)}
					<div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
						<Button
							variant="outline"
							className="min-h-11"
							disabled={!dirty || update.isPending}
							onClick={() => {
								setDraft(null);
								setSaveError(null);
							}}
						>
							Discard changes
						</Button>
						<Button
							className="min-h-11"
							disabled={!dirty || update.isPending}
							onClick={() => void handleSave()}
						>
							{update.isPending && (
								<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
							)}
							Save permissions
						</Button>
					</div>
				</div>
			)}
		</div>
	);
}
