import { useState } from "react";
import {
	Building2,
	FileText,
	MoreVertical,
	Pencil,
	Plus,
	Power,
	RefreshCw,
	Settings,
	Star,
} from "lucide-react";

import { SearchBox } from "@/components/search/SearchBox";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
	useCreateOrganization,
	useOrganizations,
	useUpdateOrganization,
} from "@/hooks/useOrganizations";
import { useSearch } from "@/hooks/useSearch";
import type { components } from "@/lib/v1";
import { RequiredInstructionsSettings } from "@/pages/settings/RequiredInstructionsSettings";

type Organization = components["schemas"]["OrganizationPublic"];
type EditTab = "general" | "instructions";

interface OrganizationFormData {
	name: string;
	domain: string;
	isActive: boolean;
}

const EMPTY_FORM: OrganizationFormData = {
	name: "",
	domain: "",
	isActive: true,
};

export function Organizations() {
	const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
	const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
	const [isDisableDialogOpen, setIsDisableDialogOpen] = useState(false);
	const [selectedOrg, setSelectedOrg] = useState<Organization>();
	const [activeEditTab, setActiveEditTab] = useState<EditTab>("general");
	const [formData, setFormData] =
		useState<OrganizationFormData>(EMPTY_FORM);
	const [searchTerm, setSearchTerm] = useState("");
	const [showInactive, setShowInactive] = useState(false);

	const { data, isLoading, refetch } = useOrganizations({
		includeInactive: showInactive,
	});
	const organizations: Organization[] = Array.isArray(data) ? data : [];
	const visibleOrganizations = showInactive
		? organizations
		: organizations.filter((org) => org.is_active);
	const filteredOrgs = useSearch(visibleOrganizations, searchTerm, [
		"name",
		"domain",
		"id",
	]);

	const createMutation = useCreateOrganization();
	const updateMutation = useUpdateOrganization();

	const resetSelection = () => {
		setSelectedOrg(undefined);
		setFormData(EMPTY_FORM);
		setActiveEditTab("general");
	};

	const handleCreate = () => {
		setFormData(EMPTY_FORM);
		setIsCreateDialogOpen(true);
	};

	const handleEdit = (org: Organization, tab: EditTab = "general") => {
		setSelectedOrg(org);
		setFormData({
			name: org.name,
			domain: org.domain || "",
			isActive: org.is_active,
		});
		setActiveEditTab(tab);
		setIsEditDialogOpen(true);
	};

	const handleSubmitCreate = async (event: React.FormEvent) => {
		event.preventDefault();
		await createMutation.mutateAsync({
			body: {
				name: formData.name.trim(),
				domain: formData.domain.trim() || null,
				is_active: true,
				is_provider: false,
			},
		});
		setIsCreateDialogOpen(false);
		setFormData(EMPTY_FORM);
	};

	const handleSubmitEdit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!selectedOrg) return;

		await updateMutation.mutateAsync({
			params: { path: { org_id: selectedOrg.id } },
			body: {
				name: formData.name.trim(),
				domain: formData.domain.trim(),
				is_active: formData.isActive,
			},
		});
		setIsEditDialogOpen(false);
		resetSelection();
	};

	const setOrganizationActive = async (
		org: Organization,
		isActive: boolean,
	) => {
		await updateMutation.mutateAsync({
			params: { path: { org_id: org.id } },
			body: { is_active: isActive },
		});
	};

	const handleToggleActive = async (org: Organization) => {
		if (org.is_provider) return;
		if (org.is_active) {
			setSelectedOrg(org);
			setIsDisableDialogOpen(true);
			return;
		}
		await setOrganizationActive(org, true);
	};

	const handleConfirmDisable = async () => {
		if (!selectedOrg) return;
		await setOrganizationActive(selectedOrg, false);
		setIsDisableDialogOpen(false);
		resetSelection();
	};

	const handleCreateDialogChange = (open: boolean) => {
		setIsCreateDialogOpen(open);
		if (!open) setFormData(EMPTY_FORM);
	};

	const handleEditDialogChange = (open: boolean) => {
		setIsEditDialogOpen(open);
		if (!open) resetSelection();
	};

	return (
		<div className="mx-auto flex h-full max-w-7xl flex-col space-y-6">
			<div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">
						Organizations
					</h1>
					<p className="mt-2 text-muted-foreground">
						Manage customer organizations and their configurations
					</p>
				</div>
				<div className="flex items-center gap-2 sm:shrink-0">
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						aria-label="Refresh organizations"
						title="Refresh organizations"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button onClick={handleCreate} className="flex-1 sm:flex-none">
						<Plus className="h-4 w-4" />
						New Organization
					</Button>
				</div>
			</div>

			<div className="flex flex-col gap-3 sm:flex-row sm:items-center">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search by name, domain, or ID..."
					className="flex-1"
				/>
				<div className="flex items-center gap-2 sm:ml-auto">
					<Switch
						id="show-inactive-organizations"
						checked={showInactive}
						onCheckedChange={setShowInactive}
					/>
					<Label
						htmlFor="show-inactive-organizations"
						className="cursor-pointer text-sm text-muted-foreground"
					>
						Show Inactive
					</Label>
				</div>
			</div>

			<div className="min-h-0 flex-1">
				{isLoading ? (
					<div className="space-y-2">
						{[...Array(5)].map((_, index) => (
							<Skeleton key={index} className="h-12 w-full" />
						))}
					</div>
				) : filteredOrgs.length > 0 ? (
					<DataTable>
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead>Name</DataTableHead>
								<DataTableHead>Domain</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">
									Status
								</DataTableHead>
								<DataTableHead className="hidden w-0 whitespace-nowrap md:table-cell">
									Created
								</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right" />
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{filteredOrgs.map((org) => (
								<DataTableRow
									key={org.id}
									clickable
									onClick={() => handleEdit(org)}
									className={`group/row${
										org.is_provider
											? " bg-amber-50/50 dark:bg-amber-950/20"
											: ""
									}${!org.is_active ? " opacity-60" : ""}`}
								>
									<DataTableCell>
										<div className="flex items-start gap-2">
											{org.is_provider && (
												<Star className="mt-0.5 h-4 w-4 shrink-0 fill-amber-500 text-amber-500" />
											)}
											<div className="min-w-0">
												<div className="flex flex-wrap items-center gap-2">
													<span className="font-medium">{org.name}</span>
													{org.is_provider && (
														<Badge
															variant="outline"
															className="border-amber-300 bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300"
														>
															Provider
														</Badge>
													)}
												</div>
												<div className="mt-1 truncate font-mono text-xs text-muted-foreground">
													{org.id}
												</div>
											</div>
										</div>
									</DataTableCell>
									<DataTableCell className="text-sm text-muted-foreground">
										{org.domain || "—"}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge variant={org.is_active ? "default" : "secondary"}>
											{org.is_active ? "Active" : "Inactive"}
										</Badge>
									</DataTableCell>
									<DataTableCell className="hidden w-0 whitespace-nowrap text-sm text-muted-foreground md:table-cell">
										{org.created_at
											? new Date(org.created_at).toLocaleDateString()
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
													aria-label={`${org.name} actions`}
												>
													<MoreVertical className="h-4 w-4" />
												</Button>
											</DropdownMenuTrigger>
											<DropdownMenuContent align="end" className="w-48">
												<DropdownMenuItem
													className="min-h-9 whitespace-nowrap px-3"
													onClick={() => handleEdit(org)}
													aria-label={`Edit ${org.name}`}
												>
													<Pencil />
													Edit
												</DropdownMenuItem>
												<DropdownMenuSeparator />
												<DropdownMenuItem
													className="min-h-9 whitespace-nowrap px-3"
													onClick={() => handleToggleActive(org)}
													disabled={org.is_provider}
													aria-label={`${org.is_active ? "Disable" : "Enable"} ${org.name}`}
												>
													<Power />
													{org.is_active ? "Disable" : "Enable"}
												</DropdownMenuItem>
											</DropdownMenuContent>
										</DropdownMenu>
									</DataTableCell>
								</DataTableRow>
							))}
						</DataTableBody>
					</DataTable>
				) : (
					<div className="flex flex-col items-center justify-center py-12 text-center">
						<Building2 className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							{searchTerm
								? "No organizations match your search"
								: showInactive
									? "No organizations found"
									: "No active organizations found"}
						</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							{searchTerm
								? "Try adjusting your search or clearing the filter."
								: showInactive
									? "Create your first organization to get started."
									: "Show inactive organizations or create a new one."}
						</p>
						<Button onClick={handleCreate} className="mt-4">
							<Plus className="h-4 w-4" />
							New Organization
						</Button>
					</div>
				)}
			</div>

			<Dialog open={isCreateDialogOpen} onOpenChange={handleCreateDialogChange}>
				<DialogContent>
					<form onSubmit={handleSubmitCreate}>
						<DialogHeader>
							<DialogTitle>Create Organization</DialogTitle>
							<DialogDescription>
								Add a customer organization to the platform.
							</DialogDescription>
						</DialogHeader>
						<div className="space-y-4 py-4">
							<div className="space-y-2">
								<Label htmlFor="organization-name">Organization Name</Label>
								<Input
									id="organization-name"
									value={formData.name}
									onChange={(event) =>
										setFormData({ ...formData, name: event.target.value })
									}
									placeholder="Acme Corporation"
									required
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="organization-domain">Email Domain</Label>
								<Input
									id="organization-domain"
									value={formData.domain}
									onChange={(event) =>
										setFormData({ ...formData, domain: event.target.value })
									}
									placeholder="acme.com"
								/>
								<p className="text-xs text-muted-foreground">
									Users with this email domain are automatically provisioned to
									this organization.
								</p>
							</div>
						</div>
						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={() => handleCreateDialogChange(false)}
							>
								Cancel
							</Button>
							<Button type="submit" disabled={createMutation.isPending}>
								{createMutation.isPending ? "Creating..." : "Create Organization"}
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>

			<Dialog open={isEditDialogOpen} onOpenChange={handleEditDialogChange}>
				<DialogContent className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-3xl">
					<DialogHeader>
						<DialogTitle>Edit Organization</DialogTitle>
						<DialogDescription>
							Manage details, status, and instructions for {selectedOrg?.name}.
						</DialogDescription>
					</DialogHeader>

					<Tabs
						value={activeEditTab}
						onValueChange={(value) => setActiveEditTab(value as EditTab)}
						className="min-h-0 flex-1 overflow-hidden"
					>
						<TabsList className="w-full shrink-0">
							<TabsTrigger value="general">
								<Settings className="h-4 w-4" />
								General
							</TabsTrigger>
							<TabsTrigger value="instructions">
								<FileText className="h-4 w-4" />
								Instructions
							</TabsTrigger>
						</TabsList>

						<div className="min-h-0 flex-1 overflow-y-auto py-4">
							<TabsContent value="general" className="mt-0">
								<form
									id="edit-organization-form"
									onSubmit={handleSubmitEdit}
									className="space-y-5"
								>
									<div className="space-y-2">
										<Label htmlFor="edit-organization-name">
											Organization Name
										</Label>
										<Input
											id="edit-organization-name"
											value={formData.name}
											onChange={(event) =>
												setFormData({ ...formData, name: event.target.value })
											}
											required
										/>
									</div>
									<div className="space-y-2">
										<Label htmlFor="edit-organization-domain">
											Email Domain
										</Label>
										<Input
											id="edit-organization-domain"
											value={formData.domain}
											onChange={(event) =>
												setFormData({ ...formData, domain: event.target.value })
											}
											placeholder="acme.com"
										/>
										<p className="text-xs text-muted-foreground">
											Users with this email domain are automatically provisioned to
											this organization.
										</p>
									</div>

									<div className="flex items-center justify-between gap-4 rounded-xl bg-muted/50 p-4 ring-1 ring-foreground/5">
										<div className="space-y-1">
											<Label htmlFor="edit-organization-status">
												Organization Status
											</Label>
											<p className="text-xs text-muted-foreground">
												{selectedOrg?.is_provider
													? "The provider organization must remain active."
													: "Inactive organizations are hidden from active organization lists."}
											</p>
										</div>
										<Switch
											id="edit-organization-status"
											checked={formData.isActive}
											onCheckedChange={(isActive) =>
												setFormData({ ...formData, isActive })
											}
											disabled={selectedOrg?.is_provider}
										/>
									</div>
								</form>
							</TabsContent>

							<TabsContent value="instructions" className="mt-0">
								{selectedOrg && (
									<RequiredInstructionsSettings
										key={selectedOrg.id}
										organizationId={selectedOrg.id}
										embedded
									/>
								)}
							</TabsContent>
						</div>
					</Tabs>

					{activeEditTab === "general" && (
						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								onClick={() => handleEditDialogChange(false)}
							>
								Cancel
							</Button>
							<Button
								type="submit"
								form="edit-organization-form"
								disabled={updateMutation.isPending}
							>
								{updateMutation.isPending ? "Saving..." : "Save Changes"}
							</Button>
						</DialogFooter>
					)}
				</DialogContent>
			</Dialog>

			<AlertDialog
				open={isDisableDialogOpen}
				onOpenChange={(open) => {
					setIsDisableDialogOpen(open);
					if (!open) resetSelection();
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Disable {selectedOrg?.name}?</AlertDialogTitle>
						<AlertDialogDescription>
							{selectedOrg?.name} will be removed from active organization lists.
							You can re-enable it later by showing inactive organizations.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDisable}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{updateMutation.isPending
								? "Disabling..."
								: "Disable"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
