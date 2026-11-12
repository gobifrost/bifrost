import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { DatePicker } from "@/components/ui/date-picker";
import { DateTimeFieldInput } from "@/components/forms/DateTimeFieldInput";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import type { FormField } from "@/lib/client-types";
import type { FormLayout } from "./form-layout";
import { FormLayoutView } from "./FormLayoutView";

interface FormPreviewProps {
	formName: string;
	formDescription: string;
	fields: FormField[];
	layout?: FormLayout | null;
}

export function FormPreview({
	formName,
	formDescription,
	fields,
	layout,
}: FormPreviewProps) {
	const renderField = (field: FormField) => {
		switch (field.type) {
			case "date":
			case "datetime": {
				const DateControl =
					field.type === "date" ? DatePicker : DateTimeFieldInput;
				return (
					<DateControl
						id={`preview-${field.name}`}
						aria-label={field.label ?? field.name}
						value={(field.default_value as string) || ""}
						onChange={() => {}}
						placeholder={field.placeholder ?? undefined}
						disabled
					/>
				);
			}
			case "textarea":
				return (
					<Textarea
						id={`preview-${field.name}`}
						placeholder={field.placeholder ?? undefined}
						defaultValue={field.default_value as string}
						disabled
					/>
				);
			case "checkbox":
				return (
					<div className="flex items-start gap-2">
						<input
							type="checkbox"
							id={`preview-${field.name}`}
							defaultChecked={field.default_value as boolean}
							disabled
							className="mt-0.5 h-4 w-4 shrink-0"
						/>
						<Label
							htmlFor={`preview-${field.name}`}
							className="cursor-pointer leading-5"
						>
							<span>{field.label}</span>
							{field.required && (
								<span
									aria-hidden="true"
									className="text-destructive ml-1"
								>
									*
								</span>
							)}
						</Label>
					</div>
				);
			case "select":
				return (
					<select
						id={`preview-${field.name}`}
						className="flex h-8 w-full rounded-2xl border border-transparent bg-input/50 px-2.5 text-sm transition-[color,box-shadow] duration-200 outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50"
						disabled
					>
						<option>
							{field.placeholder || "Select an option..."}
						</option>
					</select>
				);
			default:
				return (
					<Input
						id={`preview-${field.name}`}
						type={
							field.type === "email"
								? "email"
								: field.type === "number"
									? "number"
									: "text"
						}
						placeholder={field.placeholder ?? undefined}
						defaultValue={field.default_value as string}
						disabled
					/>
				);
		}
	};

	const renderPreviewField = (field: FormField) => (
		<div className="space-y-2">
			{field.type !== "checkbox" && (
				<Label htmlFor={`preview-${field.name}`}>
					{field.label}
					{field.required && (
						<span className="ml-1 text-destructive">*</span>
					)}
				</Label>
			)}
			{renderField(field)}
			{field.help_text && (
				<p className="text-sm text-muted-foreground">
					{field.help_text}
				</p>
			)}
		</div>
	);

	return (
		<div className="flex justify-center">
			<Card className="w-full max-w-2xl">
				<CardHeader>
					<CardTitle>{formName || "Form Preview"}</CardTitle>
					{formDescription && (
						<CardDescription>{formDescription}</CardDescription>
					)}
				</CardHeader>
				<CardContent>
					{fields.length > 0 ? (
						<div className="space-y-6">
							{layout ? (
								<FormLayoutView
									layout={layout}
									fields={fields}
									renderField={renderPreviewField}
								/>
							) : (
								fields.map((field) => (
									<div key={field.name}>
										{renderPreviewField(field)}
									</div>
								))
							)}
							<div className="pt-4">
								<Button disabled>Submit</Button>
							</div>
						</div>
					) : (
						<div className="flex flex-col items-center justify-center py-12 text-center">
							<p className="text-sm text-muted-foreground">
								Add fields to see the form preview
							</p>
						</div>
					)}
				</CardContent>
			</Card>
		</div>
	);
}
