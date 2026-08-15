import {
	useCallback,
	useEffect,
	useRef,
	useState,
	type ChangeEvent,
	type ClipboardEvent,
	type DragEvent,
	type KeyboardEvent,
} from "react";
import {
	ArrowUp,
	Bot,
	FileText,
	Loader2,
	Paperclip,
	Square,
	X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";
import {
	MAX_ATTACHMENTS_PER_MESSAGE,
	isImageAttachment,
	validateAttachment,
} from "@/services/chatAttachments";
import type {
	ChatModelTierId,
	ChatModelTierOption,
} from "@/services/chatModels";
import { MentionPicker } from "./MentionPicker";

type AgentSummary = components["schemas"]["AgentSummary"];
interface MentionChip {
	name: string;
}

interface AttachmentDraft {
	file: File;
	previewUrl: string | null;
}

interface ChatInputProps {
	onSend: (
		message: string,
		files: File[],
		modelTier: ChatModelTierId,
	) => void | Promise<void>;
	disabled?: boolean;
	isLoading?: boolean;
	placeholder?: string;
	onStop?: () => void;
	modelTiers?: ChatModelTierOption[];
	modelTier?: ChatModelTierId;
	onModelTierChange?: (tier: ChatModelTierId) => void;
}

export function ChatInput({
	onSend,
	disabled = false,
	isLoading = false,
	placeholder = "Reply…",
	onStop,
	modelTiers = [{ id: "balanced", label: "Balanced" }],
	modelTier = "balanced",
	onModelTierChange,
}: ChatInputProps) {
	const [message, setMessage] = useState("");
	const [mentions, setMentions] = useState<MentionChip[]>([]);
	const [attachments, setAttachments] = useState<AttachmentDraft[]>([]);
	const [isSubmitting, setIsSubmitting] = useState(false);
	const [isDragging, setIsDragging] = useState(false);
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const fileInputRef = useRef<HTMLInputElement>(null);
	const attachmentsRef = useRef(attachments);

	const [mentionOpen, setMentionOpen] = useState(false);
	const [mentionSearch, setMentionSearch] = useState("");
	const [mentionStart, setMentionStart] = useState<number | null>(null);

	useEffect(() => {
		attachmentsRef.current = attachments;
	}, [attachments]);

	useEffect(
		() => () => {
			for (const draft of attachmentsRef.current) {
				if (draft.previewUrl) URL.revokeObjectURL(draft.previewUrl);
			}
		},
		[],
	);

	const addFiles = useCallback((files: File[]) => {
		setAttachments((current) => {
			const slots = MAX_ATTACHMENTS_PER_MESSAGE - current.length;
			if (slots <= 0) {
				toast.error("You can attach up to 5 files per message.");
				return current;
			}
			const accepted: AttachmentDraft[] = [];
			for (const file of files.slice(0, slots)) {
				const error = validateAttachment(file);
				if (error) {
					toast.error(error);
					continue;
				}
				accepted.push({
					file,
					previewUrl: isImageAttachment(file.type)
						? URL.createObjectURL(file)
						: null,
				});
			}
			return [...current, ...accepted];
		});
	}, []);

	const removeAttachment = useCallback((index: number) => {
		setAttachments((current) => {
			const draft = current[index];
			if (draft?.previewUrl) URL.revokeObjectURL(draft.previewUrl);
			return current.filter((_, draftIndex) => draftIndex !== index);
		});
	}, []);

	const handleSend = useCallback(async () => {
		const trimmedMessage = message.trim();
		if (!trimmedMessage && mentions.length === 0 && attachments.length === 0) {
			return;
		}
		if (disabled || isLoading || isSubmitting) return;

		const mentionPrefixes = mentions.map((mention) => `@[${mention.name}]`).join(" ");
		const finalMessage = mentionPrefixes
			? `${mentionPrefixes} ${trimmedMessage}`.trim()
			: trimmedMessage;
		setIsSubmitting(true);
		try {
			await onSend(
				finalMessage,
				attachments.map((draft) => draft.file),
				modelTier,
			);
			for (const draft of attachments) {
				if (draft.previewUrl) URL.revokeObjectURL(draft.previewUrl);
			}
			setMessage("");
			setMentions([]);
			setAttachments([]);
			if (textareaRef.current) textareaRef.current.style.height = "auto";
		} finally {
			setIsSubmitting(false);
		}
	}, [
		attachments,
		disabled,
		isLoading,
		isSubmitting,
		mentions,
		message,
		modelTier,
		onSend,
	]);

	const handleKeyDown = useCallback(
		(event: KeyboardEvent<HTMLTextAreaElement>) => {
			if (mentionOpen && ["ArrowUp", "ArrowDown", "Enter", "Escape"].includes(event.key)) {
				return;
			}
			if (event.key === "Enter" && !event.shiftKey && !mentionOpen) {
				event.preventDefault();
				void handleSend();
			}
		},
		[handleSend, mentionOpen],
	);

	const handleInputChange = useCallback((event: ChangeEvent<HTMLTextAreaElement>) => {
		const value = event.target.value;
		const cursor = event.target.selectionStart;
		setMessage(value);
		const beforeCursor = value.slice(0, cursor);
		const at = beforeCursor.lastIndexOf("@");
		if (at >= 0 && (at === 0 || /\s/.test(value[at - 1]))) {
			const search = beforeCursor.slice(at + 1);
			if (!search.includes(" ")) {
				setMentionSearch(search);
				setMentionStart(at);
				setMentionOpen(true);
				return;
			}
		}
		setMentionOpen(false);
		setMentionStart(null);
	}, []);

	const handleMentionSelect = useCallback(
		(agent: AgentSummary) => {
			if (mentionStart === null) return;
			const before = message.slice(0, mentionStart);
			const after = message.slice(mentionStart + 1 + mentionSearch.length);
			setMessage(`${before}${after}`.trim());
			setMentions((current) =>
				current.some((mention) => mention.name === agent.name)
					? current
					: [...current, { name: agent.name }],
			);
			setMentionOpen(false);
			setMentionStart(null);
			setMentionSearch("");
			textareaRef.current?.focus();
		},
		[mentionSearch.length, mentionStart, message],
	);

	const handlePaste = useCallback(
		(event: ClipboardEvent<HTMLTextAreaElement>) => {
			const files = Array.from(event.clipboardData.items)
				.filter((item) => item.kind === "file")
				.map((item) => item.getAsFile())
				.filter((file): file is File => file !== null);
			if (files.length) {
				event.preventDefault();
				addFiles(files);
			}
		},
		[addFiles],
	);

	const handleDrop = useCallback(
		(event: DragEvent<HTMLDivElement>) => {
			event.preventDefault();
			setIsDragging(false);
			addFiles(Array.from(event.dataTransfer.files));
		},
		[addFiles],
	);

	useEffect(() => {
		const textarea = textareaRef.current;
		if (!textarea) return;
		textarea.style.height = "auto";
		textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
	}, [message]);

	const busy = disabled || isLoading || isSubmitting;
	const canSend =
		(message.trim().length > 0 || mentions.length > 0 || attachments.length > 0) &&
		!busy;

	return (
		<div className="px-3 pb-3 pt-2 sm:px-4 sm:pb-4">
			<div className="mx-auto max-w-3xl">
				<div
					onDrop={handleDrop}
					onDragOver={(event) => {
						event.preventDefault();
						setIsDragging(true);
					}}
					onDragLeave={() => setIsDragging(false)}
					className={cn(
						"relative rounded-2xl border bg-background shadow-sm transition-colors",
						"focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20",
						isDragging && "border-primary bg-primary/5",
					)}
				>
					<MentionPicker
						open={mentionOpen}
						onOpenChange={setMentionOpen}
						onSelect={handleMentionSelect}
						searchTerm={mentionSearch}
						position={{ x: 16, y: 0 }}
					/>

					{attachments.length > 0 && (
						<div className="flex gap-2 overflow-x-auto px-3 pt-3">
							{attachments.map((draft, index) => (
								<div
									key={`${draft.file.name}-${draft.file.lastModified}-${index}`}
									className="group relative flex h-16 min-w-40 max-w-56 items-center gap-2 rounded-xl border bg-muted/40 p-2"
								>
									{draft.previewUrl ? (
										<img
											src={draft.previewUrl}
											alt=""
											className="h-12 w-12 rounded-lg object-cover"
										/>
									) : (
										<div className="flex h-12 w-12 items-center justify-center rounded-lg bg-background">
											<FileText className="h-5 w-5 text-muted-foreground" />
										</div>
									)}
									<span className="truncate text-xs font-medium">{draft.file.name}</span>
									<Button
										type="button"
										variant="secondary"
										size="icon-sm"
										aria-label={`Remove ${draft.file.name}`}
										className="absolute -right-1.5 -top-1.5 h-6 w-6 rounded-full"
										onClick={() => removeAttachment(index)}
									>
										<X className="h-3 w-3" />
									</Button>
								</div>
							))}
						</div>
					)}

					{mentions.length > 0 && (
						<div className="flex flex-wrap gap-1.5 px-3 pt-3">
							{mentions.map((mention) => (
								<span
									key={mention.name}
									className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-1 text-xs font-medium text-primary"
								>
									<Bot className="h-3 w-3" />
									{mention.name}
									<button
										type="button"
										aria-label={`Remove ${mention.name}`}
										onClick={() =>
											setMentions((current) =>
												current.filter((item) => item.name !== mention.name),
											)
										}
									>
										<X className="h-3 w-3" />
									</button>
								</span>
							))}
						</div>
					)}

					<textarea
						ref={textareaRef}
						aria-label="Chat input"
						value={message}
						onChange={handleInputChange}
						onKeyDown={handleKeyDown}
						onPaste={handlePaste}
						placeholder={placeholder}
						disabled={disabled}
						rows={1}
						className="max-h-[200px] min-h-12 w-full resize-none bg-transparent px-4 py-3 text-base outline-none placeholder:text-muted-foreground disabled:opacity-50"
					/>

					<div className="flex items-center justify-between gap-2 px-2.5 pb-2.5">
						<div className="flex min-w-0 items-center gap-1">
							<input
								ref={fileInputRef}
								type="file"
								multiple
								className="hidden"
								accept="image/png,image/jpeg,image/webp,image/gif,application/pdf,text/*,application/json,application/csv"
								onChange={(event) => {
									addFiles(Array.from(event.target.files ?? []));
									event.target.value = "";
								}}
							/>
							<Button
								type="button"
								variant="ghost"
								size="icon-sm"
								aria-label="Attach files"
								title="Attach files"
								disabled={busy || attachments.length >= MAX_ATTACHMENTS_PER_MESSAGE}
								onClick={() => fileInputRef.current?.click()}
							>
								<Paperclip className="h-4 w-4" />
							</Button>
							{modelTiers.length > 0 && (
								<Select
									value={modelTier}
									onValueChange={(value) =>
										onModelTierChange?.(value as ChatModelTierId)
									}
									disabled={busy}
								>
									<SelectTrigger
										aria-label="Response model"
										className="h-8 w-auto min-w-24 border-0 bg-transparent px-2 text-xs shadow-none"
									>
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{modelTiers.map((tier) => (
											<SelectItem key={tier.id} value={tier.id}>
												{tier.label}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							)}
						</div>

						{isLoading && onStop ? (
							<Button
								onClick={onStop}
								size="icon-sm"
								variant="destructive"
								aria-label="Stop generation"
								title="Stop generation"
								className="rounded-full"
							>
								<Square className="h-3 w-3 fill-current" />
							</Button>
						) : (
							<Button
								onClick={() => void handleSend()}
								disabled={!canSend}
								size="icon-sm"
								aria-label="Send message"
								className="rounded-full"
							>
								{isSubmitting ? (
									<Loader2 className="h-4 w-4 animate-spin" />
								) : (
									<ArrowUp className="h-4 w-4" />
								)}
							</Button>
						)}
					</div>
				</div>
				<p className="mt-2 text-center text-[11px] text-muted-foreground">
					AI can make mistakes. Check important results.
				</p>
			</div>
		</div>
	);
}
