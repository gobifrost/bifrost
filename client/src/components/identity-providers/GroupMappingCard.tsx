import { useState } from "react";
import { ArrowRight, CheckCircle2, CircleDashed, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { MultiCombobox } from "@/components/ui/multi-combobox";
import { Skeleton } from "@/components/ui/skeleton";
import { useRoles } from "@/hooks/useRoles";
import { getErrorMessage } from "@/lib/api-error";
import {
	useIdentityProviderGroups,
	useMapIdentityProviderGroup,
	useUnmapIdentityProviderGroup,
	type IdentityProvider,
	type IdentityProviderGroup,
} from "@/services/identity-providers";

function MappingDialog({
	provider,
	group,
	onOpenChange,
}: {
	provider: IdentityProvider;
	group: IdentityProviderGroup;
	onOpenChange: (open: boolean) => void;
}) {
	const roles = useRoles();
	const map = useMapIdentityProviderGroup();
	const unmap = useUnmapIdentityProviderGroup();
	const [roleIds, setRoleIds] = useState(group.roles.map((role) => role.id));
	const busy = map.isPending || unmap.isPending;
	const path = { identity_provider_id: provider.id, group_id: group.id };
	const save = async () => {
		try {
			await map.mutateAsync({
				params: { path },
				body: { role_ids: roleIds },
			});
			onOpenChange(false);
		} catch (cause) {
			toast.error(
				getErrorMessage(cause, "The mapping could not be saved."),
			);
		}
	};
	const remove = async () => {
		try {
			await unmap.mutateAsync({ params: { path } });
			onOpenChange(false);
		} catch (cause) {
			toast.error(
				getErrorMessage(cause, "The mapping could not be removed."),
			);
		}
	};
	return (
		<Dialog open onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-md">
				<DialogHeader>
					<DialogTitle>Map {group.display_name}</DialogTitle>
					<DialogDescription>
						Members get these roles in {provider.organization_name}.
						A mapping with no roles grants access only. Once any
						group is mapped, users need a mapped group to stay
						active.
					</DialogDescription>
				</DialogHeader>
				<div className="space-y-2">
					<Label htmlFor="mapping-roles">User Roles</Label>
					<MultiCombobox
						options={(roles.data ?? []).map((role) => ({
							value: role.id,
							label: role.name,
						}))}
						value={roleIds}
						onValueChange={setRoleIds}
						isLoading={roles.isLoading}
						placeholder="Access only"
						searchPlaceholder="Search roles..."
						emptyText="No roles found."
					/>
				</div>
				<DialogFooter className="gap-2 sm:justify-between">
					{group.mapped ? (
						<Button
							variant="outline"
							disabled={busy}
							onClick={() => void remove()}
						>
							Remove Mapping
						</Button>
					) : (
						<span />
					)}
					<div className="flex gap-2">
						<Button
							variant="outline"
							disabled={busy}
							onClick={() => onOpenChange(false)}
						>
							Cancel
						</Button>
						<Button disabled={busy} onClick={() => void save()}>
							{map.isPending && (
								<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
							)}
							Save Mapping
						</Button>
					</div>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}

/** The groups the provider has pushed, and the roles each one grants. */
export function GroupMappingCard({
	provider,
	canWrite,
}: {
	provider: IdentityProvider;
	canWrite: boolean;
}) {
	const query = useIdentityProviderGroups(provider.id);
	const [editing, setEditing] = useState<IdentityProviderGroup | null>(null);
	const groups = query.data ?? [];
	return (
		<Card>
			<CardHeader>
				<CardTitle>Group Mapping</CardTitle>
				<CardDescription>
					{groups.length} {groups.length === 1 ? "group" : "groups"}{" "}
					pushed by the identity provider. Assign groups to the app in
					the identity provider to see them here.
				</CardDescription>
			</CardHeader>
			<CardContent>
				{query.isLoading ? (
					<Skeleton className="h-24 w-full" />
				) : groups.length === 0 ? (
					<p className="text-sm text-muted-foreground">
						No groups yet. They appear after the identity provider's
						next provisioning cycle.
					</p>
				) : (
					<DataTable>
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead>Identity Group</DataTableHead>
								<DataTableHead
									className="w-0"
									aria-hidden="true"
								/>
								<DataTableHead className="w-0 whitespace-nowrap">
									Mapping Status
								</DataTableHead>
								<DataTableHead>User Roles</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right">
									Members
								</DataTableHead>
								{canWrite && <DataTableHead className="w-0" />}
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{groups.map((group) => (
								<DataTableRow key={group.id}>
									<DataTableCell className="[overflow-wrap:anywhere]">
										<span className="font-medium">
											{group.display_name}
										</span>
									</DataTableCell>
									<DataTableCell className="w-0">
										<ArrowRight
											aria-hidden="true"
											className="h-4 w-4 text-muted-foreground"
										/>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										{group.mapped ? (
											<Badge variant="success">
												<CheckCircle2
													aria-hidden="true"
													className="h-3.5 w-3.5"
												/>
												Mapped
											</Badge>
										) : (
											<Badge variant="secondary">
												<CircleDashed
													aria-hidden="true"
													className="h-3.5 w-3.5"
												/>
												Not Mapped
											</Badge>
										)}
									</DataTableCell>
									<DataTableCell>
										{group.mapped &&
										group.roles.length === 0 ? (
											<span className="text-sm text-muted-foreground">
												Access Only
											</span>
										) : (
											<ul className="flex flex-wrap gap-1.5">
												{group.roles.map((role) => (
													<li key={role.id}>
														<Badge variant="power">
															{role.name}
														</Badge>
													</li>
												))}
											</ul>
										)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right">
										<span className="font-mono tabular-nums">
											{group.member_count}
										</span>
									</DataTableCell>
									{canWrite && (
										<DataTableCell className="w-0">
											<Button
												size="sm"
												variant="outline"
												aria-label={`Edit mapping for ${group.display_name}`}
												onClick={() =>
													setEditing(group)
												}
											>
												{group.mapped ? "Edit" : "Map"}
											</Button>
										</DataTableCell>
									)}
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				)}
			</CardContent>
			{editing && (
				<MappingDialog
					provider={provider}
					group={editing}
					onOpenChange={(open) => !open && setEditing(null)}
				/>
			)}
		</Card>
	);
}
