import { useEffect } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import { Markdown } from "@tiptap/markdown";
import { TiptapToolbar } from "./tiptap-toolbar";
import { cn } from "@/lib/utils";

interface TiptapEditorProps {
	content: string;
	onChange?: (content: string) => void;
	readOnly?: boolean;
	placeholder?: string;
	className?: string;
	editorClassName?: string;
	ariaLabel?: string;
	id?: string;
	ariaDescribedBy?: string;
	ariaInvalid?: boolean;
}

export function TiptapEditor({
	content,
	onChange,
	readOnly = false,
	placeholder = "Start writing...",
	className,
	editorClassName,
	ariaLabel,
	id,
	ariaDescribedBy,
	ariaInvalid,
}: TiptapEditorProps) {
	const editor = useEditor({
		extensions: [
			StarterKit.configure({
				heading: {
					levels: [2, 3],
				},
				link: {
					openOnClick: false,
					HTMLAttributes: {
						class: "text-primary underline",
					},
				},
			}),
			Markdown,
			Placeholder.configure({
				placeholder,
				emptyEditorClass:
					"before:content-[attr(data-placeholder)] before:text-muted-foreground before:float-left before:h-0 before:pointer-events-none",
			}),
		],
		content,
		contentType: "markdown",
		editable: !readOnly,
		onUpdate: ({ editor }) => {
			onChange?.(editor.getMarkdown());
		},
			editorProps: {
			attributes: {
				role: "textbox",
				"aria-multiline": "true",
				"aria-readonly": String(readOnly),
				class: cn(
					"tiptap-editor min-h-[200px] h-full overflow-y-auto p-3 focus:outline-none prose prose-sm dark:prose-invert max-w-none",
					editorClassName,
				),
				...(ariaLabel ? { "aria-label": ariaLabel } : {}),
				...(id ? { id } : {}),
				...(ariaDescribedBy
					? { "aria-describedby": ariaDescribedBy }
					: {}),
				...(ariaInvalid !== undefined
					? { "aria-invalid": String(ariaInvalid) }
					: {}),
			},
		},
	});

	// Sync editable state when readOnly changes
	useEffect(() => {
		if (editor) {
			editor.setEditable(!readOnly);
		}
	}, [editor, readOnly]);

	// Sync content when it changes externally
	useEffect(() => {
		if (editor && content !== editor.getMarkdown()) {
			editor.commands.setContent(content, { contentType: "markdown" });
		}
	}, [content, editor]);

	if (!editor) {
		return (
			<div className="border rounded-md min-h-[200px] animate-pulse bg-muted/50" />
		);
	}

	return (
		<div
			className={cn(
				"border rounded-md overflow-hidden flex flex-col",
				className,
			)}
		>
			{!readOnly && (
				<div className="shrink-0">
					<TiptapToolbar editor={editor} />
				</div>
			)}
			<div className="flex-1 min-h-0">
				<EditorContent
					editor={editor}
					className="h-full [&_.tiptap]:h-full"
				/>
			</div>
		</div>
	);
}
