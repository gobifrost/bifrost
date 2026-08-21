import { useMemo, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
	Building2,
	MoreVertical,
	Pencil,
	Plus,
	RefreshCw,
	Trash2,
	Users,
} from "lucide-react";
import { toast } from "sonner";

import { SearchBox } from "@/components/search/SearchBox";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MultiCombobox } from "@/components/forms/MultiCombobox";
import { Skeleton } from "@/components/ui/skeleton";
import { useSearch } from "@/hooks/useSearch";
import { $api } from "@/lib/api-client";
import type { components } from "@/lib/v1";

type Organization = components["schemas"]["OrganizationPublic"];
type OrganizationGroup = components["schemas"]["OrganizationGroupPublic"];

interface GroupFormState {
	name: string;
	memberOrganizationIds: string[];
}

const EMPTY_FORM: GroupFormState = {
	name: "",
	memberOrganizationIds: [],
};

function groupOwnerName(
	group: OrganizationGroup,
	organizations: Organization[],
): string {
	return organizations.find((org) => org.id === group.owner_organization_id)?.name ?? group.owner_organization_id;
}

function groupMemberSummary(
	group: OrganizationGroup,
	organizations: Organization[],
): string {
	if (!group.member_organization_ids || group.member_organization_ids.length === 0) {
		return "No member organizations";
	}
	const names = group.member_organization_ids
		.map((id) => organizations.find((org) => org.id === id)?.name ?? id)
		.slice(0, 3);
	if (group.member_organization_ids.length > 3) {
		names.push(`+${group.member_organization_ids.length - 3} more`);
	}
	return names.join(" · ");
}

export function OrganizationGroupsManager({
	organizations,
}: {
	organizations: Organization[];
}) {
	const queryClient = useQueryClient();
	const [searchTerm, setSearchTerm] = useState("");
	const [isCreateOpen, setIsCreateOpen] = useState(false);
	const [isEditOpen, setIsEditOpen] = useState(false);
	const [isDeleteOpen, setIsDeleteOpen] = useState(false);
	const [selectedGroup, setSelectedGroup] = useState<OrganizationGroup | null>(null);
	const [form, setForm] = useState<GroupFormState>(EMPTY_FORM);
	const [validationError, setValidationError] = useState<string | null>(null);

	const groupsQuery = $api.useQuery("get", "/api/organization-groups");
	const createMutation = $api.useMutation("post", "/api/organization-groups");
	const updateMutation = $api.useMutation(
		"patch",
		"/api/organization-groups/{group_id}",
	);
	const deleteMutation = $api.useMutation(
		"delete",
		"/api/organization-groups/{group_id}",
	);

	const memberOrganizations = useMemo(
		() => organizations.filter((org) => !org.is_provider),
		[organizations],
	);
	const groups: OrganizationGroup[] = Array.isArray(groupsQuery.data)
		? groupsQuery.data
		: [];
	const filteredGroups = useSearch(groups, searchTerm, [
		"name",
		"id",
		(group) => groupOwnerName(group, organizations),
		(group) => groupMemberSummary(group, organizations),
	]);

	const resetForm = () => {
		setForm(EMPTY_FORM);
		setSelectedGroup(null);
		setValidationError(null);
	};

	const refresh = () => {
		void groupsQuery.refetch();
	};

	const openCreate = () => {
		setSelectedGroup(null);
		setForm(EMPTY_FORM);
		setValidationError(null);
		setIsCreateOpen(true);
	};

	const openEdit = (group: OrganizationGroup) => {
		setSelectedGroup(group);
		setForm({
			name: group.name,
			memberOrganizationIds: group.member_organization_ids ?? [],
		});
		setValidationError(null);
		setIsEditOpen(true);
	};

	const closeEditor = () => {
		setIsCreateOpen(false);
		setIsEditOpen(false);
		resetForm();
	};

	const submitEditor = async (event: FormEvent) => {
		event.preventDefault();
		const name = form.name.trim();
		if (!name) {
			setValidationError("Enter a group name.");
			return;
		}
		const memberIds = Array.from(new Set(form.memberOrganizationIds));

		try {
			if (selectedGroup) {
				await updateMutation.mutateAsync({
					params: { path: { group_id: selectedGroup.id } },
					body: {
						name,
						member_organization_ids: memberIds,
					},
				});
			} else {
				await createMutation.mutateAsync({
					body: {
						name,
						member_organization_ids: memberIds,
					},
				});
			}
			await queryClient.invalidateQueries({
				queryKey: ["get", "/api/organization-groups"],
			});
			toast.success(selectedGroup ? "Organization group updated" : "Organization group created");
			closeEditor();
		} catch (error) {
			toast.error(
				selectedGroup ? "Failed to update organization group" : "Failed to create organization group",
				{
					description: error instanceof Error ? error.message : "Unknown error occurred",
				},
			);
		}
	};

	const confirmDelete = async () => {
		if (!selectedGroup) return;
		try {
			await deleteMutation.mutateAsync({
				params: { path: { group_id: selectedGroup.id } },
			});
			await queryClient.invalidateQueries({
				queryKey: ["get", "/api/organization-groups"],
			});
			toast.success("Organization group deleted");
			setIsDeleteOpen(false);
			resetForm();
		} catch (error) {
			toast.error("Failed to delete organization group", {
				description: error instanceof Error ? error.message : "Unknown error occurred",
			});
		}
	};

	const handleDelete = (group: OrganizationGroup) => {
		setSelectedGroup(group);
		setIsDeleteOpen(true);
	};

	const loading = groupsQuery.isLoading;
	const savePending = createMutation.isPending || updateMutation.isPending;

	return (
		<div className="space-y-4">
			<div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
				<div>
					<h2 className="text-2xl font-bold tracking-tight">Organization groups</h2>
					<p className="mt-1 text-sm text-muted-foreground">
						Provider-owned groups control bundled member organizations for access boundaries.
					</p>
				</div>
				<div className="flex items-center gap-2 sm:shrink-0">
					<Button
						variant="outline"
						size="icon"
						onClick={refresh}
						aria-label="Refresh organization groups"
						title="Refresh organization groups"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button onClick={openCreate}>
						<Plus className="h-4 w-4" />
						New group
					</Button>
				</div>
			</div>

			<div className="flex flex-col gap-3 sm:flex-row sm:items-center">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search by name, owner, or member..."
					className="flex-1"
				/>
				<Badge variant="secondary" className="w-fit">
					{groups.length} group{groups.length === 1 ? "" : "s"}
				</Badge>
			</div>

			<div className="min-h-0">
				{loading ? (
					<div className="space-y-2">
						{[...Array(4)].map((_, index) => (
							<Skeleton key={index} className="h-12 w-full" />
						))}
					</div>
				) : filteredGroups.length > 0 ? (
					<DataTable>
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead>Name</DataTableHead>
								<DataTableHead>Owner</DataTableHead>
								<DataTableHead>Members</DataTableHead>
								<DataTableHead className="hidden md:table-cell">
									Updated
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right" />
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{filteredGroups.map((group) => (
								<DataTableRow
									key={group.id}
									clickable
									onClick={() => openEdit(group)}
								>
									<DataTableCell>
										<div className="flex items-start gap-2">
											<Users className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
											<div className="min-w-0">
												<div className="flex flex-wrap items-center gap-2">
													<span className="font-medium">{group.name}</span>
													<Badge variant="outline">Group</Badge>
												</div>
												<div className="mt-1 truncate font-mono text-xs text-muted-foreground">
													{group.id}
												</div>
											</div>
										</div>
									</DataTableCell>
									<DataTableCell className="text-sm text-muted-foreground">
										{groupOwnerName(group, organizations)}
									</DataTableCell>
									<DataTableCell className="text-sm text-muted-foreground">
										{groupMemberSummary(group, organizations)}
									</DataTableCell>
									<DataTableCell className="hidden text-sm text-muted-foreground md:table-cell">
										{group.updated_at
											? new Date(group.updated_at).toLocaleDateString()
											: "N/A"}
									</DataTableCell>
									<DataTableCell
										className="w-0 whitespace-nowrap text-right"
										onClick={(event) => event.stopPropagation()}
									>
										<DropdownMenu>
											<DropdownMenuTrigger asChild>
												<Button
													variant="ghost"
													size="icon"
													aria-label={`${group.name} actions`}
												>
													<MoreVertical className="h-4 w-4" />
												</Button>
											</DropdownMenuTrigger>
											<DropdownMenuContent align="end" className="w-48">
												<DropdownMenuItem
													className="min-h-9 whitespace-nowrap px-3"
													onClick={() => openEdit(group)}
												>
													<Pencil />
													Edit
												</DropdownMenuItem>
												<DropdownMenuSeparator />
												<DropdownMenuItem
													className="min-h-9 whitespace-nowrap px-3"
													onClick={() => handleDelete(group)}
												>
													<Trash2 />
													Delete
												</DropdownMenuItem>
											</DropdownMenuContent>
										</DropdownMenu>
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				) : (
					<div className="flex flex-col items-center justify-center rounded-xl border border-dashed py-12 text-center">
						<Building2 className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							{searchTerm ? "No groups match your search" : "No organization groups yet"}
						</h3>
						<p className="mt-2 max-w-md text-sm text-muted-foreground">
							{searchTerm
								? "Try a different search term or clear the filter."
								: "Create a group to bundle customer organizations into a reusable access boundary."}
						</p>
						<Button onClick={openCreate} className="mt-4">
							<Plus className="h-4 w-4" />
							New group
						</Button>
					</div>
				)}
			</div>

			<Dialog
				open={isCreateOpen || isEditOpen}
				onOpenChange={(open) => {
					if (!open) closeEditor();
				}}
			>
				<DialogContent className="sm:max-w-2xl">
					<form onSubmit={submitEditor}>
						<DialogHeader>
							<DialogTitle>
								{selectedGroup ? "Edit organization group" : "Create organization group"}
							</DialogTitle>
							<DialogDescription>
								{selectedGroup
									? "Rename the group or adjust its member organizations."
									: "Give the group a name and add the customer organizations it should cover."}
							</DialogDescription>
						</DialogHeader>

						{validationError ? (
							<div className="mt-4 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
								{validationError}
							</div>
						) : null}

						<div className="space-y-4 py-4">
							<div className="space-y-2">
								<Label htmlFor="group-name">Group name</Label>
								<Input
									id="group-name"
									value={form.name}
									onChange={(event) =>
										setForm((current) => ({ ...current, name: event.target.value }))
									}
									placeholder="North America Support"
									required
								/>
							</div>

							{selectedGroup ? (
								<div className="space-y-2">
									<Label>Owner organization</Label>
									<div className="rounded-xl border bg-muted/30 px-3 py-2 text-sm">
										{groupOwnerName(selectedGroup, organizations)}
									</div>
								</div>
							) : null}

							<div className="space-y-2">
								<Label htmlFor="group-members">Member organizations</Label>
								<MultiCombobox
									id="group-members"
									value={form.memberOrganizationIds}
									onValueChange={(value) =>
										setForm((current) => ({ ...current, memberOrganizationIds: value }))
									}
									options={memberOrganizations.map((org) => ({
										value: org.id,
										label: org.name,
										description: org.domain ? `@${org.domain}` : undefined,
									}))}
									placeholder="Select member organizations"
									searchPlaceholder="Search member organizations..."
									emptyText="No member organizations found."
								/>
							</div>
						</div>

						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={closeEditor}
								disabled={savePending}
							>
								Cancel
							</Button>
							<Button type="submit" disabled={savePending}>
								{savePending ? "Saving..." : selectedGroup ? "Save changes" : "Create group"}
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>

			<AlertDialog
				open={isDeleteOpen}
				onOpenChange={(open) => {
					setIsDeleteOpen(open);
					if (!open) resetForm();
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete {selectedGroup?.name ?? "this organization group"}?
						</AlertDialogTitle>
						<AlertDialogDescription>
							This removes the group from access boundaries. Existing role assignments
							will keep their current selections, but the deleted group can no longer be used.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={confirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteMutation.isPending ? "Deleting..." : "Delete"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
