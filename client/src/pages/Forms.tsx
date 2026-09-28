import { ListLoadError } from "@/components/layout/ListLoadError";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import { useEffect, useState, useMemo, useRef } from "react";
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
import { FormShareDialog } from "@/components/forms/FormShareDialog";
import { term, useTerminology } from "@/lib/terminology";
import type { components } from "@/lib/v1";
import { preloadRunFormPage } from "@/pages/run-form-route";

type FormPublic = components["schemas"]["FormPublic"];
type Organization = components["schemas"]["OrganizationPublic"];

export function Forms() {
	const isDesktop = useIsDesktop();
	const actionBusy = useRef(false);
	const [actionPending, setActionPending] = useState(false);
	const [actionError, setActionError] = useState(false);
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
	const [shareForm, setShareForm] = useState<{
		id: string;
		name: string;
	} | null>(null);
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
		isError,
		isFetching,
		refetch,
	} = useForms(isPlatformAdmin ? filterOrgId : undefined);
	const deleteForm = useDeleteForm({ errorToast: false });
	const updateForm = useUpdateForm({ errorToast: false });

	useEffect(() => {
		// Start the form runner chunk while the list is visible so Launch can
		// navigate immediately instead of waiting for a large first-use import.
		void preloadRunFormPage().catch(() => undefined);
	}, []);

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

	const handleDelete = (
		formId: string,
		formName: string,
		isActive: boolean,
	) => {
		setSelectedForm({ id: formId, name: formName, isActive });
		setActionError(false);
		setIsDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!selectedForm || actionBusy.current) return;
		actionBusy.current = true;
		setActionPending(true);
		setActionError(false);
		try {
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
		} catch {
			setActionError(true);
		} finally {
			actionBusy.current = false;
			setActionPending(false);
		}
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
		setActionError(false);
		setIsDisableDialogOpen(true);
	};

	const handleConfirmToggleActive = async () => {
		if (!selectedForm || actionBusy.current) return;
		actionBusy.current = true;
		setActionPending(true);
		setActionError(false);
		try {
			await updateForm.mutateAsync({
				params: { path: { form_id: selectedForm.id } },
				body: {
					is_active: !selectedForm.isActive,
				},
			});
			setIsDisableDialogOpen(false);
			setSelectedForm(null);
		} catch {
			setActionError(true);
		} finally {
			actionBusy.current = false;
			setActionPending(false);
		}
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
		<PageWorkspace className="max-w-7xl mx-auto">
			<ListPageHeader
				title={term(terminology, "form", "plural")}
				description={
					canManageForms
						? `Launch workflows with guided ${term(terminology, "form", "singularLower")} interfaces`
						: `Launch workflows with guided ${term(terminology, "form", "pluralLower")}`
				}
				actions={
					<>
						{canManageForms && isDesktop && (
							<ToggleGroup
								aria-label="List layout"
								type="single"
								value={viewMode}
								onValueChange={(value: string) =>
									value &&
									setViewMode(value as "grid" | "table")
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
							size="icon-lg"
							onClick={() => refetch()}
							title="Refresh"
							aria-label="Refresh forms"
						>
							<RefreshCw className="h-4 w-4" />
						</Button>
						{canManageForms && (
							<Button
								variant="outline"
								size="icon-lg"
								onClick={handleCreate}
								title={`Create ${term(terminology, "form", "singular")}`}
								aria-label={`Create ${term(terminology, "form", "singular")}`}
							>
								<Plus className="h-4 w-4" />
							</Button>
						)}
					</>
				}
			/>

			{/* Search and Filters */}
			<ListToolbar>
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
			</ListToolbar>

			<PageScrollArea
				aria-label={`${term(terminology, "form", "plural")} list`}
				className={
					isDesktop && viewMode === "table"
						? "space-y-4 lg:flex lg:flex-col lg:overflow-hidden"
						: "space-y-4"
				}
			>
				{isError && (
					<ListLoadError
						resource={term(terminology, "form", "pluralLower")}
						hasCachedData={forms !== undefined}
						isRetrying={isFetching}
						onRetry={() => void refetch()}
					/>
				)}
				{isLoading && (
					<p role="status" className="sr-only">
						Loading {term(terminology, "form", "pluralLower")}…
					</p>
				)}
				{(!isError || forms !== undefined) && (
					<FormListSurface
						forms={filteredForms as FormListItem[]}
						viewMode={isDesktop ? viewMode : "grid"}
						isLoading={isLoading}
						isPlatformAdmin={isPlatformAdmin}
						canManageForms={canManageForms}
						getOrgName={getOrgName}
						formValidation={
							formValidation as Map<string, FormValidationState>
						}
						onLaunch={(form) => handleLaunch(form.id)}
						onShare={(form) =>
							setShareForm({ id: form.id, name: form.name })
						}
						onEdit={(form) => handleEdit(form.id)}
						onDelete={(form) =>
							handleDelete(form.id, form.name, form.is_active)
						}
						onToggleActive={(form) =>
							handleToggleActive(
								form.id,
								form.name,
								form.is_active,
							)
						}
						onCreateEmpty={handleCreate}
						emptySearchActive={Boolean(searchTerm)}
					/>
				)}
			</PageScrollArea>

			{shareForm ? (
				<FormShareDialog
					formId={shareForm.id}
					formName={shareForm.name}
					open
					onOpenChange={(open) => !open && setShareForm(null)}
				/>
			) : null}

			{/* Disable/Enable Confirmation Dialog */}
			<AlertDialog
				open={isDisableDialogOpen}
				onOpenChange={(open) => {
					if (!actionBusy.current) setIsDisableDialogOpen(open);
				}}
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
					{actionError && (
						<p role="alert" className="text-sm text-destructive">
							Couldn't update this form. Please retry.
						</p>
					)}
					{actionPending && (
						<p role="status" className="sr-only">
							Updating form…
						</p>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel disabled={actionPending}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							disabled={actionPending}
							onClick={(event) => {
								event.preventDefault();
								void handleConfirmToggleActive();
							}}
							className={
								selectedForm?.isActive
									? "bg-destructive text-destructive-foreground hover:bg-destructive/90"
									: ""
							}
						>
							{actionPending
								? selectedForm?.isActive
									? "Disabling..."
									: "Enabling..."
								: actionError
									? "Retry update"
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
				onOpenChange={(open) => {
					if (!actionBusy.current) setIsDeleteDialogOpen(open);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							{selectedForm?.isActive
								? "Deactivate form?"
								: "Permanently delete form?"}
						</AlertDialogTitle>
						<AlertDialogDescription>
							{selectedForm && !selectedForm.isActive
								? `This will permanently remove the inactive form "${selectedForm.name}". This action cannot be undone.`
								: `This will deactivate the form "${selectedForm?.name}". Users will no longer be able to access or execute this form.`}
						</AlertDialogDescription>
					</AlertDialogHeader>
					{actionError && (
						<p role="alert" className="text-sm text-destructive">
							Couldn't update this form. Please retry.
						</p>
					)}
					{actionPending && (
						<p role="status" className="sr-only">
							Updating form…
						</p>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel disabled={actionPending}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							disabled={actionPending}
							onClick={(event) => {
								event.preventDefault();
								void handleConfirmDelete();
							}}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{actionPending
								? "Deleting..."
								: actionError
									? "Retry delete"
									: "Delete Form"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</PageWorkspace>
	);
}
