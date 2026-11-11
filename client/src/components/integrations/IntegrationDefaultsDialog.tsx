import type { ConfigSchemaItem } from "@/services/integrations";
import { StaticConfigChoice } from "./StaticConfigChoice";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type ConfigSchemaField = Pick<ConfigSchemaItem, "key" | "type" | "options" | "option_labels"> & Partial<Pick<ConfigSchemaItem, "required">>;

export interface IntegrationDefaultsDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	configSchema: ConfigSchemaField[];
	formValues: Record<string, unknown>;
	onFormValuesChange: (values: Record<string, unknown>) => void;
	onSave: () => void;
	isSaving: boolean;
	error?: string | null;
}

export function IntegrationDefaultsDialog({
	open,
	onOpenChange,
	configSchema,
	formValues,
	onFormValuesChange,
	onSave,
	isSaving,
	error,
}: IntegrationDefaultsDialogProps) {
	return (
		<Dialog
			open={open}
			onOpenChange={(next) => {
				if (!isSaving) onOpenChange(next);
			}}
		>
			<DialogContent className="flex max-w-md max-h-[90dvh] flex-col overflow-hidden">
				<form
					onSubmit={(e) => {
						e.preventDefault();
						if (!isSaving) onSave();
					}}
					className="flex min-h-0 flex-1 flex-col"
				>
					<DialogHeader className="shrink-0">
						<DialogTitle>Edit Configuration Defaults</DialogTitle>
						<DialogDescription>
							Set default values for new organization mappings
						</DialogDescription>
					</DialogHeader>
					<div className="min-h-0 flex-1 overflow-y-auto py-4">
						<fieldset
							disabled={isSaving}
							className="min-w-0 space-y-4"
						>
							{configSchema?.map((field) => (
								<div key={field.key} className="space-y-2">
									<Label
										className="flex flex-wrap [overflow-wrap:anywhere]"
										htmlFor={`default-${field.key}`}
									>
										{field.key}
										{field.required && (
											<span className="text-destructive ml-1">
												*
											</span>
										)}
										<span className="text-muted-foreground text-xs ml-2">
											({field.type})
										</span>
									</Label>
									{field.type === "string" &&
									field.options?.length ? (
										<StaticConfigChoice
											id={`default-${field.key}`}
											options={field.options}
											optionLabels={field.option_labels}
											disabled={isSaving}
											value={formValues[field.key]}
											onChange={(value) =>
												onFormValuesChange({
													...formValues,
													[field.key]: value,
												})
											}
										/>
									) : field.type === "bool" ? (
										<select
											id={`default-${field.key}`}
											value={String(
												formValues[field.key] ?? "",
											)}
											onChange={(e) =>
												onFormValuesChange({
													...formValues,
													[field.key]:
														e.target.value === ""
															? ""
															: e.target.value ===
																"true",
												})
											}
											className="flex min-h-11 w-full rounded-[var(--bf-radius-control)] border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/25 disabled:opacity-50"
										>
											<option value="">
												— Not set —
											</option>
											<option value="true">True</option>
											<option value="false">False</option>
										</select>
									) : field.type === "json" ? (
										<Textarea
											id={`default-${field.key}`}
											className="min-h-32 max-h-64 resize-y font-mono text-sm [field-sizing:fixed] motion-reduce:transition-none"
											spellCheck={false}
											value={
												formValues[field.key] == null
													? ""
													: typeof formValues[
																field.key
														  ] === "string"
														? (formValues[
																field.key
															] as string)
														: JSON.stringify(
																formValues[
																	field.key
																],
																null,
																2,
															)
											}
											onChange={(e) =>
												onFormValuesChange({
													...formValues,
													[field.key]: e.target.value,
												})
											}
										/>
									) : field.type === "int" ? (
										<Input
											className="min-h-11"
											id={`default-${field.key}`}
											type="text"
											placeholder={`Default ${field.key}`}
											value={String(
												formValues[field.key] ?? "",
											)}
											onChange={(e) =>
												onFormValuesChange({
													...formValues,
													[field.key]: e.target.value,
												})
											}
										/>
									) : (
										<Input
											className="min-h-11"
											autoComplete={
												field.type === "secret"
													? "new-password"
													: undefined
											}
											id={`default-${field.key}`}
											type={
												field.type === "secret"
													? "password"
													: "text"
											}
											placeholder={`Default ${field.key}`}
											value={String(
												formValues[field.key] ?? "",
											)}
											onChange={(e) =>
												onFormValuesChange({
													...formValues,
													[field.key]: e.target.value,
												})
											}
										/>
									)}
								</div>
							))}
						</fieldset>
					</div>
					{error && (
						<p
							role="alert"
							className="my-3 shrink-0 text-sm text-destructive [overflow-wrap:anywhere]"
						>
							{error}
						</p>
					)}
					<DialogFooter className="shrink-0 border-t pt-4">
						<Button
							className="min-h-11"
							type="button"
							variant="outline"
							onClick={() => onOpenChange(false)}
							disabled={isSaving}
						>
							Cancel
						</Button>
						<Button
							className="min-h-11"
							type="submit"
							disabled={isSaving}
						>
							{isSaving ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
									Saving...
								</>
							) : (
								"Save Defaults"
							)}
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
