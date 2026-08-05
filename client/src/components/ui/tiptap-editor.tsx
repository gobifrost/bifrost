import { useEffect } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
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
	ariaLabel?: string;
}

export function TiptapEditor({
	content,
	onChange,
	readOnly = false,
	placeholder = "Start writing...",
	className,
	ariaLabel,
}: TiptapEditorProps) {
	const editor = useEditor({
		extensions: [
			StarterKit.configure({
				heading: {
					levels: [2, 3],
				},
			}),
			Markdown,
			Link.configure({
				openOnClick: false,
				HTMLAttributes: {
					class: "text-primary underline",
				},
			}),
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
				class: "tiptap-editor min-h-[200px] h-full overflow-y-auto p-3 focus:outline-none prose prose-sm dark:prose-invert max-w-none",
				...(ariaLabel ? { "aria-label": ariaLabel } : {}),
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
				<EditorContent editor={editor} className="h-full [&_.tiptap]:h-full" />
			</div>
		</div>
	);
}
