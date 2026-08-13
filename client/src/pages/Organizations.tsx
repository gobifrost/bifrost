import { useState } from "react";
import { Building2, FileText, Plus, Pencil, Trash2, RefreshCw, Star } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import {
	useOrganizations,
	useCreateOrganization,
	useUpdateOrganization,
	useDeleteOrganization,
} from "@/hooks/useOrganizations";
import type { components } from "@/lib/v1";
import { RequiredInstructionsSettings } from "@/pages/settings/RequiredInstructionsSettings";
type Organization = components["schemas"]["OrganizationPublic"];

interface OrganizationFormData {
	name: string;
	domain: string;
}

export function Organizations() {
	const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
	const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
	const [isInstructionsDialogOpen, setIsInstructionsDialogOpen] = useState(false);
	const [selectedOrg, setSelectedOrg] = useState<Organization | undefined>();
	const [formData, setFormData] = useState<OrganizationFormData>({
		name: "",
		domain: "",
	});
	const [searchTerm, setSearchTerm] = useState("");

	const { data, isLoading, refetch } = useOrganizations();
	const organizations: Organization[] = Array.isArray(data) ? data : [];

	const createMutation = useCreateOrganization();
	const updateMutation = useUpdateOrganization();
	const deleteMutation = useDeleteOrganization();

	// Apply search filter
	const filteredOrgs = useSearch(organizations, searchTerm, [
		"name",
		"domain",
	]);

	const handleCreate = () => {
		setFormData({ name: "", domain: "" });
		setIsCreateDialogOpen(true);
	};

	const handleEdit = (org: Organization) => {
		setSelectedOrg(org);
		setFormData({
			name: org.name,
			domain: org.domain || "",
		});
		setIsEditDialogOpen(true);
	};

	const handleDelete = (org: Organization) => {
		setSelectedOrg(org);
		setIsDeleteDialogOpen(true);
	};

	const handleInstructions = (org: Organization) => {
		setSelectedOrg(org);
		setIsInstructionsDialogOpen(true);
	};

	const handleSubmitCreate = async (e: React.FormEvent) => {
		e.preventDefault();
		await createMutation.mutateAsync({
			body: {
				name: formData.name,
				domain: formData.domain || null,
				is_active: true,
				is_provider: false,
			},
		});
		setIsCreateDialogOpen(false);
		setFormData({ name: "", domain: "" });
	};

	const handleSubmitEdit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!selectedOrg) return;

		await updateMutation.mutateAsync({
			params: { path: { org_id: selectedOrg.id } },
			body: {
				name: formData.name || null,
				domain: formData.domain || null,
				is_active: null,
			},
		});
		setIsEditDialogOpen(false);
		setSelectedOrg(undefined);
		setFormData({ name: "", domain: "" });
	};

	const handleConfirmDelete = async () => {
		if (!selectedOrg) return;

		await deleteMutation.mutateAsync({
			params: { path: { org_id: selectedOrg.id } },
		});
		setIsDeleteDialogOpen(false);
		setSelectedOrg(undefined);
	};

	const handleDialogClose = (open: boolean) => {
		if (!open) {
			setIsCreateDialogOpen(false);
			setIsEditDialogOpen(false);
			setFormData({ name: "", domain: "" });
			setSelectedOrg(undefined);
		}
	};

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			{/* Header: title left, actions right */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">
						Organizations
					</h1>
					<p className="mt-2 text-muted-foreground">
						Manage customer organizations and their configurations
					</p>
				</div>
				<div className="flex items-center gap-2">
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						title="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button
						variant="outline"
						size="icon"
						onClick={handleCreate}
						title="Create Organization"
					>
						<Plus className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Search and Filters */}
			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search organizations by name or domain..."
					className="flex-1"
				/>
			</div>

			{/* Content directly - DataTable, no Card wrapper */}
			{isLoading ? (
				<div className="space-y-2">
					{[...Array(5)].map((_, i) => (
						<Skeleton key={i} className="h-12 w-full" />
					))}
				</div>
			) : filteredOrgs && filteredOrgs.length > 0 ? (
				<div className="flex-1 min-h-0">
					<DataTable className="max-h-full">
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead>Name</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">Domain</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">Organization ID</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">Status</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">Created</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right"></DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{filteredOrgs.map((org) => (
								<DataTableRow
									key={org.id}
									className={
										org.is_provider
											? "bg-amber-50/50 dark:bg-amber-950/20"
											: ""
									}
								>
									<DataTableCell className="font-medium">
										<div className="flex items-center gap-2">
											{org.is_provider && (
												<Star className="h-4 w-4 text-amber-500 fill-amber-500" />
											)}
											{org.name}
											{org.is_provider && (
												<Badge
													variant="outline"
													className="text-amber-600 border-amber-300 bg-amber-50 dark:bg-amber-950/50"
												>
													Provider
												</Badge>
											)}
										</div>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
										{org.domain || "-"}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap font-mono text-xs text-muted-foreground">
										{org.id}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge
											variant={
												org.is_active
													? "default"
													: "secondary"
											}
										>
											{org.is_active
												? "Active"
												: "Inactive"}
										</Badge>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-sm">
										{org.created_at
											? new Date(
													org.created_at,
												).toLocaleDateString()
											: "N/A"}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right">
										<div className="flex items-center justify-end gap-2">
											<Button
												variant="ghost"
												size="icon"
												onClick={() => handleInstructions(org)}
												title="Edit required instructions"
											>
												<FileText className="h-4 w-4" />
											</Button>
											<Button
												variant="ghost"
												size="icon"
												onClick={() => handleEdit(org)}
												title="Edit organization"
											>
												<Pencil className="h-4 w-4" />
											</Button>
											<Button
												variant="ghost"
												size="icon"
												onClick={() =>
													handleDelete(org)
												}
												disabled={org.is_provider}
												title={
													org.is_provider
														? "Provider organization cannot be deleted"
														: "Delete organization"
												}
											>
												<Trash2 className="h-4 w-4" />
											</Button>
										</div>
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				</div>
			) : (
				<div className="flex flex-col items-center justify-center py-12 text-center">
					<Building2 className="h-12 w-12 text-muted-foreground" />
					<h3 className="mt-4 text-lg font-semibold">
						{searchTerm
							? "No organizations match your search"
							: "No organizations found"}
					</h3>
					<p className="mt-2 text-sm text-muted-foreground">
						{searchTerm
							? "Try adjusting your search term or clear the filter"
							: "Create your first organization to get started"}
					</p>
					<Button
						variant="outline"
						size="icon"
						onClick={handleCreate}
						title="Create Organization"
						className="mt-4"
					>
						<Plus className="h-4 w-4" />
					</Button>
				</div>
			)}

			{/* Create Dialog */}
			<Dialog open={isCreateDialogOpen} onOpenChange={handleDialogClose}>
				<DialogContent>
					<form onSubmit={handleSubmitCreate}>
						<DialogHeader>
							<DialogTitle>Create Organization</DialogTitle>
							<DialogDescription>
								Add a new customer organization to the platform
							</DialogDescription>
						</DialogHeader>
						<div className="space-y-4 py-4">
							<div className="space-y-2">
								<Label htmlFor="name">
									Organization Name *
								</Label>
								<Input
									id="name"
									value={formData.name}
									onChange={(e) =>
										setFormData({
											...formData,
											name: e.target.value,
										})
									}
									placeholder="Acme Corporation"
									required
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="domain">Email Domain</Label>
								<Input
									id="domain"
									value={formData.domain}
									onChange={(e) =>
										setFormData({
											...formData,
											domain: e.target.value,
										})
									}
									placeholder="acme.com"
								/>
								<p className="text-xs text-muted-foreground">
									Users with this email domain will be
									auto-provisioned to this organization
								</p>
							</div>
						</div>
						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={() => handleDialogClose(false)}
							>
								Cancel
							</Button>
							<Button
								type="submit"
								disabled={createMutation.isPending}
							>
								{createMutation.isPending
									? "Creating..."
									: "Create"}
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>

			{/* Edit Dialog */}
			<Dialog open={isEditDialogOpen} onOpenChange={handleDialogClose}>
				<DialogContent>
					<form onSubmit={handleSubmitEdit}>
						<DialogHeader>
							<DialogTitle>Edit Organization</DialogTitle>
							<DialogDescription>
								Update organization details
							</DialogDescription>
						</DialogHeader>
						<div className="space-y-4 py-4">
							<div className="space-y-2">
								<Label htmlFor="edit-name">
									Organization Name *
								</Label>
								<Input
									id="edit-name"
									value={formData.name}
									onChange={(e) =>
										setFormData({
											...formData,
											name: e.target.value,
										})
									}
									placeholder="Acme Corporation"
									required
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="edit-domain">
									Email Domain
								</Label>
								<Input
									id="edit-domain"
									value={formData.domain}
									onChange={(e) =>
										setFormData({
											...formData,
											domain: e.target.value,
										})
									}
									placeholder="acme.com"
								/>
								<p className="text-xs text-muted-foreground">
									Users with this email domain will be
									auto-provisioned to this organization
								</p>
							</div>
						</div>
						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={() => handleDialogClose(false)}
							>
								Cancel
							</Button>
							<Button
								type="submit"
								disabled={updateMutation.isPending}
							>
								{updateMutation.isPending
									? "Updating..."
									: "Update"}
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>

			{/* Organization Instructions Dialog */}
			<Dialog
				open={isInstructionsDialogOpen}
				onOpenChange={(open) => {
					setIsInstructionsDialogOpen(open);
					if (!open) setSelectedOrg(undefined);
				}}
			>
				<DialogContent className="max-w-3xl">
					<DialogHeader>
						<DialogTitle>{selectedOrg?.name}</DialogTitle>
						<DialogDescription>
							Configure instructions that apply only to this organization.
						</DialogDescription>
					</DialogHeader>
					{selectedOrg && (
						<RequiredInstructionsSettings
							key={selectedOrg.id}
							organizationId={selectedOrg.id}
							embedded
						/>
					)}
				</DialogContent>
			</Dialog>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={isDeleteDialogOpen}
				onOpenChange={setIsDeleteDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Are you sure?</AlertDialogTitle>
						<AlertDialogDescription>
							This will permanently delete the organization "
							{selectedOrg?.name}". This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteMutation.isPending
								? "Deleting..."
								: "Delete"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
