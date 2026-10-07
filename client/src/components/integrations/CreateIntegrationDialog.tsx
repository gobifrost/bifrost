import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { IntegrationReadError } from "./IntegrationReadError";
import { IntegrationSchemaEditor } from "./IntegrationSchemaEditor";
import { Combobox } from "@/components/ui/combobox";
import { Checkbox } from "@/components/ui/checkbox";
import { LogoDropZone } from "@/components/LogoDropZone";
import { bumpEntityLogo } from "@/components/entityLogoVersions";
import { Loader2, Trash2, Plug } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
	useCreateIntegration,
	useUpdateIntegration,
	useIntegration,
	useResetEntityIdSource,
	type ConfigSchemaItem,
	type IntegrationDetail,
} from "@/services/integrations";
import { useDataProviders } from "@/services/dataProviders";

interface CreateIntegrationDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	editIntegrationId?: string;
	/**
	 * If provided, use this data instead of fetching.
	 * This avoids duplicate API calls when the parent already has the data.
	 */
	initialData?: IntegrationDetail;
}

/**
 * Form component that renders once data is available.
 * Gets remounted via key when existingIntegration changes from undefined to loaded.
 */
function CreateIntegrationForm({
	onOpenChange,
	editIntegrationId,
	existingIntegration,
	isSaving,
	setIsSaving,
}: {
	onOpenChange: (open: boolean) => void;
	editIntegrationId?: string;
	existingIntegration?: IntegrationDetail;
	isSaving: boolean;
	setIsSaving: (saving: boolean) => void;
}) {
	const queryClient = useQueryClient();

	// Fetch available data providers
	const {
		data: dataProviders,
		isLoading: isLoadingProviders,
		isError: providersError,
		isFetching: fetchingProviders,
		refetch: refetchProviders,
	} = useDataProviders();

	const createMutation = useCreateIntegration();
	const updateMutation = useUpdateIntegration();

	const isEditing = Boolean(editIntegrationId);
	const isLoading =
		isSaving || createMutation.isPending || updateMutation.isPending;
	const [saveError, setSaveError] = useState<string | null>(null);

	// Initialize state from existing integration (or empty for new)
	// This is safe because the parent only mounts this component once data is ready
	const [name, setName] = useState(existingIntegration?.name || "");
	const [description, setDescription] = useState(
		existingIntegration?.description || "",
	);
	const [dataProviderId, setDataProviderId] = useState<string | null>(
		existingIntegration?.list_entities_data_provider_id || null,
	);
	const [configSchema, setConfigSchema] = useState<ConfigSchemaItem[]>(
		existingIntegration?.config_schema || [],
	);
	const [defaultEntityId, setDefaultEntityId] = useState<string>(
		existingIntegration?.default_entity_id || "",
	);

	// Track original values for confirmation dialogs
	const originalName = existingIntegration?.name || "";
	const originalDataProviderId =
		existingIntegration?.list_entities_data_provider_id || null;

	// Confirmation dialog states
	const [showDataProviderConfirm, setShowDataProviderConfirm] =
		useState(false);
	const [showNameChangeConfirm, setShowNameChangeConfirm] = useState(false);
	// Populated from the server's 409 `integration_schema_removal_requires_confirmation`
	// response — the removed keys and affected-value count are authoritative
	// (computed server-side), not guessed client-side.
	const [schemaRemovalConfirm, setSchemaRemovalConfirm] = useState<{
		removedKeys: string[];
		affectedConfigValues: number;
	} | null>(null);

	// Entity ID source reset state
	const [showResetEntityIdSource, setShowResetEntityIdSource] =
		useState(false);
	const [resetClearMappings, setResetClearMappings] = useState(false);
	const resetEntityIdSource = useResetEntityIdSource();

	const existingEntityIdSource =
		existingIntegration?.oauth_config?.entity_id_source ?? null;
	const entityIdSourceDisplay = existingEntityIdSource
		? `${String(existingEntityIdSource.type ?? "")}:${String(existingEntityIdSource.key ?? "")}`
		: "";

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (isLoading) return;

		if (!name.trim()) {
			toast.error("Integration name is required");
			return;
		}

		await confirmAndSave(0);
	};

	const confirmAndSave = async (stage: number) => {
		// Advance through every applicable confirmation before saving.
		if (isEditing) {
			// Check 1: Name change confirmation
			if (stage < 1 && name !== originalName) {
				setShowNameChangeConfirm(true);
				return;
			}

			// Check 2: Data provider swap confirmation
			if (stage < 2 && dataProviderId !== originalDataProviderId) {
				// Count affected mappings (those with entity_id values)
				const affectedMappingsCount =
					existingIntegration?.mappings?.filter((m) => m.entity_id)
						.length || 0;
				if (affectedMappingsCount > 0) {
					setShowDataProviderConfirm(true);
					return;
				}
			}

			// Config-schema key removal is confirmed server-side (see
			// performSave's 409 handling below) — the server knows which
			// keys actually have saved values, so there's nothing to guess
			// here.
		}

		// Proceed with save
		await performSave(false);
	};

	const performSave = async (forceRemoveKeys: boolean) => {
		if (isLoading) return;
		setIsSaving(true);
		setSaveError(null);
		try {
			if (isEditing && editIntegrationId) {
				await updateMutation.mutateAsync({
					params: {
						path: { integration_id: editIntegrationId },
						query: { force_remove_keys: forceRemoveKeys },
					},
					body: {
						name,
						description: description.trim() || null,
						list_entities_data_provider_id:
							dataProviderId || undefined,
						config_schema: configSchema,
						default_entity_id: defaultEntityId || undefined,
					},
				});
				toast.success("Integration updated successfully");
			} else {
				await createMutation.mutateAsync({
					body: {
						name,
						description: description.trim() || null,
						config_schema:
							configSchema.length > 0 ? configSchema : undefined,
						default_entity_id: defaultEntityId || undefined,
					},
				});
				toast.success("Integration created successfully");
			}

			// Invalidate queries to refresh the list
			queryClient.invalidateQueries({ queryKey: ["integrations"] });
			onOpenChange(false);
		} catch (err) {
			const detail = (
				err as {
					detail?: {
						code?: string;
						removed_keys?: string[];
						affected_config_values?: number;
					};
				}
			)?.detail;
			if (
				detail?.code ===
				"integration_schema_removal_requires_confirmation"
			) {
				setSchemaRemovalConfirm({
					removedKeys: detail.removed_keys ?? [],
					affectedConfigValues: detail.affected_config_values ?? 0,
				});
				return;
			}
			setSaveError(
				isEditing
					? "Could not update integration. Your changes are preserved. Try again."
					: "Could not create integration. Your changes are preserved. Try again.",
			);
		} finally {
			setIsSaving(false);
		}
	};

	const addConfigField = () => {
		setConfigSchema([
			...configSchema,
			{
				key: "",
				type: "string",
				required: false,
			},
		]);
	};

	const removeConfigField = (index: number) => {
		setConfigSchema(configSchema.filter((_, i) => i !== index));
	};

	const updateConfigField = (
		index: number,
		field: Partial<ConfigSchemaItem>,
	) => {
		const updated = [...configSchema];
		updated[index] = { ...updated[index], ...field };
		setConfigSchema(updated);
	};

	// Build data provider options for combobox
	const dataProviderOptions = [
		{ value: "none", label: "None" },
		...((
			dataProviders as Array<{
				id?: string | null;
				name: string;
			}>
		)?.flatMap((provider) =>
			provider.id ? [{ value: provider.id, label: provider.name }] : [],
		) || []),
	];

	return (
		<>
			<form
				onSubmit={handleSubmit}
				className="flex min-h-0 flex-1 flex-col"
			>
				<DialogHeader className="shrink-0">
					<DialogTitle>
						{isEditing ? "Edit Integration" : "Create Integration"}
					</DialogTitle>
					<DialogDescription>
						{isEditing
							? "Update integration settings and configuration schema"
							: "Create a new integration to map organizations to external entities"}
					</DialogDescription>
				</DialogHeader>

				<div className="min-h-0 flex-1 overflow-y-auto py-4">
					<fieldset
						disabled={isLoading}
						className="min-w-0 space-y-4"
					>
						{editIntegrationId && (
							<div className="flex items-center gap-4">
								<LogoDropZone
									uploadUrl={`/api/integrations/${editIntegrationId}/logo`}
									deleteUrl={`/api/integrations/${editIntegrationId}/logo`}
									previewUrl={
										existingIntegration?.logo_url ??
										`/api/integrations/${editIntegrationId}/logo`
									}
									fallback={<Plug className="size-8" />}
									size={80}
									ariaLabel="Upload integration logo"
									onChange={() => {
										bumpEntityLogo(
											"integration",
											editIntegrationId,
										);
										void queryClient.invalidateQueries({
											queryKey: [
												"get",
												"/api/integrations",
											],
										});
										void queryClient.invalidateQueries({
											queryKey: [
												"get",
												"/api/integrations/{integration_id}",
											],
										});
									}}
								/>
								<div>
									<p className="text-sm font-medium">
										Integration logo
									</p>
									<p className="text-sm text-muted-foreground">
										PNG, JPEG, or SVG. Logo changes save
										immediately.
									</p>
								</div>
							</div>
						)}
						{/* Name */}
						<div className="space-y-2">
							<Label htmlFor="name">Integration Name *</Label>
							<Input
								className="min-h-11"
								id="name"
								placeholder="e.g., Microsoft 365, Google Workspace"
								value={name}
								onChange={(e) => setName(e.target.value)}
								required
							/>
						</div>

						{/* Description */}
						<div className="space-y-2">
							<Label htmlFor="description">Description</Label>
							<Input
								className="min-h-11"
								id="description"
								placeholder="Brief description of this integration"
								value={description}
								onChange={(e) => setDescription(e.target.value)}
							/>
						</div>

						{/* Data Provider Selection */}
						<div className="space-y-2">
							<Label htmlFor="dataProvider">
								Entity Data Provider
							</Label>
							<Combobox
								id="dataProvider"
								className="min-h-11 sm:min-h-11"
								disabled={
									isLoading ||
									(providersError && !dataProviders)
								}
								options={dataProviderOptions}
								value={dataProviderId || "none"}
								onValueChange={(value) =>
									setDataProviderId(
										value === "none" ? null : value,
									)
								}
								placeholder={
									dataProviderId
										? "Current provider unavailable"
										: "Select a data provider..."
								}
								searchPlaceholder="Search data providers..."
								emptyText="No data providers found."
								isLoading={isLoadingProviders}
							/>
							{providersError && (
								<IntegrationReadError
									resource="data providers"
									cached={Boolean(dataProviders)}
									pending={fetchingProviders}
									onRetry={() => {
										void refetchProviders();
									}}
								/>
							)}
							<p className="text-xs text-muted-foreground">
								Select a data provider to populate entity
								options for organization mappings
							</p>
						</div>

						{/* Entity ID source (read-only; reset via trash icon) */}
						{isEditing && existingIntegration?.oauth_config && (
							<div className="space-y-2">
								<Label htmlFor="entityIdSource">
									Entity ID source
								</Label>
								<div className="flex gap-2">
									<Input
										className="min-h-11"
										id="entityIdSource"
										readOnly
										value={entityIdSourceDisplay}
										placeholder="Not set — picker will appear on next OAuth connect"
									/>
									<Button
										type="button"
										variant="outline"
										size="icon"
										disabled={!existingEntityIdSource}
										onClick={() => {
											setResetClearMappings(false);
											setShowResetEntityIdSource(true);
										}}
										title="Reset Entity ID source"
									>
										<Trash2 className="h-4 w-4" />
									</Button>
								</div>
								<p className="text-xs text-muted-foreground">
									Where new connections auto-capture the
									entity ID from. Set by picking a field in
									the OAuth callback popup.
								</p>
							</div>
						)}

						{/* Default Entity ID */}
						<div className="space-y-2">
							<Label htmlFor="defaultEntityId">
								Default Entity ID
							</Label>
							<Input
								className="min-h-11"
								id="defaultEntityId"
								placeholder="e.g., common"
								value={defaultEntityId}
								onChange={(e) =>
									setDefaultEntityId(e.target.value)
								}
							/>
							<p className="text-xs text-muted-foreground">
								Default value for entity_id in URL templates
								(used when org mapping doesn't specify one)
							</p>
						</div>

						<IntegrationSchemaEditor
							fields={configSchema}
							onAdd={addConfigField}
							onUpdate={updateConfigField}
							onRemove={removeConfigField}
						/>
					</fieldset>
				</div>
				{saveError && (
					<p
						role="alert"
						className="my-3 shrink-0 text-sm text-destructive"
					>
						{saveError}
					</p>
				)}

				<DialogFooter className="shrink-0 border-t pt-4">
					<Button
						type="button"
						variant="outline"
						className="min-h-11"
						onClick={() => onOpenChange(false)}
						disabled={isLoading}
					>
						Cancel
					</Button>
					<Button
						className="min-h-11"
						type="submit"
						disabled={isLoading}
					>
						{isLoading ? (
							<>
								<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
								{isEditing ? "Updating..." : "Creating..."}
							</>
						) : isEditing ? (
							"Update Integration"
						) : (
							"Create Integration"
						)}
					</Button>
				</DialogFooter>
			</form>

			{/* Data Provider Change Confirmation */}
			<AlertDialog
				open={showDataProviderConfirm}
				onOpenChange={setShowDataProviderConfirm}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Change Data Provider?
						</AlertDialogTitle>
						<AlertDialogDescription className="[overflow-wrap:anywhere]">
							Changing the data provider may orphan{" "}
							{existingIntegration?.mappings?.filter(
								(m) => m.entity_id,
							).length || 0}{" "}
							existing entity mapping(s). The entity IDs will be
							preserved but may not match entities from the new
							provider.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							className="min-h-11"
							onClick={() => {
								setShowDataProviderConfirm(false);
								void confirmAndSave(2);
							}}
						>
							Keep Mappings
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Name Change Confirmation */}
			<AlertDialog
				open={showNameChangeConfirm}
				onOpenChange={setShowNameChangeConfirm}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Rename Integration?</AlertDialogTitle>
						<AlertDialogDescription className="[overflow-wrap:anywhere]">
							Renaming this integration will break any SDK calls
							using the name '{originalName}'. Workflows and
							scripts will need to be updated to use '{name}'.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							className="min-h-11"
							onClick={() => {
								setShowNameChangeConfirm(false);
								void confirmAndSave(1);
							}}
						>
							Rename Anyway
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Config Field Removal Confirmation — driven by the server's 409
			    integration_schema_removal_requires_confirmation response, so
			    the key list and affected-value count are authoritative. */}
			<AlertDialog
				open={schemaRemovalConfirm !== null}
				onOpenChange={(open) => {
					if (!open) setSchemaRemovalConfirm(null);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Remove Configuration Fields?
						</AlertDialogTitle>
						<AlertDialogDescription className="[overflow-wrap:anywhere]">
							Removing{" "}
							{schemaRemovalConfirm?.removedKeys.join(", ")} will
							delete{" "}
							{schemaRemovalConfirm?.affectedConfigValues}{" "}
							saved config{" "}
							{schemaRemovalConfirm?.affectedConfigValues === 1
								? "value"
								: "values"}{" "}
							(defaults and per-organization overrides). This
							cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={() => {
								setSchemaRemovalConfirm(null);
								void performSave(true);
							}}
							className="min-h-11 bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete Fields
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			<AlertDialog
				open={showResetEntityIdSource}
				onOpenChange={setShowResetEntityIdSource}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Reset Entity ID source?
						</AlertDialogTitle>
						<AlertDialogDescription asChild>
							<div className="space-y-3">
								<p>
									Clears the configured source on this
									integration's OAuth provider. The picker
									will reappear on the next connect.
								</p>
								<label className="flex items-start gap-2 text-sm">
									<Checkbox
										checked={resetClearMappings}
										onCheckedChange={(c) =>
											setResetClearMappings(c === true)
										}
									/>
									<span>
										Also clear captured Entity IDs on
										existing mappings for this integration.
									</span>
								</label>
							</div>
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel
							disabled={resetEntityIdSource.isPending}
						>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							disabled={resetEntityIdSource.isPending}
							onClick={(e) => {
								e.preventDefault();
								if (!editIntegrationId) return;
								resetEntityIdSource.mutate(
									{
										params: {
											path: {
												integration_id:
													editIntegrationId,
											},
											query: {
												clear_mappings:
													resetClearMappings,
											},
										},
									},
									{
										onSuccess: () => {
											toast.success(
												"Entity ID source cleared",
											);
											setShowResetEntityIdSource(false);
										},
										onError: () => {
											toast.error(
												"Failed to clear Entity ID source",
											);
										},
									},
								);
							}}
							className="min-h-11 bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{resetEntityIdSource.isPending
								? "Clearing…"
								: "Reset"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}

// Inner component that handles data fetching and renders form when ready
function CreateIntegrationDialogContent({
	onOpenChange,
	editIntegrationId,
	initialData,
	isSaving,
	setIsSaving,
}: Omit<CreateIntegrationDialogProps, "open"> & {
	isSaving: boolean;
	setIsSaving: (saving: boolean) => void;
}) {
	// Only fetch if we don't have initialData and we're editing
	const {
		data: fetchedIntegration,
		isLoading,
		isFetching,
		refetch,
	} = useIntegration(initialData ? "" : editIntegrationId || "");

	// Use initialData if provided, otherwise use fetched data
	const existingIntegration = initialData || fetchedIntegration;

	const isEditing = Boolean(editIntegrationId);
	const needsFetch = isEditing && !initialData;

	// Show loading skeleton while fetching existing integration data
	if (needsFetch && isLoading) {
		return (
			<>
				<DialogHeader>
					<DialogTitle>Edit Integration</DialogTitle>
					<DialogDescription>
						Loading integration details...
					</DialogDescription>
				</DialogHeader>
				<div className="space-y-4 py-4">
					<Skeleton className="h-10 w-full" />
					<Skeleton className="h-10 w-full" />
					<Skeleton className="h-10 w-full" />
					<Skeleton className="h-10 w-full" />
				</div>
			</>
		);
	}

	if (needsFetch && !existingIntegration) {
		return (
			<>
				<DialogHeader>
					<DialogTitle>Edit Integration</DialogTitle>
					<DialogDescription>
						Load the integration settings before editing.
					</DialogDescription>
				</DialogHeader>
				<IntegrationReadError
					resource="integration"
					cached={false}
					pending={isFetching}
					onRetry={() => {
						void refetch();
					}}
				/>
			</>
		);
	}

	// Keep the draft mounted across background refreshes; reopening resets it.
	const formKey = existingIntegration?.id || "new";

	return (
		<CreateIntegrationForm
			key={formKey}
			isSaving={isSaving}
			setIsSaving={setIsSaving}
			onOpenChange={onOpenChange}
			editIntegrationId={editIntegrationId}
			existingIntegration={existingIntegration}
		/>
	);
}

// Outer component that uses key to remount content when dialog opens or integration changes
export function CreateIntegrationDialog({
	open,
	onOpenChange,
	editIntegrationId,
	initialData,
}: CreateIntegrationDialogProps) {
	// Create a stable key that changes when dialog opens or when editing a different integration
	// This forces a remount of the inner component, resetting all form state
	const [isSaving, setIsSaving] = useState(false);
	const dialogKey = open ? `open-${editIntegrationId || "new"}` : "closed";

	return (
		<Dialog
			open={open}
			onOpenChange={(nextOpen) => {
				if (!isSaving) onOpenChange(nextOpen);
			}}
		>
			<DialogContent className="flex max-w-2xl max-h-[90dvh] flex-col overflow-hidden">
				{open && (
					<CreateIntegrationDialogContent
						key={dialogKey}
						isSaving={isSaving}
						setIsSaving={setIsSaving}
						onOpenChange={onOpenChange}
						editIntegrationId={editIntegrationId}
						initialData={initialData}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
