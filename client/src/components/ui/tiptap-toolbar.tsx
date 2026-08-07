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
		const url = window.prompt("Enter URL:");
		if (url) {
			editor.chain().focus().setLink({ href: url }).run();
		}
	};

	return (
		<div className="flex flex-wrap items-center gap-1 p-2 border-b bg-muted/30">
			{/* Undo/Redo */}
			<Button
				type="button"
				variant="ghost"
				size="sm"
				onClick={() => editor.chain().focus().undo().run()}
				disabled={!editor.can().undo()}
			>
				<Undo className="h-4 w-4" />
			</Button>
			<Button
				type="button"
				variant="ghost"
				size="sm"
				onClick={() => editor.chain().focus().redo().run()}
				disabled={!editor.can().redo()}
			>
				<Redo className="h-4 w-4" />
			</Button>

			<Separator orientation="vertical" className="h-6 mx-1" />

			{/* Headings */}
			<Toggle
				size="sm"
				pressed={editor.isActive("heading", { level: 2 })}
				onPressedChange={() =>
					editor.chain().focus().toggleHeading({ level: 2 }).run()
				}
			>
				<Heading2 className="h-4 w-4" />
			</Toggle>
			<Toggle
				size="sm"
				pressed={editor.isActive("heading", { level: 3 })}
				onPressedChange={() =>
					editor.chain().focus().toggleHeading({ level: 3 }).run()
				}
			>
				<Heading3 className="h-4 w-4" />
			</Toggle>

			<Separator orientation="vertical" className="h-6 mx-1" />

			{/* Text formatting */}
			<Toggle
				size="sm"
				pressed={editor.isActive("bold")}
				onPressedChange={() => editor.chain().focus().toggleBold().run()}
			>
				<Bold className="h-4 w-4" />
			</Toggle>
			<Toggle
				size="sm"
				pressed={editor.isActive("italic")}
				onPressedChange={() =>
					editor.chain().focus().toggleItalic().run()
				}
			>
				<Italic className="h-4 w-4" />
			</Toggle>
			<Toggle
				size="sm"
				pressed={editor.isActive("strike")}
				onPressedChange={() =>
					editor.chain().focus().toggleStrike().run()
				}
			>
				<Strikethrough className="h-4 w-4" />
			</Toggle>
			<Toggle
				size="sm"
				pressed={editor.isActive("code")}
				onPressedChange={() => editor.chain().focus().toggleCode().run()}
			>
				<Code className="h-4 w-4" />
			</Toggle>

			<Separator orientation="vertical" className="h-6 mx-1" />

			{/* Link */}
			<Toggle
				size="sm"
				pressed={editor.isActive("link")}
				onPressedChange={handleLinkClick}
			>
				<LinkIcon className="h-4 w-4" />
			</Toggle>

			<Separator orientation="vertical" className="h-6 mx-1" />

			{/* Lists */}
			<Toggle
				size="sm"
				pressed={editor.isActive("bulletList")}
				onPressedChange={() =>
					editor.chain().focus().toggleBulletList().run()
				}
			>
				<List className="h-4 w-4" />
			</Toggle>
			<Toggle
				size="sm"
				pressed={editor.isActive("orderedList")}
				onPressedChange={() =>
					editor.chain().focus().toggleOrderedList().run()
				}
			>
				<ListOrdered className="h-4 w-4" />
			</Toggle>

			<Separator orientation="vertical" className="h-6 mx-1" />

			{/* Block formatting */}
			<Toggle
				size="sm"
				pressed={editor.isActive("blockquote")}
				onPressedChange={() =>
					editor.chain().focus().toggleBlockquote().run()
				}
			>
				<Quote className="h-4 w-4" />
			</Toggle>
		</div>
	);
}
