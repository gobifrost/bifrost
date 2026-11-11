import type { ConfigSchemaItem } from "@/services/integrations";
import { StaticConfigChoice } from "./StaticConfigChoice";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";

export function OverrideValueEditor({
	name,
	type,
	options,
	optionLabels,
	value,
	pending,
	error,
	onChange,
	onSave,
	onCancel,
}: {
	name: string;
	type: string;
	options?: string[] | null;
	optionLabels?: ConfigSchemaItem["option_labels"];
	value: unknown;
	pending: boolean;
	error: string | null;
	onChange: (value: unknown) => void;
	onSave: (value: unknown) => void;
	onCancel: () => void;
}) {
	const id = useId();
	const [validation, setValidation] = useState<string | null>(null);
	const text =
		value == null
			? ""
			: typeof value === "string"
				? value
				: type === "json"
					? JSON.stringify(value, null, 2)
					: String(value);
	const save = () => {
		if (pending) return;
		setValidation(null);
		let parsed: unknown = value;
		if (text !== "" && type === "int") {
			if (
				!/^[+-]?\d+$/.test(text.trim()) ||
				!Number.isSafeInteger(Number(text))
			) {
				setValidation("Enter a valid integer.");
				return;
			}
			parsed = Number(text);
		}
		if (text !== "" && type === "json") {
			try {
				parsed = JSON.parse(text);
			} catch {
				setValidation("Enter valid JSON.");
				return;
			}
		}
		onSave(parsed);
	};
	return (
		<div
			className="space-y-3"
			onKeyDown={(e) => {
				if (e.key === "Escape" && !pending) {
					e.preventDefault();
					onCancel();
				} else if (
					e.key === "Enter" &&
					e.target instanceof HTMLInputElement
				) {
					e.preventDefault();
					save();
				}
			}}
		>
			<fieldset disabled={pending} className="min-w-0 space-y-2">
				<Label htmlFor={id} className="[overflow-wrap:anywhere]">
					Value for {name}
				</Label>
				{type === "string" && options?.length ? (
					<StaticConfigChoice
						id={id}
						options={options}
						optionLabels={optionLabels}
						disabled={pending}
						value={value}
						onChange={onChange}
					/>
				) : type === "bool" ? (
					<select
						id={id}
						autoFocus
						value={String(Boolean(value))}
						onChange={(e) => onChange(e.target.value === "true")}
						className="min-h-11 w-full rounded-[var(--bf-radius-control)] border border-input bg-background px-3"
					>
						<option value="true">True</option>
						<option value="false">False</option>
					</select>
				) : type === "json" ? (
					<Textarea
						id={id}
						autoFocus
						spellCheck={false}
						className="min-h-32 max-h-64 resize-y font-mono [field-sizing:fixed]"
						value={text}
						onChange={(e) => onChange(e.target.value)}
					/>
				) : (
					<Input
						id={id}
						autoFocus
						className="min-h-11"
						value={text}
						onChange={(e) => onChange(e.target.value)}
					/>
				)}
			</fieldset>
			{(validation || error) && (
				<p
					role="alert"
					className="text-sm text-destructive [overflow-wrap:anywhere]"
				>
					{validation || error}
				</p>
			)}
			<div className="flex flex-wrap gap-2">
				<Button
					type="button"
					className="min-h-11"
					disabled={pending}
					onClick={save}
				>
					{pending ? "Saving…" : "Save value"}
				</Button>
				<Button
					type="button"
					variant="outline"
					className="min-h-11"
					disabled={pending}
					onClick={onCancel}
				>
					Cancel
				</Button>
			</div>
		</div>
	);
}
