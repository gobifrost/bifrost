import {
	useEffect,
	useMemo,
	useRef,
	useState,
	type MouseEvent,
	type RefObject,
} from "react";
import {
	Building2,
	FileText,
	MoreVertical,
	Pencil,
	Plus,
	Power,
	RefreshCw,
	Settings,
} from "lucide-react";

import { SearchBox } from "@/components/search/SearchBox";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableFooter,
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
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { getErrorMessage } from "@/lib/api-error";
import { GLOBAL_TARGET } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";
import {
	useCreateOrganization,
	useOrganizations,
	useUpdateOrganization,
} from "@/hooks/useOrganizations";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useSearch } from "@/hooks/useSearch";
import type { components } from "@/lib/v1";
import { RequiredInstructionsSettings } from "@/pages/settings/RequiredInstructionsSettings";
import { ListPagination } from "@/components/pagination/ListPagination";

type Organization = components["schemas"]["OrganizationPublic"];
type EditTab = "general" | "instructions";
const PAGE_SIZE = 25;

interface OrganizationFormData {
	name: string;
	domain: string;
	isActive: boolean;
}

interface MutationRetryAlertProps {
	alertRef: RefObject<HTMLDivElement | null>;
	buttonLabel: string;
	description: string;
	formId: string;
	title: string;
}

const EMPTY_FORM: OrganizationFormData = {
	name: "",
	domain: "",
	isActive: true,
};

function MutationRetryAlert({
	alertRef,
	buttonLabel,
	description,
	formId,
	title,
}: MutationRetryAlertProps) {
	return (
		<Alert
			variant="destructive"
			className="mt-4 space-y-3 outline-none"
			aria-live="polite"
			tabIndex={-1}
			ref={alertRef}
		>
			<AlertTitle>{title}</AlertTitle>
			<AlertDescription className="space-y-3">
				<p>{description}</p>
				<Button
					type="submit"
					form={formId}
					variant="outline"
					className="min-h-11 w-full sm:w-auto"
				>
					{buttonLabel}
				</Button>
			</AlertDescription>
		</Alert>
	);
}

export function Organizations() {
	const compactLayout = useMediaQuery("(max-width: 1023px)");
	const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
	const [isEditDialogOpen, setIsEditDialogOpen] = useState(false);
	const [isDisableDialogOpen, setIsDisableDialogOpen] = useState(false);
	const [selectedOrg, setSelectedOrg] = useState<Organization>();
	const [activeEditTab, setActiveEditTab] = useState<EditTab>("general");
	const [formData, setFormData] = useState<OrganizationFormData>(EMPTY_FORM);
	const [searchTerm, setSearchTerm] = useState("");
	const [showInactive, setShowInactive] = useState(false);
	const [offset, setOffset] = useState(0);

	const { data, isLoading, isFetching, error, refetch } = useOrganizations({
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
	const clampedOffset =
		offset > 0 && offset >= filteredOrgs.length
			? Math.max(
					0,
					Math.floor((filteredOrgs.length - 1) / PAGE_SIZE) *
						PAGE_SIZE,
				)
			: offset;
	const pagedOrgs = useMemo(
		() => filteredOrgs.slice(clampedOffset, clampedOffset + PAGE_SIZE),
		[filteredOrgs, clampedOffset],
	);

	const createMutation = useCreateOrganization({ toastOnError: false });
	const authorization = useAuthorization();
	// Creating is platform-level; editing and disabling are per organization.
	const canCreate = authorization.canAt(
		"organizations.readwrite",
		GLOBAL_TARGET,
	);
	const canManage = (org: Organization) =>
		authorization.canAt("organizations.readwrite", {
			kind: "org",
			id: org.id,
		});
	const showActions = pagedOrgs.some(canManage);
	const updateMutation = useUpdateOrganization({ toastOnError: false });

	const resetSelection = () => {
		setSelectedOrg(undefined);
		setFormData(EMPTY_FORM);
		setActiveEditTab("general");
	};

	const createTriggerRef = useRef<HTMLButtonElement | null>(null);
	const editTriggerRef = useRef<HTMLButtonElement | null>(null);
	const createSubmitBusyRef = useRef(false);
	const updateSubmitBusyRef = useRef(false);
	const pendingFocusOrgIdRef = useRef<string | null>(null);
	const pendingFocusTargetRef = useRef<"actions" | "name" | null>(null);
	const handleCreate = (event: MouseEvent<HTMLButtonElement>) => {
		createTriggerRef.current = event.currentTarget;
		setFormData(EMPTY_FORM);
		setIsCreateDialogOpen(true);
	};

	const handleEdit = (
		org: Organization,
		event?: MouseEvent<HTMLElement>,
		tab: EditTab = "general",
	) => {
		if (event?.currentTarget instanceof HTMLButtonElement) {
			editTriggerRef.current = event.currentTarget;
		} else if (event?.currentTarget instanceof HTMLElement) {
			const rowActionTrigger = event.currentTarget
				.closest("tr")
				?.querySelector<HTMLButtonElement>(
					'button[aria-label$="actions"]',
				);
			if (rowActionTrigger) {
				editTriggerRef.current = rowActionTrigger;
			}
		}
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
		if (createSubmitBusyRef.current) return;
		createSubmitBusyRef.current = true;
		try {
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
			createMutation.reset?.();
		} catch {
			// The mutation hook already reports the failure.
		} finally {
			createSubmitBusyRef.current = false;
		}
	};

	const handleSubmitEdit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (!selectedOrg) return;
		if (updateSubmitBusyRef.current) return;
		updateSubmitBusyRef.current = true;

		try {
			await updateMutation.mutateAsync({
				params: { path: { org_id: selectedOrg.id } },
				body: {
					name: formData.name.trim(),
					domain: formData.domain.trim(),
					is_active: formData.isActive,
				},
			});
			pendingFocusOrgIdRef.current = selectedOrg.id;
			pendingFocusTargetRef.current = editTriggerRef.current?.matches(
				'[aria-label$="actions"]',
			)
				? "actions"
				: "name";
			setIsEditDialogOpen(false);
			resetSelection();
			updateMutation.reset?.();
		} catch {
			// The mutation hook already reports the failure.
		} finally {
			updateSubmitBusyRef.current = false;
		}
	};

	const setOrganizationActive = async (
		org: Organization,
		isActive: boolean,
	) => {
		try {
			await updateMutation.mutateAsync({
				params: { path: { org_id: org.id } },
				body: { is_active: isActive },
			});
			updateMutation.reset?.();
			return true;
		} catch {
			return false;
		}
	};

	const handleToggleActive = async (org: Organization) => {
		if (org.is_provider || updateMutation.isPending) return;
		updateMutation.reset?.();
		if (org.is_active) {
			setSelectedOrg(org);
			setIsDisableDialogOpen(true);
			return;
		}
		await setOrganizationActive(org, true);
	};

	const handleConfirmDisable = async () => {
		if (!selectedOrg || updateMutation.isPending) return;
		if (!(await setOrganizationActive(selectedOrg, false))) return;
		setIsDisableDialogOpen(false);
		resetSelection();
	};

	const handleCreateDialogChange = (open: boolean) => {
		if (createMutation.isPending) return;
		setIsCreateDialogOpen(open);
		if (!open) {
			setFormData(EMPTY_FORM);
			createMutation.reset?.();
		}
	};

	const handleEditDialogChange = (open: boolean) => {
		if (updateMutation.isPending) return;
		setIsEditDialogOpen(open);
		if (!open) {
			resetSelection();
			updateMutation.reset?.();
		}
	};

	const createErrorMessage = createMutation.error
		? getErrorMessage(createMutation.error, "Failed to create organization")
		: "";
	const updateErrorMessage = updateMutation.error
		? getErrorMessage(updateMutation.error, "Failed to update organization")
		: "";
	const createErrorRef = useRef<HTMLDivElement | null>(null);
	const updateErrorRef = useRef<HTMLDivElement | null>(null);

	useEffect(() => {
		if (!isCreateDialogOpen || !createErrorMessage) return;
		createErrorRef.current?.scrollIntoView({ block: "center" });
		createErrorRef.current?.focus({ preventScroll: true });
	}, [createErrorMessage, isCreateDialogOpen]);

	useEffect(() => {
		if (!isEditDialogOpen || !updateErrorMessage) return;
		updateErrorRef.current?.scrollIntoView({ block: "center" });
		updateErrorRef.current?.focus({ preventScroll: true });
	}, [isEditDialogOpen, updateErrorMessage]);

	useEffect(() => {
		if (isEditDialogOpen || !pendingFocusOrgIdRef.current) return;
		const orgId = pendingFocusOrgIdRef.current;
		const target = pendingFocusTargetRef.current;
		pendingFocusOrgIdRef.current = null;
		pendingFocusTargetRef.current = null;

		const focusElement = () => {
			const row = document.querySelector<HTMLElement>(
				`[data-org-id="${orgId}"]`,
			);
			const preferredButton =
				target === "actions"
					? row?.querySelector<HTMLButtonElement>(
							'button[aria-label$="actions"]',
						)
					: row?.querySelector<HTMLButtonElement>(
							'button[aria-label^="Edit "]',
						);
			const fallbackButton = row?.querySelector<HTMLButtonElement>(
				'button[aria-label$="actions"], button[aria-label^="Edit "]',
			);
			const focusTarget = preferredButton ?? fallbackButton ?? null;
			if (focusTarget) {
				focusTarget.focus();
				return true;
			}
			const pageFallback =
				createTriggerRef.current ??
				document.querySelector<HTMLButtonElement>(
					'button[aria-label="Refresh organizations"]',
				);
			pageFallback?.focus();
			return Boolean(pageFallback);
		};

		const frame = window.requestAnimationFrame(focusElement);
		return () => window.cancelAnimationFrame(frame);
	}, [isEditDialogOpen, data, compactLayout]);

	const renderOrganizationActions = (org: Organization) =>
		canManage(org) && (
			<DropdownMenu>
				<DropdownMenuTrigger asChild>
					<Button
						variant="ghost"
						size="icon"
						className="h-11 w-11 lg:h-9 lg:w-9"
						aria-label={`${org.name} actions`}
						onClick={(event) => {
							editTriggerRef.current = event.currentTarget;
						}}
					>
						<MoreVertical className="h-4 w-4" />
					</Button>
				</DropdownMenuTrigger>
				<DropdownMenuContent align="end" className="w-48">
					<DropdownMenuItem
						className="min-h-11 whitespace-nowrap px-3 lg:min-h-9"
						onClick={() => handleEdit(org)}
						aria-label={`Edit ${org.name}`}
					>
						<Pencil />
						Edit
					</DropdownMenuItem>
					<DropdownMenuSeparator />
					<DropdownMenuItem
						className="min-h-11 whitespace-nowrap px-3 lg:min-h-9"
						onClick={() => handleToggleActive(org)}
						disabled={org.is_provider}
						aria-label={`${org.is_active ? "Disable" : "Enable"} ${org.name}`}
					>
						<Power />
						{org.is_active ? "Disable" : "Enable"}
					</DropdownMenuItem>
				</DropdownMenuContent>
			</DropdownMenu>
		);

	const renderName = (
		org: Organization,
		className: string,
		stopPropagation: boolean,
	) =>
		canManage(org) ? (
			<button
				type="button"
				data-org-id={org.id}
				onClick={(event) => {
					if (stopPropagation) event.stopPropagation();
					handleEdit(org, event);
				}}
				aria-label={`Edit ${org.name}`}
				className={className}
			>
				{org.name}
			</button>
		) : (
			<span className="[overflow-wrap:anywhere]">{org.name}</span>
		);

	return (
		<PageWorkspace className="mx-auto max-w-7xl">
			<ListPageHeader
				title="Organizations"
				description={
					authorization.canAnywhere("organizations.readwrite")
						? "Manage customer organizations and their configurations"
						: "Organizations you can view"
				}
				actions={
					<>
						<Button
							variant="outline"
							size="icon"
							onClick={() => refetch()}
							disabled={isFetching}
							aria-label="Refresh organizations"
							title="Refresh organizations"
							className="h-11 w-11 lg:h-10 lg:w-10"
						>
							<RefreshCw
								className={`h-4 w-4 ${isFetching ? "animate-spin motion-reduce:animate-none" : ""}`}
							/>
						</Button>
						{canCreate && (
							<Button
								onClick={handleCreate}
								className="min-h-11 flex-1 sm:flex-none lg:min-h-10"
							>
								<Plus className="h-4 w-4" />
								New Organization
							</Button>
						)}
					</>
				}
			/>

			{updateErrorMessage &&
				!isEditDialogOpen &&
				!isDisableDialogOpen && (
					<Alert variant="destructive">
						<AlertDescription>
							Could not update organization status.{" "}
							{updateErrorMessage} Try the action again.
						</AlertDescription>
					</Alert>
				)}

			<ListToolbar>
				<SearchBox
					value={searchTerm}
					onChange={(value) => {
						setSearchTerm(value);
						setOffset(0);
					}}
					placeholder="Search by name, domain, or ID..."
					aria-label="Search organizations"
					className="w-full sm:flex-1"
				/>
				<div className="flex items-center gap-2 sm:ml-auto">
					<Switch
						id="show-inactive-organizations"
						checked={showInactive}
						onCheckedChange={(checked) => {
							setShowInactive(checked);
							setOffset(0);
						}}
					/>
					<Label
						htmlFor="show-inactive-organizations"
						className="flex min-h-11 cursor-pointer items-center text-sm text-muted-foreground"
					>
						Show Inactive
					</Label>
				</div>
			</ListToolbar>

			{error && (
				<Alert variant="destructive">
					<AlertTitle>Organizations could not be loaded</AlertTitle>
					<AlertDescription className="space-y-3">
						<p>
							{data
								? "Showing the last loaded organizations. Refresh to get the latest changes."
								: "Try again to load your organizations. Your filters are preserved."}
						</p>
						<Button
							variant="outline"
							className="min-h-11"
							disabled={isFetching}
							onClick={() => refetch()}
						>
							{isFetching ? "Retrying…" : "Retry organizations"}
						</Button>
					</AlertDescription>
				</Alert>
			)}
			<PageScrollArea
				aria-label="Organizations list"
				className="lg:flex lg:flex-col lg:overflow-hidden"
			>
				{isLoading ? (
					<div
						role="status"
						aria-label="Loading organizations"
						className="space-y-2"
					>
						<span className="sr-only">Loading organizations…</span>
						{[...Array(5)].map((_, index) => (
							<Skeleton key={index} className="h-12 w-full" />
						))}
					</div>
				) : error && !data ? null : filteredOrgs.length > 0 ? (
					compactLayout ? (
						<div className="rounded-[var(--bf-radius-surface)] border bg-card">
							<ul aria-label="Organizations" className="divide-y">
								{pagedOrgs.map((org) => (
									<li
										key={org.id}
										className="space-y-3 p-4"
										data-org-id={org.id}
									>
										<div className="flex items-start gap-3">
											<div className="min-w-0 flex-1">
												<h2 className="text-base font-semibold">
													{renderName(
														org,
														"min-h-11 w-full rounded-[var(--bf-radius-control)] text-left [overflow-wrap:anywhere] transition-colors duration-[var(--bf-motion-feedback)] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
														false,
													)}
												</h2>
												<div className="mt-1 flex flex-wrap gap-2">
													{org.is_provider && (
														<Badge
															variant="outline"
															className="border-[var(--bf-info)]/20 bg-[var(--bf-info)]/10 text-[var(--bf-info)] dark:bg-[var(--bf-info)]/15"
														>
															Provider
														</Badge>
													)}
													<Badge
														variant="outline"
														className={
															org.is_active
																? "border-[color:var(--bf-success)]/30 text-[color:var(--bf-success)]"
																: "text-muted-foreground"
														}
													>
														{org.is_active
															? "Active"
															: "Inactive"}
													</Badge>
												</div>
											</div>
											{renderOrganizationActions(org)}
										</div>
										<dl className="grid gap-3 text-sm">
											<div>
												<dt className="text-xs text-muted-foreground">
													Email domain
												</dt>
												<dd className="mt-1 [overflow-wrap:anywhere]">
													{org.domain || "Not set"}
												</dd>
											</div>
											<div>
												<dt className="text-xs text-muted-foreground">
													Organization ID
												</dt>
												<dd className="mt-1 font-mono text-xs [overflow-wrap:anywhere]">
													{org.id}
												</dd>
											</div>
											<div>
												<dt className="text-xs text-muted-foreground">
													Created
												</dt>
												<dd className="mt-1">
													{org.created_at
														? new Date(
																org.created_at,
															).toLocaleDateString()
														: "Not available"}
												</dd>
											</div>
										</dl>
									</li>
								))}
							</ul>
							<ListPagination
								offset={clampedOffset}
								limit={PAGE_SIZE}
								total={filteredOrgs.length}
								isFetching={isFetching}
								onPageChange={setOffset}
							/>
						</div>
					) : (
						<DataTable className="max-h-full">
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
									{showActions && (
										<DataTableHead className="w-0 whitespace-nowrap text-right">
											Actions
										</DataTableHead>
									)}
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{pagedOrgs.map((org) => (
									<DataTableRow
										key={org.id}
										clickable={canManage(org)}
										onClick={(event) =>
											canManage(org) &&
											handleEdit(org, event)
										}
										className="group/row"
										data-org-id={org.id}
									>
										<DataTableCell>
											<div className="flex items-start gap-2">
												<div className="min-w-0">
													<div className="flex flex-wrap items-center gap-2">
														{renderName(
															org,
															"min-h-11 text-left font-medium [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
															true,
														)}
														{org.is_provider && (
															<Badge
																variant="outline"
																className="border-[var(--bf-info)]/20 bg-[var(--bf-info)]/10 text-[var(--bf-info)] dark:bg-[var(--bf-info)]/15"
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
											<Badge
												variant={
													org.is_active
														? "outline"
														: "secondary"
												}
												className={
													org.is_active
														? "border-[color:var(--bf-success)]/30 bg-[color:var(--bf-success)]/10 text-[color:var(--bf-success)] dark:bg-[color:var(--bf-success)]/15"
														: "text-muted-foreground"
												}
											>
												{org.is_active
													? "Active"
													: "Inactive"}
											</Badge>
										</DataTableCell>
										<DataTableCell className="hidden w-0 whitespace-nowrap text-sm text-muted-foreground md:table-cell">
											{org.created_at
												? new Date(
														org.created_at,
													).toLocaleDateString()
												: "N/A"}
										</DataTableCell>
										{showActions && (
											<DataTableCell
												className="w-0 whitespace-nowrap text-right"
												onClick={(event) =>
													event.stopPropagation()
												}
											>
												{renderOrganizationActions(org)}
											</DataTableCell>
										)}
									</DataTableRow>
								))}
							</DataTableBody>
							<DataTableFooter>
								<DataTableRow>
									<DataTableCell
										colSpan={showActions ? 5 : 4}
										className="p-0"
									>
										<ListPagination
											offset={clampedOffset}
											limit={PAGE_SIZE}
											total={filteredOrgs.length}
											isFetching={isFetching}
											onPageChange={setOffset}
										/>
									</DataTableCell>
								</DataTableRow>
							</DataTableFooter>
						</DataTable>
					)
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
								: !canCreate
									? "Organizations you have access to appear here."
									: showInactive
										? "Create your first organization to get started."
										: "Show inactive organizations or create a new one."}
						</p>
						{canCreate && (
							<Button onClick={handleCreate} className="mt-4">
								<Plus className="h-4 w-4" />
								New Organization
							</Button>
						)}
					</div>
				)}
			</PageScrollArea>

			<Dialog
				open={isCreateDialogOpen}
				onOpenChange={handleCreateDialogChange}
			>
				<DialogContent
					className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-2xl"
					aria-busy={createMutation.isPending}
					inert={createMutation.isPending}
					onEscapeKeyDown={(event) => {
						if (createMutation.isPending) event.preventDefault();
					}}
					onPointerDownOutside={(event) => {
						if (createMutation.isPending) event.preventDefault();
					}}
					onCloseAutoFocus={(event) => {
						event.preventDefault();
						createTriggerRef.current?.focus();
					}}
				>
					<DialogHeader className="shrink-0">
						<DialogTitle>Create Organization</DialogTitle>
						<DialogDescription>
							Add a customer organization to the platform.
						</DialogDescription>
					</DialogHeader>
					<form
						id="create-organization-form"
						onSubmit={handleSubmitCreate}
						className="min-h-0 flex-1 overflow-y-auto py-4"
						aria-busy={createMutation.isPending}
						inert={createMutation.isPending}
					>
						<fieldset
							disabled={createMutation.isPending}
							className="space-y-4"
						>
							<div className="space-y-2">
								<Label htmlFor="organization-name">
									Organization Name
								</Label>
								<Input
									id="organization-name"
									value={formData.name}
									onChange={(event) =>
										setFormData({
											...formData,
											name: event.target.value,
										})
									}
									placeholder="Acme Corporation"
									required
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="organization-domain">
									Email Domain
								</Label>
								<Input
									id="organization-domain"
									value={formData.domain}
									onChange={(event) =>
										setFormData({
											...formData,
											domain: event.target.value,
										})
									}
									placeholder="acme.com"
								/>
								<p className="text-xs text-muted-foreground">
									Users with this email domain are
									automatically provisioned to this
									organization.
								</p>
							</div>
						</fieldset>
						{createErrorMessage && (
							<MutationRetryAlert
								alertRef={createErrorRef}
								buttonLabel="Retry create"
								description={createErrorMessage}
								formId="create-organization-form"
								title="Failed to create organization"
							/>
						)}
					</form>
					<DialogFooter className="shrink-0 border-t border-border/60 pt-4">
						<Button
							type="button"
							variant="outline"
							onClick={() => handleCreateDialogChange(false)}
							disabled={createMutation.isPending}
							className="min-h-11"
						>
							Cancel
						</Button>
						<Button
							type="submit"
							form="create-organization-form"
							disabled={createMutation.isPending}
							className="min-h-11"
						>
							{createMutation.isPending
								? "Creating..."
								: "Create Organization"}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog
				open={isEditDialogOpen}
				onOpenChange={handleEditDialogChange}
			>
				<DialogContent
					className="flex max-h-[85vh] flex-col overflow-hidden sm:max-w-3xl"
					aria-busy={updateMutation.isPending}
					inert={updateMutation.isPending}
					onEscapeKeyDown={(event) => {
						if (updateMutation.isPending) event.preventDefault();
					}}
					onPointerDownOutside={(event) => {
						if (updateMutation.isPending) event.preventDefault();
					}}
					onCloseAutoFocus={(event) => {
						event.preventDefault();
						editTriggerRef.current?.focus();
					}}
				>
					<DialogHeader className="shrink-0">
						<DialogTitle>Edit Organization</DialogTitle>
						<DialogDescription>
							Manage details, status, and instructions for{" "}
							{selectedOrg?.name}.
						</DialogDescription>
					</DialogHeader>

					<Tabs
						value={activeEditTab}
						onValueChange={(value) => {
							if (updateMutation.isPending) return;
							setActiveEditTab(value as EditTab);
						}}
						className="min-h-0 flex-1 overflow-hidden"
						aria-busy={updateMutation.isPending}
						inert={updateMutation.isPending}
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
									aria-busy={updateMutation.isPending}
									inert={updateMutation.isPending}
								>
									<div className="space-y-2">
										<Label htmlFor="edit-organization-name">
											Organization Name
										</Label>
										<Input
											id="edit-organization-name"
											value={formData.name}
											onChange={(event) =>
												setFormData({
													...formData,
													name: event.target.value,
												})
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
												setFormData({
													...formData,
													domain: event.target.value,
												})
											}
											placeholder="acme.com"
										/>
										<p className="text-xs text-muted-foreground">
											Users with this email domain are
											automatically provisioned to this
											organization.
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
												setFormData({
													...formData,
													isActive,
												})
											}
											disabled={selectedOrg?.is_provider}
										/>
									</div>
									{updateErrorMessage && (
										<MutationRetryAlert
											alertRef={updateErrorRef}
											buttonLabel="Retry save"
											description={updateErrorMessage}
											formId="edit-organization-form"
											title="Failed to update organization"
										/>
									)}
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
						<DialogFooter className="shrink-0 border-t border-border/60 pt-4">
							<Button
								type="button"
								variant="outline"
								onClick={() => handleEditDialogChange(false)}
								disabled={updateMutation.isPending}
							>
								Cancel
							</Button>
							<Button
								type="submit"
								form="edit-organization-form"
								disabled={updateMutation.isPending}
							>
								{updateMutation.isPending
									? "Saving..."
									: "Save Changes"}
							</Button>
						</DialogFooter>
					)}
				</DialogContent>
			</Dialog>

			<AlertDialog
				open={isDisableDialogOpen}
				onOpenChange={(open) => {
					if (updateMutation.isPending) return;
					setIsDisableDialogOpen(open);
					if (!open) resetSelection();
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Disable organization?
						</AlertDialogTitle>
						<AlertDialogDescription>
							{selectedOrg?.name} will be removed from active
							organization lists. You can re-enable it later by
							showing inactive organizations.
						</AlertDialogDescription>
					</AlertDialogHeader>
					{updateErrorMessage && (
						<Alert variant="destructive">
							<AlertDescription>
								{updateErrorMessage} Try again.
							</AlertDescription>
						</Alert>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel
							className="min-h-11"
							disabled={updateMutation.isPending}
						>
							Cancel
						</AlertDialogCancel>
						<Button
							variant="destructive"
							disabled={updateMutation.isPending}
							onClick={handleConfirmDisable}
							className="min-h-11"
						>
							{updateMutation.isPending
								? "Disabling..."
								: "Disable"}
						</Button>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</PageWorkspace>
	);
}
