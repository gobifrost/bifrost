import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Loader2 } from "lucide-react";
import { ConfigFieldInput } from "./ConfigFieldInput";
import type { ConfigSchemaItem } from "@/services/integrations";
import { IntegrationSetupNotesContext } from "./IntegrationSetupNotes";

interface OrgConfigDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	orgId: string;
	orgName: string;
	configSchema: ConfigSchemaItem[];
	/** The org's current config overrides (only keys that have explicit overrides) */
	currentConfig: Record<string, unknown>;
	onSave: (config: Record<string, unknown>) => Promise<void>;
	notes?: string | null;
}

function OrgConfigDialogContent({
	orgName,
	configSchema,
	currentConfig,
	onSave,
	onOpenChange,
	notes,
	isSaving,
	setIsSaving,
}: Omit<OrgConfigDialogProps, "open" | "orgId"> & {
	isSaving: boolean;
	setIsSaving: (saving: boolean) => void;
}) {
	// Track which keys have overrides (start with what's in currentConfig)
	// We use a separate Set to track this because we need to distinguish
	// between "value is undefined because no override" vs "value is explicitly set to empty"
	const [overrideKeys, setOverrideKeys] = useState<Set<string>>(
		() => new Set(Object.keys(currentConfig)),
	);

	// Form values - only contains values for fields that have overrides
	const [formValues, setFormValues] = useState<Record<string, unknown>>(
		() => ({ ...currentConfig }),
	);
	const [saveError, setSaveError] = useState<string | null>(null);

	const handleFieldChange = (key: string, value: unknown) => {
		setFormValues((prev) => ({ ...prev, [key]: value }));
		// Mark as having an override when user types
		if (value !== undefined && value !== null && value !== "") {
			setOverrideKeys((prev) => new Set(prev).add(key));
		}
	};

	const handleReset = (key: string) => {
		// Remove the override - this will cause the integration default to be used
		setFormValues((prev) => {
			const next = { ...prev };
			delete next[key];
			return next;
		});
		setOverrideKeys((prev) => {
			const next = new Set(prev);
			next.delete(key);
			return next;
		});
	};

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (isSaving) return;
		setSaveError(null);
		setIsSaving(true);
		try {
			// Build config to save:
			// - Keys with non-empty values: send the value
			// - Keys that were in currentConfig but now empty/removed: send null to delete
			const configToSave: Record<string, unknown> = {};

			// Check all keys that were originally set OR are in overrideKeys
			const allKeys = new Set([
				...Object.keys(currentConfig),
				...overrideKeys,
			]);

			for (const key of allKeys) {
				const value = formValues[key];
				const hasValue =
					value !== undefined && value !== null && value !== "";

				if (hasValue) {
					// Has a value - send it
					configToSave[key] = value;
				} else if (key in currentConfig) {
					// Was previously set but now empty - send null to delete
					configToSave[key] = null;
				}
				// If not in currentConfig and no value, skip (nothing to do)
			}

			await onSave(configToSave);
			onOpenChange(false);
		} catch {
			setSaveError(
				"Unable to save organization configuration. Your values are retained; try saving again.",
			);
		} finally {
			setIsSaving(false);
		}
	};

	const handleCancel = () => {
		onOpenChange(false);
	};

	return (
		<form className="flex min-h-0 flex-1 flex-col" onSubmit={handleSubmit}>
			<DialogHeader className="shrink-0">
				<DialogTitle className="[overflow-wrap:anywhere]">
					Configure {orgName}
				</DialogTitle>
				<DialogDescription>
					Set organization-specific configuration overrides. Empty
					fields use the integration default.
				</DialogDescription>
			</DialogHeader>

			<div className="min-h-0 flex-1 overflow-y-auto py-4">
				<IntegrationSetupNotesContext notes={notes}>
				<fieldset disabled={isSaving} className="min-w-0 space-y-4">
					{configSchema.length > 0 ? (
						configSchema.map((field) => (
							<ConfigFieldInput
								key={field.key}
								field={field}
								value={formValues[field.key]}
								onChange={(value) =>
									handleFieldChange(field.key, value)
								}
								onReset={() => handleReset(field.key)}
								hasOverride={overrideKeys.has(field.key)}
							/>
						))
					) : (
						<p className="text-center py-4 text-sm text-muted-foreground">
							No configuration fields defined
						</p>
					)}
				</fieldset>
				</IntegrationSetupNotesContext>
			</div>
			{saveError && (
				<p
					role="alert"
					className="my-3 shrink-0 text-sm text-destructive [overflow-wrap:anywhere]"
				>
					{saveError}
				</p>
			)}
			<DialogFooter className="shrink-0 border-t pt-4">
				<Button
					className="min-h-11"
					variant="outline"
					onClick={handleCancel}
					type="button"
					disabled={isSaving}
				>
					Cancel
				</Button>
				<Button className="min-h-11" type="submit" disabled={isSaving}>
					{isSaving ? (
						<>
							<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
							Saving...
						</>
					) : (
						"Save"
					)}
				</Button>
			</DialogFooter>
		</form>
	);
}

export function OrgConfigDialog({
	open,
	onOpenChange,
	orgId,
	orgName,
	configSchema,
	currentConfig,
	onSave,
	notes,
}: OrgConfigDialogProps) {
	const [isSaving, setIsSaving] = useState(false);
	return (
		<Dialog
			open={open}
			onOpenChange={(next) => {
				if (!isSaving) onOpenChange(next);
			}}
		>
			<DialogContent className={`flex max-h-[90dvh] flex-col overflow-hidden ${notes ? "max-w-[1120px]" : "max-w-2xl"}`}>
				{/* Key resets form state when switching between orgs */}
				<OrgConfigDialogContent
					key={orgId}
					isSaving={isSaving}
					setIsSaving={setIsSaving}
					orgName={orgName}
					configSchema={configSchema}
					currentConfig={currentConfig}
					onSave={onSave}
					onOpenChange={onOpenChange}
					notes={notes}
				/>
			</DialogContent>
		</Dialog>
	);
}
