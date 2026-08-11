import { useState } from "react";
import { Eye, Pencil } from "lucide-react";

import { TiptapEditor } from "@/components/ui/tiptap-editor";
import {
	ToggleGroup,
	ToggleGroupItem,
} from "@/components/ui/toggle-group";
import { cn } from "@/lib/utils";

type MarkdownMode = "edit" | "preview";

interface MarkdownEditorFieldProps {
	value: string;
	onChange?: (value: string) => void;
	readOnly?: boolean;
	placeholder?: string;
	ariaLabel: string;
	className?: string;
	id?: string;
	"aria-describedby"?: string;
	"aria-invalid"?: boolean;
}

/**
 * Shared Markdown form surface.
 *
 * TipTap owns both states so the editable value and rendered preview use the
 * same Markdown parser. Read-only consumers render the preview directly.
 */
export function MarkdownEditorField({
	value,
	onChange,
	readOnly = false,
	placeholder,
	ariaLabel,
	className,
	id,
	"aria-describedby": ariaDescribedBy,
	"aria-invalid": ariaInvalid,
}: MarkdownEditorFieldProps) {
	const [mode, setMode] = useState<MarkdownMode>(
		readOnly ? "preview" : "edit",
	);

	const previewing = readOnly || mode === "preview";

	return (
		<div className={cn("space-y-2", className)}>
			{!readOnly ? (
				<div className="flex justify-end">
					<ToggleGroup
						type="single"
						value={mode}
						onValueChange={(nextMode) => {
							if (nextMode === "edit" || nextMode === "preview") {
								setMode(nextMode);
							}
						}}
						variant="outline"
						size="sm"
						aria-label="Markdown display mode"
					>
						<ToggleGroupItem value="edit" aria-label="Edit markdown">
							<Pencil className="mr-1.5 h-3.5 w-3.5" />
							Edit
						</ToggleGroupItem>
						<ToggleGroupItem value="preview" aria-label="Preview markdown">
							<Eye className="mr-1.5 h-3.5 w-3.5" />
							Preview
						</ToggleGroupItem>
					</ToggleGroup>
				</div>
			) : null}
			<TiptapEditor
				content={value}
				onChange={previewing ? undefined : onChange}
				readOnly={previewing}
				placeholder={
					previewing ? "Nothing to preview yet." : placeholder
				}
				className="min-h-[240px]"
				id={id}
				ariaLabel={ariaLabel}
				ariaDescribedBy={ariaDescribedBy}
				ariaInvalid={ariaInvalid}
			/>
		</div>
	);
}
