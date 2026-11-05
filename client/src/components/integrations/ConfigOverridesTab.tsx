import { useState, useMemo } from "react";
import { OverrideValueEditor } from "./OverrideValueEditor";
import { ConfigurationOverrideRecord } from "./ConfigurationOverrideRecord";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
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
import { Settings, Trash2 } from "lucide-react";
import { toast } from "sonner";
import type {
	ConfigSchemaItem,
	IntegrationMapping,
} from "@/services/integrations";
import { useUpdateMapping } from "@/services/integrations";

interface OrgWithMapping {
	id: string;
	name: string;
	mapping?: IntegrationMapping;
	formData: {
		organization_id: string;
		entity_id: string;
		entity_name: string;
		oauth_token_id?: string;
		config: Record<string, unknown>;
	};
}

interface ConfigRow {
	orgId: string;
	orgName: string;
	mappingId: string | null;
	configKey: string;
	value: unknown;
	hasOverride: boolean;
	fieldType: ConfigSchemaItem["type"];
	fieldSchema: ConfigSchemaItem;
	// For saving - we need full mapping data
	mapping: IntegrationMapping | undefined;
	currentConfig: Record<string, unknown>;
}

interface ConfigOverridesTabProps {
	orgsWithMappings: OrgWithMapping[];
	configSchema: ConfigSchemaItem[];
	integrationId: string;
	canManageMapping: (organizationId: string) => boolean;
}

interface EditingCell {
	rowKey: string; // orgId:configKey
	value: unknown;
}

export function ConfigOverridesTab({
	orgsWithMappings,
	configSchema,
	integrationId,
	canManageMapping,
}: ConfigOverridesTabProps) {
	const [saveError, setSaveError] = useState<string | null>(null);
	const [editingCell, setEditingCell] = useState<EditingCell | null>(null);
	const [savingRows, setSavingRows] = useState<Set<string>>(new Set());
	const [deleteConfirm, setDeleteConfirm] = useState<ConfigRow | null>(null);
	const [deleteError, setDeleteError] = useState<string | null>(null);
	const isDeleting =
		deleteConfirm !== null &&
		savingRows.has(`${deleteConfirm.orgId}:${deleteConfirm.configKey}`);

	const updateMutation = useUpdateMapping();

	// Filter out secret fields - they should never be shown in this view
	const visibleSchema = useMemo(
		() => configSchema.filter((field) => field.type !== "secret"),
		[configSchema],
	);

	// Flatten data: one row per org + config key that HAS an override (excluding secrets)
	const rows = useMemo((): ConfigRow[] => {
		const result: ConfigRow[] = [];

		// Only include orgs that have mappings
		const orgsWithMappingsOnly = orgsWithMappings.filter(
			(org) => org.mapping,
		);

		for (const org of orgsWithMappingsOnly) {
			for (const field of visibleSchema) {
				const currentValue = org.mapping?.config?.[field.key];
				// Only include rows that have an actual override
				const hasOverride =
					currentValue !== undefined && currentValue !== null;

				if (hasOverride) {
					result.push({
						orgId: org.id,
						orgName: org.name,
						mappingId: org.mapping?.id || null,
						configKey: field.key,
						value: currentValue,
						hasOverride: true,
						fieldType: field.type,
						fieldSchema: field,
						mapping: org.mapping,
						currentConfig: org.mapping?.config || {},
					});
				}
			}
		}

		return result;
	}, [orgsWithMappings, visibleSchema]);

	const getRowKey = (row: ConfigRow) => `${row.orgId}:${row.configKey}`;

	const handleCellClick = (row: ConfigRow) => {
		if (!canManageMapping(row.orgId)) return;
		if (savingRows.size > 0) return;
		setSaveError(null);
		// Start with current value or empty - never show defaults
		setEditingCell({
			rowKey: getRowKey(row),
			value: row.value ?? "",
		});
	};

	const handleSave = async (row: ConfigRow, newValue: unknown) => {
		if (!canManageMapping(row.orgId)) return;
		if (!row.mappingId || !row.mapping) {
			toast.error("No mapping exists - create a mapping first");
			return;
		}

		const rowKey = getRowKey(row);
		if (savingRows.has(rowKey)) return;
		setSaveError(null);
		setSavingRows((prev) => new Set(prev).add(rowKey));

		try {
			const updatedConfig = {
				[row.configKey]:
					newValue === "" || newValue == null ? null : newValue,
			};

			await updateMutation.mutateAsync({
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: row.mappingId,
					},
				},
				body: {
					entity_id: row.mapping.entity_id,
					entity_name: row.mapping.entity_name || undefined,
					oauth_token_id: row.mapping.oauth_token_id || undefined,
					config:
						Object.keys(updatedConfig).length > 0
							? updatedConfig
							: undefined,
				},
			});

			setEditingCell(null);
			// Query invalidation in useUpdateMapping handles refresh
		} catch {
			setSaveError(
				"Unable to save this value. Your edit is retained; try again.",
			);
		} finally {
			setSavingRows((prev) => {
				const next = new Set(prev);
				next.delete(rowKey);
				return next;
			});
		}
	};

	const handleDeleteClick = (row: ConfigRow) => {
		if (!canManageMapping(row.orgId)) return;
		setDeleteError(null);
		// Show confirmation dialog
		setDeleteConfirm(row);
	};

	const handleDeleteConfirm = async () => {
		const row = deleteConfirm;
		if (!row || !canManageMapping(row.orgId) || isDeleting) return;
		setDeleteError(null);

		// Delete the override by sending null for the key
		if (!row.mappingId || !row.mapping) {
			toast.error("No mapping exists");
			setDeleteConfirm(null);
			return;
		}

		const rowKey = getRowKey(row);
		setSavingRows((prev) => new Set(prev).add(rowKey));

		try {
			// Send null for the key to delete it from the database
			const configToSave = {
				[row.configKey]: null, // This tells backend to delete this key
			};

			await updateMutation.mutateAsync({
				params: {
					path: {
						integration_id: integrationId,
						mapping_id: row.mappingId,
					},
				},
				body: {
					entity_id: row.mapping.entity_id,
					entity_name: row.mapping.entity_name || undefined,
					oauth_token_id: row.mapping.oauth_token_id || undefined,
					config: configToSave,
				},
			});

			setDeleteConfirm(null);
			toast.success(
				`Removed ${row.configKey} override for ${row.orgName}`,
			);
			// Query invalidation in useUpdateMapping handles refresh
		} catch {
			setDeleteError(
				"Unable to delete this override. Try again to restore the integration default.",
			);
		} finally {
			setSavingRows((prev) => {
				const next = new Set(prev);
				next.delete(rowKey);
				return next;
			});
		}
	};

	const handleCancel = () => {
		setEditingCell(null);
	};

	const formatDisplayValue = (
		value: unknown,
		fieldType: ConfigSchemaItem["type"],
		hasOverride: boolean,
	): string => {
		// If no override, show placeholder
		if (!hasOverride) {
			return "—";
		}
		if (value === undefined || value === null || value === "") {
			return "—";
		}
		if (fieldType === "bool") {
			return value ? "True" : "False";
		}
		if (fieldType === "json") {
			return typeof value === "string" ? value : JSON.stringify(value);
		}
		return String(value);
	};

	if (visibleSchema.length === 0) {
		return (
			<div className="flex flex-col items-center justify-center py-12 text-center">
				<Settings className="h-12 w-12 text-muted-foreground" />
				<h3 className="mt-4 text-lg font-semibold">
					No configuration schema
				</h3>
				<p className="mt-2 text-sm text-muted-foreground max-w-md">
					Add configuration fields to the integration to enable
					per-org configuration.
				</p>
			</div>
		);
	}

	if (rows.length === 0) {
		return (
			<div className="flex flex-col items-center justify-center py-12 text-center">
				<Settings className="h-12 w-12 text-muted-foreground" />
				<h3 className="mt-4 text-lg font-semibold">
					No configuration overrides
				</h3>
				<p className="mt-2 text-sm text-muted-foreground max-w-md">
					All organizations are using integration defaults. Use the
					Configure button in the Mappings tab to set
					organization-specific values.
				</p>
			</div>
		);
	}

	return (
		<div className="space-y-4">
			<p className="text-sm text-muted-foreground">
				Organization-specific configuration overrides. Click to edit, or
				delete to revert to integration default.
			</p>
			<ul aria-label="Configuration overrides" className="divide-y">
				{rows.map((row) => {
					const rowKey = getRowKey(row);
					const isEditing = editingCell?.rowKey === rowKey;
					const isSaving = savingRows.has(rowKey);
					const canManage = canManageMapping(row.orgId);
					return (
						<ConfigurationOverrideRecord
							key={rowKey}
							organization={row.orgName}
							configKey={row.configKey}
							type={row.fieldType}
							actions={
								canManage &&
								!isEditing && (
									<RecordActionsMenu
										label={`More actions for ${row.orgName} ${row.configKey}`}
									>
										<DropdownMenuItem
											variant="destructive"
											className="min-h-11"
											disabled={savingRows.size > 0}
											onSelect={(event) => {
												event.preventDefault();
												handleDeleteClick(row);
											}}
										>
											<Trash2 className="size-4" />
											Delete override
										</DropdownMenuItem>
									</RecordActionsMenu>
								)
							}
						>
							{isEditing ? (
								<OverrideValueEditor
									name={row.configKey}
									type={row.fieldType}
									value={editingCell.value}
									pending={isSaving}
									error={saveError}
									onChange={(value) =>
										setEditingCell({ rowKey, value })
									}
									onSave={(value) => {
										void handleSave(row, value);
									}}
									onCancel={handleCancel}
								/>
							) : canManage ? (
								<button
									type="button"
									disabled={savingRows.size > 0}
									aria-label={`Edit ${row.configKey} for ${row.orgName}`}
									onClick={() => handleCellClick(row)}
									className="min-h-11 w-full rounded-[var(--bf-radius-control)] border border-border/70 bg-background px-3 py-3 text-left text-sm whitespace-pre-wrap [overflow-wrap:anywhere] hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25"
								>
									{formatDisplayValue(
										row.value,
										row.fieldType,
										row.hasOverride,
									)}
								</button>
							) : (
								<span className="block min-h-11 w-full rounded-[var(--bf-radius-control)] border border-border/70 bg-muted/30 px-3 py-3 text-left text-sm whitespace-pre-wrap [overflow-wrap:anywhere]">
									{formatDisplayValue(
										row.value,
										row.fieldType,
										row.hasOverride,
									)}
								</span>
							)}
						</ConfigurationOverrideRecord>
					);
				})}
			</ul>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={deleteConfirm !== null}
				onOpenChange={(open) => {
					if (!open && !isDeleting) setDeleteConfirm(null);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete Configuration Override
						</AlertDialogTitle>
						<AlertDialogDescription className="[overflow-wrap:anywhere]">
							Are you sure you want to delete the{" "}
							<span className="font-mono font-semibold">
								{deleteConfirm?.configKey}
							</span>{" "}
							override for {deleteConfirm?.orgName}? This will
							revert to the integration default value.
						</AlertDialogDescription>
					</AlertDialogHeader>
					{deleteError && (
						<p
							role="alert"
							className="text-sm text-destructive [overflow-wrap:anywhere]"
						>
							{deleteError}
						</p>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel
							className="min-h-11 lg:min-h-11"
							disabled={isDeleting}
						>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							disabled={isDeleting}
							onClick={(event) => {
								event.preventDefault();
								void handleDeleteConfirm();
							}}
							className="min-h-11 bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{isDeleting
								? "Deleting…"
								: deleteError
									? "Retry deletion"
									: "Delete"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
