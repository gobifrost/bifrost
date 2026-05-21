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
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Combobox } from "@/components/ui/combobox";
import { Checkbox } from "@/components/ui/checkbox";
import { Loader2, Plus, Trash2 } from "lucide-react";
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
	initialData,
}: {
	onOpenChange: (open: boolean) => void;
	editIntegrationId?: string;
	existingIntegration?: IntegrationDetail;
	initialData?: IntegrationDetail;
}) {
	const queryClient = useQueryClient();

	// Fetch available data providers
	const { data: dataProviders, isLoading: isLoadingProviders } =
		useDataProviders();

	const createMutation = useCreateIntegration();
	const updateMutation = useUpdateIntegration();

	const isEditing = Boolean(editIntegrationId);
	const isLoading = createMutation.isPending || updateMutation.isPending;

	// Initialize state from existing integration (or empty for new)
	// This is safe because the parent only mounts this component once data is ready
	const [name, setName] = useState(existingIntegration?.name || "");
	const [description, setDescription] = useState(() => {
		const integrationWithDesc =
			existingIntegration as typeof existingIntegration & {
				description?: string;
			};
		return integrationWithDesc?.description || "";
	});
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
	const originalConfigSchemaKeys = new Set(
		existingIntegration?.config_schema?.map((f) => f.key) || [],
	);

	// Confirmation dialog states
	const [showDataProviderConfirm, setShowDataProviderConfirm] =
		useState(false);
	const [showNameChangeConfirm, setShowNameChangeConfirm] = useState(false);
	const [showConfigFieldRemovalConfirm, setShowConfigFieldRemovalConfirm] =
		useState(false);
	const [removedFieldNames, setRemovedFieldNames] = useState<string[]>([]);

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

		if (!name.trim()) {
			toast.error("Integration name is required");
			return;
		}

		// In edit mode, check for confirmations needed
		if (isEditing) {
			// Check 1: Name change confirmation
			if (name !== originalName) {
				setShowNameChangeConfirm(true);
				return;
			}

			// Check 2: Data provider swap confirmation
			if (dataProviderId !== originalDataProviderId) {
				// Count affected mappings (those with entity_id values)
				const affectedMappingsCount =
					initialData?.mappings?.filter((m) => m.entity_id).length ||
					0;
				if (affectedMappingsCount > 0) {
					setShowDataProviderConfirm(true);
					return;
				}
			}

			// Check 3: Config field removal warning
			const currentKeys = new Set(
				configSchema.map((f) => f.key).filter((k) => k.trim()),
			);
			const removedKeys = Array.from(originalConfigSchemaKeys).filter(
				(k) => !currentKeys.has(k),
			);
			if (removedKeys.length > 0) {
				setRemovedFieldNames(removedKeys);
				setShowConfigFieldRemovalConfirm(true);
				return;
			}
		}

		// Proceed with save
		await performSave();
	};

	const performSave = async () => {
		try {
			if (isEditing && editIntegrationId) {
				await updateMutation.mutateAsync({
					params: { path: { integration_id: editIntegrationId } },
					body: {
						name,
						list_entities_data_provider_id:
							dataProviderId || undefined,
						config_schema:
							configSchema.length > 0 ? configSchema : undefined,
						default_entity_id: defaultEntityId || undefined,
					},
				});
				toast.success("Integration updated successfully");
			} else {
				await createMutation.mutateAsync({
					body: {
						name,
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
		} catch (error: unknown) {
			console.error("Failed to save integration:", error);
			toast.error(
				isEditing
					? "Failed to update integration"
					: "Failed to create integration",
			);
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
			provider.id
				? [{ value: provider.id, label: provider.name }]
				: [],
		) || []),
	];

	return (
		<>
			<form onSubmit={handleSubmit}>
				<DialogHeader>
					<DialogTitle>
						{isEditing ? "Edit Integration" : "Create Integration"}
					</DialogTitle>
					<DialogDescription>
						{isEditing
							? "Update integration settings and configuration schema"
							: "Create a new integration to map organizations to external entities"}
					</DialogDescription>
				</DialogHeader>

				<div className="space-y-4 py-4">
					{/* Name */}
					<div className="space-y-2">
						<Label htmlFor="name">Integration Name *</Label>
						<Input
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
							options={dataProviderOptions}
							value={dataProviderId || "none"}
							onValueChange={(value) =>
								setDataProviderId(
									value === "none" || value === ""
										? null
										: value,
								)
							}
							placeholder="Select a data provider..."
							searchPlaceholder="Search data providers..."
							emptyText="No data providers found."
							isLoading={isLoadingProviders}
						/>
						<p className="text-xs text-muted-foreground">
							Select a data provider to populate entity options
							for organization mappings
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
									id="entityIdSource"
									readOnly
									value={entityIdSourceDisplay}
									placeholder="Not set — picker will appear on next OAuth connect"
									className="font-mono text-xs"
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
								Where new connections auto-capture the entity
								ID from. Set by picking a field in the OAuth
								callback popup.
							</p>
						</div>
					)}

					{/* Default Entity ID */}
					<div className="space-y-2">
						<Label htmlFor="defaultEntityId">
							Default Entity ID
						</Label>
						<Input
							id="defaultEntityId"
							placeholder="e.g., common"
							value={defaultEntityId}
							onChange={(e) => setDefaultEntityId(e.target.value)}
						/>
						<p className="text-xs text-muted-foreground">
							Default value for entity_id in URL templates (used
							when org mapping doesn't specify one)
						</p>
					</div>

					{/* Config Schema */}
					<div className="space-y-2">
						<div className="flex items-center justify-between">
							<Label>Configuration Schema</Label>
							<Button
								type="button"
								variant="outline"
								size="sm"
								onClick={addConfigField}
							>
								<Plus className="h-4 w-4 mr-1" />
								Add Field
							</Button>
						</div>
						<p className="text-xs text-muted-foreground">
							Define configuration fields required for each
							organization mapping
						</p>

						<div className="space-y-3">
							{configSchema.map((field, index) => (
								<div
									key={index}
									className="flex gap-2 items-start p-3 border rounded-md"
								>
									<div className="flex-1 space-y-2">
										<Input
											placeholder="Field key (e.g., tenant_id)"
											value={field.key}
											onChange={(e) =>
												updateConfigField(index, {
													key: e.target.value,
												})
											}
											required
										/>
										<div className="flex gap-2">
											<Select
												value={field.type}
												onValueChange={(value) =>
													updateConfigField(index, {
														type: value as ConfigSchemaItem["type"],
													})
												}
											>
												<SelectTrigger className="w-32">
													<SelectValue />
												</SelectTrigger>
												<SelectContent>
													<SelectItem value="string">
														String
													</SelectItem>
													<SelectItem value="int">
														Integer
													</SelectItem>
													<SelectItem value="bool">
														Boolean
													</SelectItem>
													<SelectItem value="json">
														JSON
													</SelectItem>
													<SelectItem value="secret">
														Secret
													</SelectItem>
												</SelectContent>
											</Select>
											<label className="flex items-center gap-2 text-sm">
												<Checkbox
													checked={field.required}
													onCheckedChange={(checked) =>
														updateConfigField(
															index,
															{
																required:
																	checked === true,
															},
														)
													}
												/>
												Required
											</label>
										</div>
									</div>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										onClick={() => removeConfigField(index)}
										className="text-destructive"
									>
										<Trash2 className="h-4 w-4" />
									</Button>
								</div>
							))}
						</div>
					</div>
				</div>

				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						onClick={() => onOpenChange(false)}
						disabled={isLoading}
					>
						Cancel
					</Button>
					<Button type="submit" disabled={isLoading}>
						{isLoading ? (
							<>
								<Loader2 className="mr-2 h-4 w-4 animate-spin" />
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
						<AlertDialogDescription>
							Changing the data provider may orphan{" "}
							{initialData?.mappings?.filter((m) => m.entity_id)
								.length || 0}{" "}
							existing entity mapping(s). The entity IDs will be
							preserved but may not match entities from the new
							provider.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={() => {
								setShowDataProviderConfirm(false);
								performSave();
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
						<AlertDialogDescription>
							Renaming this integration will break any SDK calls
							using the name '{originalName}'. Workflows and
							scripts will need to be updated to use '{name}'.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={() => {
								setShowNameChangeConfirm(false);
								performSave();
							}}
						>
							Rename Anyway
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Config Field Removal Confirmation */}
			<AlertDialog
				open={showConfigFieldRemovalConfirm}
				onOpenChange={setShowConfigFieldRemovalConfirm}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Remove Configuration Fields?
						</AlertDialogTitle>
						<AlertDialogDescription>
							Removing config field(s) will delete all stored
							values for: {removedFieldNames.join(", ")}. This
							cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={() => {
								setShowConfigFieldRemovalConfirm(false);
								performSave();
							}}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
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
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
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
}: Omit<CreateIntegrationDialogProps, "open">) {
	// Only fetch if we don't have initialData and we're editing
	const {
		data: fetchedIntegration,
		isLoading,
		dataUpdatedAt,
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

	// Use dataUpdatedAt in key so form remounts when fresh data arrives
	// (e.g., after saving and re-opening the dialog with stale cache)
	const formKey = needsFetch
		? `${existingIntegration?.id}-${dataUpdatedAt}`
		: existingIntegration?.id || "new";

	return (
		<CreateIntegrationForm
			key={formKey}
			onOpenChange={onOpenChange}
			editIntegrationId={editIntegrationId}
			existingIntegration={existingIntegration}
			initialData={initialData}
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
	const dialogKey = open ? `open-${editIntegrationId || "new"}` : "closed";

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
				{open && (
					<CreateIntegrationDialogContent
						key={dialogKey}
						onOpenChange={onOpenChange}
						editIntegrationId={editIntegrationId}
						initialData={initialData}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
