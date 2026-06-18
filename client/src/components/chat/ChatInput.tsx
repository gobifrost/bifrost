/**
 * ChatInput Component
 *
 * Modern floating chat input with send button inside.
 * Supports Enter to send, auto-resize textarea, and @mention agent switching.
 */

import { useState, useRef, useCallback, useEffect } from "react";
import {
	ArrowUp,
	Bot,
	FileImage,
	FileSpreadsheet,
	FileText,
	Loader2,
	Paperclip,
	Square,
	X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { MentionPicker } from "./MentionPicker";
import { ModelPicker } from "./ModelPicker";
import {
	MAX_ATTACHMENTS_PER_MESSAGE,
	isImageAttachment,
	uploadChatAttachments,
	validateAttachment,
	type AttachmentPublic,
} from "@/services/chatAttachments";
import type { components } from "@/lib/v1";

type AgentSummary = components["schemas"]["AgentSummary"];

interface MentionChip {
	name: string;
	position: number; // Where in the message this mention starts
}

interface ChatInputProps {
	onSend: (message: string, attachmentIds?: string[]) => void;
	disabled?: boolean;
	isLoading?: boolean;
	placeholder?: string;
	onStop?: () => void;
	/** Conversation to upload attachments to. When absent (new chat before
	 *  the conversation exists) the attach affordance is disabled. */
	conversationId?: string;
	/** Currently-selected model for this conversation. The picker uses
	 *  the resolved default when this is null. */
	selectedModel?: string | null;
	/** Called when the user picks a different model. Also fires before
	 *  the first message in a new chat — the caller should buffer the pick
	 *  and apply it on conversation create. */
	onSelectModel?: (modelId: string) => void;
}

function attachmentIcon(contentType: string) {
	if (isImageAttachment(contentType)) return FileImage;
	if (contentType === "text/csv" || contentType === "application/csv")
		return FileSpreadsheet;
	return FileText;
}

export function ChatInput({
	onSend,
	disabled = false,
	isLoading = false,
	placeholder = "Reply...",
	onStop,
	conversationId,
	selectedModel,
	onSelectModel,
}: ChatInputProps) {
	const [message, setMessage] = useState("");
	const [mentions, setMentions] = useState<MentionChip[]>([]);
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const containerRef = useRef<HTMLDivElement>(null);
	const fileInputRef = useRef<HTMLInputElement>(null);

	// Attachment state
	const [attachments, setAttachments] = useState<AttachmentPublic[]>([]);
	const [isUploading, setIsUploading] = useState(false);
	const [isDragging, setIsDragging] = useState(false);

	// Mention picker state
	const [mentionOpen, setMentionOpen] = useState(false);
	const [mentionSearch, setMentionSearch] = useState("");
	const [mentionPosition, setMentionPosition] = useState({ x: 0, y: 0 });
	const [mentionStart, setMentionStart] = useState<number | null>(null);

	const attachEnabled = !!conversationId && !disabled;

	const uploadFiles = useCallback(
		async (files: File[]) => {
			if (!conversationId || files.length === 0) return;
			if (attachments.length + files.length > MAX_ATTACHMENTS_PER_MESSAGE) {
				toast.error(
					`You can attach at most ${MAX_ATTACHMENTS_PER_MESSAGE} files per message.`,
				);
				return;
			}
			for (const f of files) {
				const err = validateAttachment(f);
				if (err) {
					toast.error(err);
					return;
				}
			}
			setIsUploading(true);
			try {
				const res = await uploadChatAttachments(conversationId, files);
				setAttachments((prev) => [...prev, ...res.attachments]);
			} catch (e) {
				toast.error(
					e instanceof Error ? e.message : "Failed to upload attachment",
				);
			} finally {
				setIsUploading(false);
			}
		},
		[conversationId, attachments.length],
	);

	const handleRemoveAttachment = useCallback((id: string) => {
		setAttachments((prev) => prev.filter((a) => a.id !== id));
	}, []);

	const handleSend = useCallback(() => {
		const trimmedMessage = message.trim();
		if (!trimmedMessage && mentions.length === 0) return;
		if (disabled || isLoading || isUploading) return;

		// Build final message with mentions prepended
		const mentionPrefixes = mentions.map((m) => `@[${m.name}]`).join(" ");
		const finalMessage = mentionPrefixes
			? `${mentionPrefixes} ${trimmedMessage}`.trim()
			: trimmedMessage;

		const attachmentIds = attachments.map((a) => a.id);
		onSend(finalMessage, attachmentIds.length > 0 ? attachmentIds : undefined);
		setMessage("");
		setMentions([]);
		setAttachments([]);

		// Reset textarea height
		if (textareaRef.current) {
			textareaRef.current.style.height = "auto";
		}
	}, [message, mentions, attachments, disabled, isLoading, isUploading, onSend]);

	const handleKeyDown = useCallback(
		(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
			// If mention picker is open, let it handle navigation
			if (mentionOpen) {
				if (
					["ArrowUp", "ArrowDown", "Enter", "Escape"].includes(e.key)
				) {
					// These are handled by MentionPicker
					return;
				}
			}

			// Send on Enter (without Shift) when mention picker is closed
			if (e.key === "Enter" && !e.shiftKey && !mentionOpen) {
				e.preventDefault();
				handleSend();
			}
		},
		[handleSend, mentionOpen],
	);

	// Paste an image from the clipboard → upload as an attachment (§16.8).
	const handlePaste = useCallback(
		(e: React.ClipboardEvent<HTMLTextAreaElement>) => {
			if (!attachEnabled) return;
			const imageFiles: File[] = [];
			for (const item of Array.from(e.clipboardData.items)) {
				if (item.kind === "file" && item.type.startsWith("image/")) {
					const file = item.getAsFile();
					if (file) {
						const ext = file.type.split("/")[1] || "png";
						const stamp = new Date()
							.toISOString()
							.slice(0, 16)
							.replace(/[-:T]/g, "")
							.replace(/(\d{8})(\d{4})/, "$1-$2");
						imageFiles.push(
							new File([file], `screenshot-${stamp}.${ext}`, {
								type: file.type,
							}),
						);
					}
				}
			}
			if (imageFiles.length > 0) {
				e.preventDefault();
				void uploadFiles(imageFiles);
			}
		},
		[attachEnabled, uploadFiles],
	);

	const handleDrop = useCallback(
		(e: React.DragEvent) => {
			e.preventDefault();
			setIsDragging(false);
			if (!attachEnabled) return;
			const files = Array.from(e.dataTransfer.files);
			if (files.length > 0) void uploadFiles(files);
		},
		[attachEnabled, uploadFiles],
	);

	const handleDragOver = useCallback(
		(e: React.DragEvent) => {
			if (!attachEnabled) return;
			e.preventDefault();
			setIsDragging(true);
		},
		[attachEnabled],
	);

	const handleDragLeave = useCallback((e: React.DragEvent) => {
		e.preventDefault();
		setIsDragging(false);
	}, []);

	// Detect @ mentions while typing
	const handleInputChange = useCallback(
		(e: React.ChangeEvent<HTMLTextAreaElement>) => {
			const value = e.target.value;
			const cursorPos = e.target.selectionStart;
			setMessage(value);

			// Find @ before cursor
			const textBeforeCursor = value.slice(0, cursorPos);
			const lastAtIndex = textBeforeCursor.lastIndexOf("@");

			if (lastAtIndex !== -1) {
				// Check if @ is at start or preceded by whitespace
				const charBefore =
					lastAtIndex > 0 ? value[lastAtIndex - 1] : " ";
				if (/\s/.test(charBefore) || lastAtIndex === 0) {
					const searchText = textBeforeCursor.slice(lastAtIndex + 1);
					// Check if there's no space in the search text (would close mention)
					if (!searchText.includes(" ")) {
						setMentionSearch(searchText);
						setMentionStart(lastAtIndex);
						setMentionOpen(true);

						// Position for mention picker (above the textarea)
						setMentionPosition({ x: 16, y: 0 });
						return;
					}
				}
			}

			// Close mention picker if no valid @ mention
			setMentionOpen(false);
			setMentionStart(null);
		},
		[],
	);

	// Handle agent selection from mention picker
	const handleMentionSelect = useCallback(
		(agent: AgentSummary) => {
			if (mentionStart === null) return;

			// Remove the @search from message text (mention will show as chip)
			const beforeMention = message.slice(0, mentionStart);
			const afterCursor = message.slice(
				mentionStart + 1 + mentionSearch.length,
			);
			const newMessage = `${beforeMention}${afterCursor}`.trim();

			// Add mention as a chip (avoid duplicates)
			setMentions((prev) => {
				if (prev.some((m) => m.name === agent.name)) {
					return prev;
				}
				return [
					...prev,
					{
						name: agent.name,
						position: mentionStart,
					},
				];
			});

			setMessage(newMessage);
			setMentionOpen(false);
			setMentionStart(null);
			setMentionSearch("");

			// Focus back on textarea
			if (textareaRef.current) {
				textareaRef.current.focus();
				// Move cursor to where the @ was
				const newCursorPos = beforeMention.length;
				setTimeout(() => {
					textareaRef.current?.setSelectionRange(
						newCursorPos,
						newCursorPos,
					);
				}, 0);
			}
		},
		[message, mentionStart, mentionSearch],
	);

	// Remove a mention chip
	const handleRemoveMention = useCallback((name: string) => {
		setMentions((prev) => prev.filter((m) => m.name !== name));
	}, []);

	// Auto-resize textarea
	useEffect(() => {
		const textarea = textareaRef.current;
		if (!textarea) return;

		textarea.style.height = "auto";
		textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
	}, [message]);

	const canSend =
		(message.trim().length > 0 || mentions.length > 0) &&
		!disabled &&
		!isLoading &&
		!isUploading;

	return (
		<div className="p-4 pt-2">
			<div className="max-w-4xl mx-auto">
				{/* Floating input container */}
				<div
					ref={containerRef}
					onDrop={handleDrop}
					onDragOver={handleDragOver}
					onDragLeave={handleDragLeave}
					className={cn(
						"relative rounded-2xl bg-muted/50 shadow-lg ring-1 ring-foreground/5 dark:ring-foreground/10",
						"transition-all duration-200",
						"focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background",
						isDragging && "ring-2 ring-primary",
					)}
				>
					{/* Drag-and-drop overlay (§16.8) */}
					{isDragging && (
						<div className="absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-primary/10 backdrop-blur-sm pointer-events-none">
							<div className="flex items-center gap-2 text-sm font-medium text-primary">
								<Paperclip className="h-4 w-4" />
								Drop files to attach
							</div>
						</div>
					)}
					{/* Mention picker */}
					<MentionPicker
						open={mentionOpen}
						onOpenChange={setMentionOpen}
						onSelect={handleMentionSelect}
						searchTerm={mentionSearch}
						position={mentionPosition}
					/>

					{/* Top row: mention chips + textarea */}
					<div className="px-4 pt-3 pb-2">
						{/* Mention chips */}
						{mentions.length > 0 && (
							<div className="flex flex-wrap gap-1.5 mb-2">
								{mentions.map((mention) => (
									<span
										key={mention.name}
										className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-full bg-primary/15 text-primary text-sm font-medium"
									>
										<Bot className="h-3 w-3 shrink-0" />
										{mention.name}
										<button
											type="button"
											onClick={() =>
												handleRemoveMention(
													mention.name,
												)
											}
											className="ml-0.5 p-0.5 rounded-full hover:bg-primary/20 transition-colors"
											aria-label={`Remove ${mention.name}`}
										>
											<X className="h-3 w-3" />
										</button>
									</span>
								))}
							</div>
						)}
						{/* Attachment chips (§16.8) */}
						{attachments.length > 0 && (
							<div className="flex flex-wrap gap-1.5 mb-2">
								{attachments.map((att) => {
									const Icon = attachmentIcon(att.content_type);
									return (
										<span
											key={att.id}
											className="inline-flex items-center gap-1 pl-2 pr-1 py-0.5 rounded-md bg-muted text-foreground text-xs"
										>
											<Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
											<span className="max-w-[160px] truncate">
												{att.filename}
											</span>
											<button
												type="button"
												onClick={() =>
													handleRemoveAttachment(att.id)
												}
												className="ml-0.5 p-0.5 rounded-full hover:bg-foreground/10 transition-colors"
												aria-label={`Remove ${att.filename}`}
											>
												<X className="h-3 w-3" />
											</button>
										</span>
									);
								})}
							</div>
						)}
						<textarea
							ref={textareaRef}
							aria-label="Chat input"
							value={message}
							onChange={handleInputChange}
							onKeyDown={handleKeyDown}
							onPaste={handlePaste}
							placeholder={
								mentions.length > 0
									? "Add a message..."
									: placeholder
							}
							disabled={disabled}
							className={cn(
								"w-full bg-transparent resize-none outline-none",
								"text-base placeholder:text-muted-foreground",
								"min-h-[24px] max-h-[200px]",
								"disabled:opacity-50 disabled:cursor-not-allowed",
							)}
							rows={1}
						/>
					</div>

					{/* Bottom row: actions and send */}
					<div className="flex items-center justify-between px-3 pb-3">
						{/* Left side actions */}
						<div className="flex items-center gap-1">
							<input
								ref={fileInputRef}
								type="file"
								multiple
								accept="image/png,image/jpeg,image/webp,image/gif,application/pdf,text/csv,text/plain,text/markdown,application/json,.txt,.md,.json,.yaml,.yml,.csv"
								className="hidden"
								onChange={(e) => {
									const files = Array.from(e.target.files ?? []);
									if (files.length > 0) void uploadFiles(files);
									e.target.value = "";
								}}
							/>
							<Button
								type="button"
								variant="ghost"
								size="icon"
								onClick={() => fileInputRef.current?.click()}
								className={cn(
									"h-8 w-8 rounded-full",
									attachEnabled
										? "text-muted-foreground hover:text-foreground"
										: "text-muted-foreground/50 cursor-not-allowed",
								)}
								disabled={!attachEnabled || isUploading}
								title={
									attachEnabled
										? "Attach files"
										: "Start a chat to attach files"
								}
								aria-label="Attach files"
							>
								{isUploading ? (
									<Loader2 className="h-4 w-4 animate-spin" />
								) : (
									<Paperclip className="h-4 w-4" />
								)}
							</Button>
							{onSelectModel && (
								<ModelPicker
									value={selectedModel ?? null}
									onChange={onSelectModel}
									disabled={disabled}
								/>
							)}
						</div>

						{/* Right side: stop or send button */}
						{isLoading && onStop ? (
							<Button
								onClick={onStop}
								size="icon"
								variant="destructive"
								aria-label="Stop generation"
								className={cn(
									"h-8 w-8 rounded-full shrink-0",
									"transition-all duration-200",
								)}
								title="Stop generation"
							>
								<Square className="h-3 w-3 fill-current" />
							</Button>
						) : (
							<Button
								onClick={handleSend}
								disabled={!canSend}
								size="icon"
								aria-label="Send message"
								className={cn(
									"h-8 w-8 rounded-full shrink-0",
									"transition-all duration-200",
									canSend
										? "bg-primary text-primary-foreground hover:bg-primary/90"
										: "bg-muted-foreground/20 text-muted-foreground",
								)}
							>
								{isLoading ? (
									<Loader2 className="h-4 w-4 animate-spin" />
								) : (
									<ArrowUp className="h-4 w-4" />
								)}
							</Button>
						)}
					</div>
				</div>

				{/* Disclaimer — provider-neutral (the model may be Claude, GPT, etc.) */}
				<p className="text-center text-xs text-muted-foreground mt-2">
					AI can make mistakes. Please double-check responses.
				</p>
			</div>
		</div>
	);
}
