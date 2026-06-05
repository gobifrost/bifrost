import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
	Plus,
	RefreshCw,
	FileCode,
	Pencil,
	Trash2,
	PlayCircle,
	Globe,
	Building2,
	LayoutGrid,
	Table as TableIcon,
	AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
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
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { useForms, useDeleteForm, useUpdateForm } from "@/hooks/useForms";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { term, useTerminology } from "@/lib/terminology";
import type { components } from "@/lib/v1";

type FormPublic = components["schemas"]["FormPublic"];
type Organization = components["schemas"]["OrganizationPublic"];

export function Forms() {
	const navigate = useNavigate();
	const terminology = useTerminology();
	const { isPlatformAdmin } = useAuth();
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [searchTerm, setSearchTerm] = useState("");
	const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
	const [isDisableDialogOpen, setIsDisableDialogOpen] = useState(false);
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
	const [selectedForm, setSelectedForm] = useState<{
		id: string;
		name: string;
		isActive: boolean;
	} | null>(null);

	// Pass filterOrgId to backend for filtering (undefined = all, null = global only)
	// For platform admins, undefined means show all. For non-admins, backend handles filtering.
	const {
		data: forms,
		isLoading,
		refetch,
	} = useForms(isPlatformAdmin ? filterOrgId : undefined);
	const deleteForm = useDeleteForm();
	const updateForm = useUpdateForm();

	// Fetch organizations for the org name lookup (platform admins only)
	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});

	// Helper to get organization name from ID
	const getOrgName = (orgId: string | null | undefined): string => {
		if (!orgId) return "Global";
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return org?.name || orgId;
	};

	// For now, only platform admins can manage forms
	const canManageForms = isPlatformAdmin;

	// Build validation map from backend-provided missingRequiredParams
	const formValidation = useMemo(() => {
		const validationMap = new Map<
			string,
			{ valid: boolean; missingParams: string[] }
		>();

		forms?.forEach((form) => {
			const formWithParams = form as FormPublic & {
				missingRequiredParams?: string[];
			};
			const missingParams = formWithParams.missingRequiredParams || [];
			validationMap.set(form.id, {
				valid: missingParams.length === 0,
				missingParams,
			});
		});

		return validationMap;
	}, [forms]);

	const handleCreate = () => {
		navigate("/forms/new");
	};

	const handleEdit = (formId: string) => {
		navigate(`/forms/${formId}/edit`);
	};

	const handleDelete = (formId: string, formName: string, isActive: boolean) => {
		setSelectedForm({ id: formId, name: formName, isActive });
		setIsDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!selectedForm) return;
		// If the form is already inactive, purge it permanently
		const purge = !selectedForm.isActive;
		await deleteForm.mutateAsync({
			params: {
				path: { form_id: selectedForm.id },
				query: { purge },
			},
		});
		setIsDeleteDialogOpen(false);
		setSelectedForm(null);
	};

	const handleToggleActive = (
		formId: string,
		formName: string,
		currentlyActive: boolean,
	) => {
		setSelectedForm({
			id: formId,
			name: formName,
			isActive: currentlyActive,
		});
		setIsDisableDialogOpen(true);
	};

	const handleConfirmToggleActive = async () => {
		if (!selectedForm) return;
		await updateForm.mutateAsync({
			params: { path: { form_id: selectedForm.id } },
			body: {
				name: null,
				description: null,
				workflow_id: null,
				form_schema: null,
				is_active: !selectedForm.isActive,
				access_level: null,
				launch_workflow_id: null,
				allowed_query_params: null,
				default_launch_params: null,
				clear_roles: false,
			},
		});
		setIsDisableDialogOpen(false);
		setSelectedForm(null);
	};

	const handleLaunch = (formId: string) => {
		navigate(`/execute/${formId}`);
	};

	// Filter forms based on validation only (backend handles org filtering)
	const scopeFilteredForms =
		forms?.filter((form) => {
			// Hide invalid forms from regular users
			if (!isPlatformAdmin) {
				const validation = formValidation.get(form.id);
				if (validation && !validation.valid) {
					return false;
				}
			}
			return true;
		}) || [];

	// Apply search filter
	const filteredForms = useSearch(scopeFilteredForms, searchTerm, [
		"name",
		"description",
		"workflow_id",
		(form) => form.id,
	]);

	return (
		<div className="flex flex-col space-y-6 max-w-7xl mx-auto">
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
				<div>
					<h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
						{term(terminology, "form", "plural")}
					</h1>
					<p className="mt-2 text-muted-foreground">
						{canManageForms
							? `Launch workflows with guided ${term(terminology, "form", "singularLower")} interfaces`
							: `Launch workflows with guided ${term(terminology, "form", "pluralLower")}`}
					</p>
				</div>
				<div className="flex flex-wrap gap-2">
					{canManageForms && (
						<ToggleGroup
							type="single"
							value={viewMode}
							onValueChange={(value: string) =>
								value && setViewMode(value as "grid" | "table")
							}
						>
							<ToggleGroupItem
								value="grid"
								aria-label="Grid view"
								size="sm"
							>
								<LayoutGrid className="h-4 w-4" />
							</ToggleGroupItem>
							<ToggleGroupItem
								value="table"
								aria-label="Table view"
								size="sm"
							>
								<TableIcon className="h-4 w-4" />
							</ToggleGroupItem>
						</ToggleGroup>
					)}
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						title="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					{canManageForms && (
						<Button
							variant="outline"
							size="icon"
							onClick={handleCreate}
							title={`Create ${term(terminology, "form", "singular")}`}
						>
							<Plus className="h-4 w-4" />
						</Button>
					)}
				</div>
			</div>

			{/* Search and Filters */}
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder={`Search ${term(terminology, "form", "pluralLower")} by name, description, or workflow...`}
					className="flex-1"
				/>
				{isPlatformAdmin && (
					<div className="w-full sm:w-64">
						<OrganizationSelect
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={true}
							placeholder="All organizations"
						/>
					</div>
				)}
			</div>

			{isLoading ? (
				viewMode === "grid" || !canManageForms ? (
					<div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]">
						{[...Array(6)].map((_, i) => (
							<Skeleton key={i} className="h-48 w-full" />
						))}
					</div>
				) : (
					<div className="space-y-2">
						{[...Array(3)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				)
			) : filteredForms && filteredForms.length > 0 ? (
				viewMode === "grid" || !canManageForms ? (
					<div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]">
						{filteredForms.map((form) => (
							<Card
								key={form.id}
								className="hover:border-primary transition-colors flex flex-col"
							>
								<CardHeader className="pb-3">
									<CardTitle
										className="text-base truncate"
										title={form.name}
									>
										{form.name}
									</CardTitle>
									{!formValidation.get(form.id)?.valid &&
										canManageForms && (
											<Badge
												variant="destructive"
												className="gap-1 w-fit mt-1"
											>
												<AlertTriangle className="h-3 w-3" />
												Invalid
											</Badge>
										)}
									<CardDescription className="mt-1.5 text-sm line-clamp-2">
										{form.description || (
											<span className="italic text-muted-foreground/60">
												No description
											</span>
										)}
									</CardDescription>
								</CardHeader>
								<CardContent className="flex-1 flex flex-col pt-0">
									{/* Validation warnings */}
									{!formValidation.get(form.id)?.valid &&
										canManageForms && (
											<div className="mb-3 pb-3 border-b">
												<span className="text-destructive font-medium text-sm">
													Missing required parameters:
												</span>
												<div className="mt-1.5 flex flex-wrap gap-1">
													{formValidation
														.get(form.id)
														?.missingParams.map(
															(param) => (
																<Badge
																	key={param}
																	variant="outline"
																	className="text-xs font-mono"
																>
																	{param}
																</Badge>
															),
														)}
												</div>
											</div>
										)}

									{/* Spacer to push bottom content down */}
									<div className="flex-1" />

									{/* Scope badge - fixed position above buttons */}
									{isPlatformAdmin && (
										<div className="mb-3">
											{form.organization_id ? (
												<Badge
													variant="outline"
													className="text-xs"
												>
													<Building2 className="mr-1 h-3 w-3" />
													{getOrgName(
														form.organization_id,
													)}
												</Badge>
											) : (
												<Badge
													variant="default"
													className="text-xs"
												>
													<Globe className="mr-1 h-3 w-3" />
													Global
												</Badge>
											)}
										</div>
									)}

									{/* Action buttons */}
									<div className="flex items-center gap-2">
										<Button
											className="flex-1"
											onClick={() =>
												handleLaunch(form.id)
											}
											disabled={
												(!form.is_active &&
													!canManageForms) ||
												!formValidation.get(form.id)
													?.valid
											}
											title={
												!formValidation.get(form.id)
													?.valid
													? `Cannot launch: Missing required parameters (${formValidation.get(form.id)?.missingParams.join(", ")})`
													: !form.is_active &&
														  !canManageForms
														? `${term(terminology, "form", "singular")} is disabled`
														: `Launch ${term(terminology, "form", "singularLower")}`
											}
										>
											<PlayCircle className="mr-2 h-4 w-4" />
											Launch
										</Button>
										{canManageForms && (
											<>
												<Button
													variant="outline"
													size="icon"
													onClick={() =>
														handleEdit(form.id)
													}
													title={`Edit ${term(terminology, "form", "singularLower")}`}
												>
													<Pencil className="h-4 w-4" />
												</Button>
												<Button
													variant="outline"
													size="icon"
													onClick={() =>
														handleDelete(
															form.id,
															form.name,
															form.is_active,
														)
													}
													title={`Delete ${term(terminology, "form", "singularLower")}`}
												>
													<Trash2 className="h-4 w-4" />
												</Button>
												<Tooltip>
													<TooltipTrigger asChild>
														<div className="shrink-0 ml-auto">
															<Switch
																checked={
																	form.is_active
																}
																onCheckedChange={() =>
																	handleToggleActive(
																		form.id,
																		form.name,
																		form.is_active,
																	)
																}
																id={`form-active-${form.id}`}
															/>
														</div>
													</TooltipTrigger>
													<TooltipContent>
														{form.is_active
															? "Enabled - click to disable"
															: "Disabled - click to enable"}
													</TooltipContent>
												</Tooltip>
											</>
										)}
									</div>
								</CardContent>
							</Card>
						))}
					</div>
				) : (
					<div className="flex-1 min-h-0">
						<DataTable className="max-h-full">
							<DataTableHeader>
								<DataTableRow>
									{isPlatformAdmin && (
										<DataTableHead className="w-0 whitespace-nowrap">
											Organization
										</DataTableHead>
									)}
									<DataTableHead>Name</DataTableHead>
									<DataTableHead>Description</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap">Status</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap text-right" />
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{filteredForms.map((form) => {
									const validation = formValidation.get(
										form.id,
									);
									return (
										<DataTableRow key={form.id}>
											{isPlatformAdmin && (
												<DataTableCell className="w-0 whitespace-nowrap">
													{form.organization_id ? (
														<Badge
															variant="outline"
															className="text-xs"
														>
															<Building2 className="mr-1 h-3 w-3" />
															{getOrgName(
																form.organization_id,
															)}
														</Badge>
													) : (
														<Badge
															variant="default"
															className="text-xs"
														>
															<Globe className="mr-1 h-3 w-3" />
															Global
														</Badge>
													)}
												</DataTableCell>
											)}
											<DataTableCell className="font-medium">
												{form.name}
											</DataTableCell>
											<DataTableCell className="max-w-xs truncate text-muted-foreground">
												{form.description || (
													<span className="italic">
														No description
													</span>
												)}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap">
												{canManageForms ? (
													<Tooltip>
														<TooltipTrigger asChild>
															<div className="w-fit">
																<Switch
																	checked={
																		form.is_active
																	}
																	onCheckedChange={() =>
																		handleToggleActive(
																			form.id,
																			form.name,
																			form.is_active,
																		)
																	}
																	id={`form-active-table-${form.id}`}
																/>
															</div>
														</TooltipTrigger>
														<TooltipContent>
															{form.is_active
																? "Enabled - click to disable"
																: "Disabled - click to enable"}
														</TooltipContent>
													</Tooltip>
												) : (
													<Badge
														variant={
															form.is_active
																? "default"
																: "secondary"
														}
													>
														{form.is_active
																	? "Enabled"
																	: "Inactive"}
													</Badge>
												)}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-right">
												<div className="flex gap-1 justify-end">
													<Button
														size="sm"
														onClick={() =>
															handleLaunch(
																form.id,
															)
														}
														disabled={
															(!form.is_active &&
																!canManageForms) ||
															!validation?.valid
														}
														title={
															!validation?.valid
																? `Cannot launch: Missing ${validation?.missingParams.join(", ")}`
																: !form.is_active &&
																	  !canManageForms
																	? `${term(terminology, "form", "singular")} is disabled`
																	: `Launch ${term(terminology, "form", "singularLower")}`
														}
													>
														<PlayCircle className="h-4 w-4" />
													</Button>
													{canManageForms && (
														<>
															<Button
																variant="ghost"
																size="sm"
																onClick={() =>
																	handleEdit(
																		form.id,
																	)
																}
																title={`Edit ${term(terminology, "form", "singularLower")}`}
															>
																<Pencil className="h-4 w-4" />
															</Button>
															<Button
																variant="ghost"
																size="sm"
																onClick={() =>
																	handleDelete(
																		form.id,
																		form.name,
																		form.is_active,
																	)
																}
																title={`Delete ${term(terminology, "form", "singularLower")}`}
															>
																<Trash2 className="h-4 w-4" />
															</Button>
														</>
													)}
												</div>
											</DataTableCell>
										</DataTableRow>
									);
								})}
							</DataTableBody>
						</DataTable>
					</div>
				)
			) : (
				<Card>
					<CardContent className="flex flex-col items-center justify-center py-12 text-center">
						<FileCode className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							{searchTerm
								? `No ${term(terminology, "form", "pluralLower")} match your search`
								: `No ${term(terminology, "form", "pluralLower")} found`}
						</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							{searchTerm
								? "Try adjusting your search term or clear the filter"
								: canManageForms
									? `Get started by creating your first ${term(terminology, "form", "singularLower")}`
									: `No ${term(terminology, "form", "pluralLower")} are currently available`}
						</p>
						{canManageForms && !searchTerm && (
							<Button
								variant="outline"
								size="icon"
								onClick={handleCreate}
								className="mt-4"
								title={`Create ${term(terminology, "form", "singular")}`}
							>
								<Plus className="h-4 w-4" />
							</Button>
						)}
					</CardContent>
				</Card>
			)}

			{/* Disable/Enable Confirmation Dialog */}
			<AlertDialog
				open={isDisableDialogOpen}
				onOpenChange={setIsDisableDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							{selectedForm?.isActive
								? "Disable Form?"
								: "Enable Form?"}
						</AlertDialogTitle>
						<AlertDialogDescription>
							{selectedForm?.isActive ? (
								<>
									Are you sure you want to disable the form "
									{selectedForm?.name}"? When disabled, users
									will no longer be able to launch this form.
								</>
							) : (
								<>
									Are you sure you want to enable the form "
									{selectedForm?.name}"? When enabled, users
									will be able to launch this form.
								</>
							)}
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmToggleActive}
							className={
								selectedForm?.isActive
									? "bg-destructive text-destructive-foreground hover:bg-destructive/90"
									: ""
							}
						>
							{updateForm.isPending
								? selectedForm?.isActive
									? "Disabling..."
									: "Enabling..."
								: selectedForm?.isActive
									? "Disable Form"
									: "Enable Form"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={isDeleteDialogOpen}
				onOpenChange={setIsDeleteDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Are you sure?</AlertDialogTitle>
						<AlertDialogDescription>
							{selectedForm && !selectedForm.isActive
								? `This will permanently remove the inactive form "${selectedForm.name}" from the database. This action cannot be undone.`
								: `This will deactivate the form "${selectedForm?.name}". Users will no longer be able to access or execute this form.`}
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteForm.isPending
								? "Deleting..."
								: "Delete Form"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
