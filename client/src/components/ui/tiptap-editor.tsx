import { useEffect, useId, useRef, useState } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import { Markdown } from "@tiptap/markdown";
import { TiptapToolbar } from "./tiptap-toolbar";
import { cn } from "@/lib/utils";
import { ToggleGroup, ToggleGroupItem } from "./toggle-group";
import { Textarea } from "./textarea";
import { canEditMarkdownVisually } from "./tiptap-markdown";

interface TiptapEditorProps {
	content: string;
	onChange?: (content: string) => void;
	readOnly?: boolean;
	placeholder?: string;
	className?: string;
	editorClassName?: string;
	ariaLabel?: string;
	id?: string;
	onBlur?: () => void;
	"aria-describedby"?: string;
	"aria-invalid"?: boolean | "true" | "false";
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
	onBlur,
	"aria-describedby": describedBy,
	"aria-invalid": invalid,
}: TiptapEditorProps) {
	const [mode, setMode] = useState("visual");
	const [source, setSource] = useState(content);
	const sourceRef = useRef(content);
	const incomingContentRef = useRef(content);
	const visualSourceRef = useRef<string | undefined>(undefined);
	const [visualProtected, setVisualProtected] = useState(false);
	const noticeId = useId();
	const editor = useEditor({
		// Create the instance after commit, including when a lazy consumer suspends.
		immediatelyRender: false,
		extensions: [
			StarterKit.configure({
				link: false,
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
		onBlur: () => onBlur?.(),
		onUpdate: ({ editor }) => {
			if (readOnly || mode !== "visual" || visualProtected) return;
			const markdown = editor.getMarkdown();
			sourceRef.current = markdown;
			visualSourceRef.current = markdown;
			setSource(markdown);
			onChange?.(markdown);
		},
		editorProps: {
			attributes: {
				role: "textbox",
				...(id ? { id } : {}),
				"aria-readonly": String(readOnly || visualProtected),
				"aria-describedby": [
					describedBy,
					visualProtected && !readOnly ? noticeId : null,
				]
					.filter(Boolean)
					.join(" "),
				...(invalid !== undefined
					? { "aria-invalid": String(invalid) }
					: {}),
				"aria-multiline": "true",
				class: cn(
					"tiptap-editor min-h-[200px] p-3 focus:outline-none prose prose-sm dark:prose-invert max-w-none",
					editorClassName,
				),
				...(ariaLabel ? { "aria-label": ariaLabel } : {}),
			},
		},
	});

	// Only real content changes update the caller. Loading source into Tiptap or
	// changing permissions must never normalize the parent's Markdown draft.
	useEffect(() => {
		if (content !== incomingContentRef.current) {
			incomingContentRef.current = content;
			sourceRef.current = content;
			setSource(content);
		}
		if (
			!editor ||
			editor.isDestroyed ||
			mode !== "visual" ||
			sourceRef.current === visualSourceRef.current
		)
			return;
		editor.commands.setContent(sourceRef.current, {
			contentType: "markdown",
			emitUpdate: false,
		});
		visualSourceRef.current = sourceRef.current;
		setVisualProtected(
			!canEditMarkdownVisually(
				editor.markdown!,
				sourceRef.current,
				editor.getMarkdown(),
			),
		);
	}, [content, editor, mode]);

	useEffect(() => {
		if (!editor || editor.isDestroyed) return;
		editor.setEditable(
			!readOnly && mode === "visual" && !visualProtected,
			false,
		);
	}, [editor, readOnly, mode, visualProtected]);

	if (!editor) {
		return (
			<div className="min-h-[200px] animate-pulse motion-reduce:animate-none rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/50" />
		);
	}

	return (
		<div
			className={cn(
				"flex flex-col overflow-hidden rounded-[var(--bf-radius-surface)] border border-border/70 bg-card",
				className,
			)}
		>
			{!readOnly && (
				<div className="shrink-0">
					<ToggleGroup
						type="single"
						value={mode}
						onValueChange={(value) => value && setMode(value)}
						aria-label="Editor mode"
						className="justify-start border-b border-border/70 bg-muted/30 p-2"
					>
						<ToggleGroupItem
							value="visual"
							className="min-h-11 px-3"
						>
							Visual
						</ToggleGroupItem>
						<ToggleGroupItem
							value="markdown"
							className="min-h-11 px-3"
						>
							Markdown
						</ToggleGroupItem>
					</ToggleGroup>
					{mode === "visual" && !visualProtected && (
						<TiptapToolbar editor={editor} />
					)}
					{mode === "visual" && visualProtected && (
						<p
							id={noticeId}
							role="status"
							className="border-b border-border/70 p-3 text-sm text-muted-foreground"
						>
							This content includes Markdown that Visual mode
							cannot preserve. Edit in Markdown mode to keep all
							content.
						</p>
					)}
				</div>
			)}
			<div className="min-h-0 overflow-y-auto">
				{mode === "markdown" ? (
					<Textarea
						id={id}
						aria-label={ariaLabel ?? "Markdown source"}
						aria-describedby={describedBy}
						aria-invalid={invalid}
						readOnly={readOnly}
						value={source}
						placeholder={placeholder}
						onBlur={onBlur}
						onChange={(event) => {
							const markdown = event.target.value;
							sourceRef.current = markdown;
							setSource(markdown);
							onChange?.(markdown);
						}}
						className={cn(
							"min-h-[200px] rounded-none border-0 bg-transparent p-3 font-mono shadow-none focus-visible:ring-inset",
							editorClassName,
						)}
					/>
				) : (
					<EditorContent editor={editor} />
				)}
			</div>
		</div>
	);
}
