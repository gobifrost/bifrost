import { StaticConfigChoice } from "./StaticConfigChoice";
import { useId } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { RotateCcw, CheckCircle2 } from "lucide-react";
import type { ConfigSchemaItem } from "@/services/integrations";

interface ConfigFieldInputProps {
	field: ConfigSchemaItem;
	value: unknown;
	onChange: (value: unknown) => void;
	/** If provided, shows a reset button that clears to undefined (uses integration default) */
	onReset?: () => void;
	/** Whether this field has a value different from integration default */
	hasOverride?: boolean;
}

export function ConfigFieldInput({
	field,
	value,
	onChange,
	onReset,
	hasOverride,
}: ConfigFieldInputProps) {
	const inputId = useId();
	const descriptionId = `${inputId}-description`;
	// Secrets should never be displayed - only allow setting new values
	if (field.type === "secret") {
		const hasSecretValue = Boolean(value);

		return (
			<div className="space-y-2">
				<div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
					<Label
						htmlFor={inputId}
						className="min-h-11 flex min-w-0 flex-wrap items-center gap-2 [overflow-wrap:anywhere]"
					>
						{field.key}
						{field.required && (
							<span className="text-destructive">*</span>
						)}
						{hasSecretValue && (
							<Badge
								variant="secondary"
								className="text-xs font-normal"
							>
								<CheckCircle2 className="h-3 w-3 mr-1" />
								Secret configured
							</Badge>
						)}
					</Label>
					{onReset && hasOverride && (
						<Button
							type="button"
							variant="ghost"
							size="sm"
							onClick={onReset}
							className="min-h-11 px-3 text-muted-foreground hover:text-foreground"
							title="Reset to integration default"
						>
							<RotateCcw className="h-3 w-3 mr-1" />
							Reset
						</Button>
					)}
				</div>
				{field.description && (
					<p
						id={descriptionId}
						className="text-sm text-muted-foreground [overflow-wrap:anywhere]"
					>
						{field.description}
					</p>
				)}
				<Input
					id={inputId}
					aria-describedby={
						field.description ? descriptionId : undefined
					}
					className="min-h-11"
					type="password"
					autoComplete="new-password"
					value={(value as string) || ""}
					onChange={(e) => onChange(e.target.value)}
					placeholder={
						hasOverride
							? "••••••••  (override set)"
							: hasSecretValue
								? "••••••••"
								: "Enter new value..."
					}
				/>
				{hasOverride && !value && (
					<p className="text-xs text-muted-foreground">
						An override is set. Enter a new value to change it, or
						reset to use the integration default.
					</p>
				)}
			</div>
		);
	}

	const renderInput = () => {
		if (field.type === "string" && field.options?.length) {
			return (
				<StaticConfigChoice
					id={inputId}
					options={field.options}
					optionLabels={field.option_labels}
					value={value}
					aria-describedby={
						field.description ? descriptionId : undefined
					}
					onChange={(next) =>
						onChange(next === "" ? undefined : next)
					}
				/>
			);
		}
		switch (field.type) {
			case "bool":
				return (
					<Switch
						id={inputId}
						aria-describedby={
							field.description ? descriptionId : undefined
						}
						checked={Boolean(value)}
						onCheckedChange={(checked) => onChange(checked)}
					/>
				);

			case "int":
				return (
					<Input
						id={inputId}
						aria-describedby={
							field.description ? descriptionId : undefined
						}
						className="min-h-11"
						type="number"
						value={
							value !== undefined && value !== null
								? (value as number)
								: ""
						}
						onChange={(e) => {
							const val = e.target.value;
							onChange(
								val === "" ? undefined : parseInt(val) || 0,
							);
						}}
						placeholder={field.description || field.key}
					/>
				);

			case "json":
				return (
					<Textarea
						id={inputId}
						aria-describedby={
							field.description ? descriptionId : undefined
						}
						spellCheck={false}
						value={
							value === undefined || value === null
								? ""
								: typeof value === "string"
									? value
									: JSON.stringify(value, null, 2)
						}
						onChange={(e) => {
							const val = e.target.value;
							if (val === "") {
								onChange(undefined);
								return;
							}
							try {
								onChange(JSON.parse(val));
							} catch {
								// Keep as string if invalid JSON
								onChange(val);
							}
						}}
						placeholder={field.description || field.key}
						className="min-h-32 max-h-64 resize-y font-mono [field-sizing:fixed] motion-reduce:transition-none"
					/>
				);

			case "string":
			default:
				return (
					<Input
						id={inputId}
						aria-describedby={
							field.description ? descriptionId : undefined
						}
						className="min-h-11"
						type="text"
						value={(value as string) ?? ""}
						onChange={(e) => {
							const val = e.target.value;
							onChange(val === "" ? undefined : val);
						}}
						placeholder={field.description || field.key}
					/>
				);
		}
	};

	return (
		<div className="space-y-2">
			<div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
				<Label
					htmlFor={inputId}
					className="min-h-11 flex min-w-0 flex-wrap items-center gap-1 [overflow-wrap:anywhere]"
				>
					{field.key}
					{field.required && (
						<span className="text-destructive">*</span>
					)}
				</Label>
				{onReset && hasOverride && (
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={onReset}
						className="min-h-11 px-3 text-muted-foreground hover:text-foreground"
						title="Reset to integration default"
					>
						<RotateCcw className="h-3 w-3 mr-1" />
						Reset
					</Button>
				)}
			</div>
			{field.description && (
				<p
					id={descriptionId}
					className="text-sm text-muted-foreground [overflow-wrap:anywhere]"
				>
					{field.description}
				</p>
			)}
			{renderInput()}
		</div>
	);
}
