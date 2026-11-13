import type { Editor } from "@tiptap/react";
import { Toggle } from "@/components/ui/toggle";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
	Bold,
	Italic,
	Strikethrough,
	Code,
	Heading2,
	Heading3,
	List,
	ListOrdered,
	Quote,
	Link as LinkIcon,
	Undo,
	Redo,
} from "lucide-react";

interface TiptapToolbarProps {
	editor: Editor;
}

export function TiptapToolbar({ editor }: TiptapToolbarProps) {
	const handleLinkClick = () => {
		const url = window.prompt(
			"Enter URL (leave empty to remove link):",
			editor.getAttributes("link").href ?? "",
		);
		if (url === null) return;
		const chain = editor.chain().focus().extendMarkRange("link");
		if (url === "") {
			chain.unsetLink().run();
		} else {
			chain.setLink({ href: url }).run();
		}
	};

	return (
		<div
			className="flex flex-wrap items-center gap-1.5 border-b border-border/70 bg-muted/30 p-2"
			role="group"
			aria-label="Rich text formatting"
		>
			<div className="flex flex-wrap items-center gap-1.5">
				<Button
					type="button"
					variant="ghost"
					size="icon"
					aria-label="Undo"
					onClick={() => editor.chain().focus().undo().run()}
					disabled={!editor.can().undo()}
					className="h-11 w-11 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<Undo className="h-4 w-4" />
				</Button>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					aria-label="Redo"
					onClick={() => editor.chain().focus().redo().run()}
					disabled={!editor.can().redo()}
					className="h-11 w-11 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<Redo className="h-4 w-4" />
				</Button>
			</div>

			<Separator
				orientation="vertical"
				className="hidden h-6 sm:block"
			/>

			<div className="flex flex-wrap items-center gap-1.5">
				<Toggle
					size="sm"
					pressed={editor.isActive("heading", { level: 2 })}
					onPressedChange={() =>
						editor.chain().focus().toggleHeading({ level: 2 }).run()
					}
					aria-label="Heading level 2"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<Heading2 className="h-4 w-4" />
				</Toggle>
				<Toggle
					size="sm"
					pressed={editor.isActive("heading", { level: 3 })}
					onPressedChange={() =>
						editor.chain().focus().toggleHeading({ level: 3 }).run()
					}
					aria-label="Heading level 3"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<Heading3 className="h-4 w-4" />
				</Toggle>
			</div>

			<Separator
				orientation="vertical"
				className="hidden h-6 sm:block"
			/>

			<div className="flex flex-wrap items-center gap-1.5">
				<Toggle
					size="sm"
					pressed={editor.isActive("bold")}
					onPressedChange={() => editor.chain().focus().toggleBold().run()}
					aria-label="Bold"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<Bold className="h-4 w-4" />
				</Toggle>
				<Toggle
					size="sm"
					pressed={editor.isActive("italic")}
					onPressedChange={() =>
						editor.chain().focus().toggleItalic().run()
					}
					aria-label="Italic"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<Italic className="h-4 w-4" />
				</Toggle>
				<Toggle
					size="sm"
					pressed={editor.isActive("strike")}
					onPressedChange={() =>
						editor.chain().focus().toggleStrike().run()
					}
					aria-label="Strikethrough"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<Strikethrough className="h-4 w-4" />
				</Toggle>
				<Toggle
					size="sm"
					pressed={editor.isActive("code")}
					onPressedChange={() => editor.chain().focus().toggleCode().run()}
					aria-label="Code"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<Code className="h-4 w-4" />
				</Toggle>
			</div>

			<Separator
				orientation="vertical"
				className="hidden h-6 sm:block"
			/>

			<div className="flex flex-wrap items-center gap-1.5">
				<Toggle
					size="sm"
					pressed={editor.isActive("link")}
					onPressedChange={handleLinkClick}
					aria-label="Insert link"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<LinkIcon className="h-4 w-4" />
				</Toggle>
			</div>

			<Separator
				orientation="vertical"
				className="hidden h-6 sm:block"
			/>

			<div className="flex flex-wrap items-center gap-1.5">
				<Toggle
					size="sm"
					pressed={editor.isActive("bulletList")}
					onPressedChange={() =>
						editor.chain().focus().toggleBulletList().run()
					}
					aria-label="Bullet list"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<List className="h-4 w-4" />
				</Toggle>
				<Toggle
					size="sm"
					pressed={editor.isActive("orderedList")}
					onPressedChange={() =>
						editor.chain().focus().toggleOrderedList().run()
					}
					aria-label="Numbered list"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<ListOrdered className="h-4 w-4" />
				</Toggle>
			</div>

			<Separator
				orientation="vertical"
				className="hidden h-6 sm:block"
			/>

			<div className="flex flex-wrap items-center gap-1.5">
				<Toggle
					size="sm"
					pressed={editor.isActive("blockquote")}
					onPressedChange={() =>
						editor.chain().focus().toggleBlockquote().run()
					}
					aria-label="Block quote"
					className="h-11 w-11 px-0 duration-[var(--bf-motion-feedback)] motion-reduce:transition-none sm:h-10 sm:w-10"
				>
					<Quote className="h-4 w-4" />
				</Toggle>
			</div>
		</div>
	);
}
