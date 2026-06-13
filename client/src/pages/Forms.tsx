import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, RefreshCw, LayoutGrid, Table as TableIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	FormListSurface,
	type FormListItem,
	type FormValidationState,
} from "@/components/forms/FormListSurface";
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
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

			<FormListSurface
				forms={filteredForms as FormListItem[]}
				viewMode={viewMode}
				isLoading={isLoading}
				isPlatformAdmin={isPlatformAdmin}
				canManageForms={canManageForms}
				getOrgName={getOrgName}
				formValidation={formValidation as Map<string, FormValidationState>}
				onLaunch={(form) => handleLaunch(form.id)}
				onEdit={(form) => handleEdit(form.id)}
				onDelete={(form) =>
					handleDelete(form.id, form.name, form.is_active)
				}
				onToggleActive={(form) =>
					handleToggleActive(form.id, form.name, form.is_active)
				}
				onCreateEmpty={handleCreate}
				emptySearchActive={Boolean(searchTerm)}
			/>

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
