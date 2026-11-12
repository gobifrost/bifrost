import { useId } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { OptionsEditor } from "@/components/forms/OptionsEditor";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import type { ConfigSchemaItem } from "@/services/integrations";

export function IntegrationSchemaEditor({
	fields,
	onAdd,
	onUpdate,
	onRemove,
}: {
	fields: ConfigSchemaItem[];
	onAdd: () => void;
	onUpdate: (index: number, field: Partial<ConfigSchemaItem>) => void;
	onRemove: (index: number) => void;
}) {
	return (
		<section className="min-w-0 space-y-3">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<h3 className="text-sm font-medium">Configuration Schema</h3>
				<Button
					type="button"
					variant="outline"
					className="min-h-11"
					onClick={onAdd}
				>
					<Plus className="size-4" />
					Add Field
				</Button>
			</div>
			<p className="text-xs text-muted-foreground">
				Define configuration fields required for each organization
				mapping
			</p>
			<div className="space-y-3">
				{fields.map((field, index) => (
					<SchemaField
						key={index}
						field={field}
						index={index}
						onUpdate={onUpdate}
						onRemove={onRemove}
					/>
				))}
			</div>
		</section>
	);
}
function SchemaField({
	field,
	index,
	onUpdate,
	onRemove,
}: {
	field: ConfigSchemaItem;
	index: number;
	onUpdate: (index: number, field: Partial<ConfigSchemaItem>) => void;
	onRemove: (index: number) => void;
}) {
	const id = useId();
	return (
		<fieldset className="min-w-0 space-y-3 rounded-[var(--bf-radius-control)] border border-border/70 p-4">
			<legend className="px-1 text-xs text-muted-foreground">
				Field {index + 1}
			</legend>
			<div className="space-y-2">
				<Label htmlFor={`${id}-key`}>Field key</Label>
				<Input
					id={`${id}-key`}
					className="min-h-11"
					placeholder="Field key (e.g., tenant_id)"
					value={field.key}
					onChange={(e) => onUpdate(index, { key: e.target.value })}
					required
				/>
			</div>
			<div className="flex flex-wrap items-end gap-3">
				<div className="min-w-0 flex-1 space-y-2">
					<Label htmlFor={`${id}-type`}>Type</Label>
					<Select
						value={field.type}
						onValueChange={(value) =>
							onUpdate(index, {
								type: value as ConfigSchemaItem["type"],
								...(value !== "string"
									? { options: null, option_labels: null }
									: {}),
							})
						}
					>
						<SelectTrigger
							id={`${id}-type`}
							className="min-h-11 w-full"
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							{Object.entries({
								string: "String",
								int: "Integer",
								bool: "Boolean",
								json: "JSON",
								secret: "Secret",
							}).map(([value, label]) => (
								<SelectItem key={value} value={value}>
									{label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
				</div>
				<Label htmlFor={`${id}-required`} className="min-h-11 gap-2">
					<Switch
						id={`${id}-required`}
						checked={field.required}
						onCheckedChange={(checked) =>
							onUpdate(index, { required: checked === true })
						}
					/>
					Required
				</Label>
			</div>
			{field.type === "string" && (
				<OptionsEditor
					options={(field.options ?? []).map((value) => ({
						value,
						label: field.option_labels?.[value] ?? value,
					}))}
					onChange={(choices) =>
						onUpdate(index, {
							options: choices.length
								? choices.map((choice) => choice.value)
								: null,
							option_labels: choices.length
								? Object.fromEntries(
										choices.map((choice) => [
											choice.value,
											choice.label,
										]),
									)
								: null,
						})
					}
					label="Dropdown Options"
					helpText="Add choices for a dropdown, or leave empty for free text. Labels are shown to users; stored values are passed to workflows and included in Solution templates."
				/>
			)}
			<Button
				type="button"
				variant="ghost"
				className="min-h-11 text-destructive"
				aria-label={`Remove field ${index + 1}`}
				onClick={() => onRemove(index)}
			>
				<Trash2 className="size-4" />
				Remove field
			</Button>
		</fieldset>
	);
}
