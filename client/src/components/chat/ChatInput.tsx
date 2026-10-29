import { DraftAttachment } from "./DraftAttachment";
import {
	useCallback,
	useEffect,
	useRef,
	useState,
	type ChangeEvent,
	type ClipboardEvent,
	type CSSProperties,
	type DragEvent,
	type KeyboardEvent,
} from "react";
import { ArrowUp, Bot, Loader2, Paperclip, Square, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { formatContextTokens } from "@/lib/run-usage";
import type { components } from "@/lib/v1";
import {
	MAX_ATTACHMENTS_PER_MESSAGE,
	isImageAttachment,
	validateAttachment,
} from "@/services/chatAttachments";
import type {
	ChatModelProfileId,
	ChatModelProfileOption,
} from "@/services/chatModels";
import { MentionPicker } from "./MentionPicker";

type AgentSummary = components["schemas"]["AgentSummary"];
type ChatContextUsage = components["schemas"]["ChatContextUsage"];
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
		modelProfileId: ChatModelProfileId | null,
	) => void | Promise<void>;
	disabled?: boolean;
	isLoading?: boolean;
	placeholder?: string;
	onStop?: () => void;
	modelProfiles?: ChatModelProfileOption[];
	modelProfileId?: ChatModelProfileId | null;
	onModelProfileChange?: (profileId: ChatModelProfileId) => void;
	contextUsage?: ChatContextUsage | null;
}

export function ChatInput({
	onSend,
	disabled = false,
	isLoading = false,
	placeholder = "Reply…",
	onStop,
	modelProfiles = [],
	modelProfileId = null,
	onModelProfileChange,
	contextUsage = null,
}: ChatInputProps) {
	const [message, setMessage] = useState("");
	const [mentions, setMentions] = useState<MentionChip[]>([]);
	const [attachments, setAttachments] = useState<AttachmentDraft[]>([]);
	const [isSubmitting, setIsSubmitting] = useState(false);
	const [sendError, setSendError] = useState<string | null>(null);
	const [isDragging, setIsDragging] = useState(false);
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const fileInputRef = useRef<HTMLInputElement>(null);
	const attachmentsRef = useRef(attachments);
	const selectedProfile = modelProfiles.find(
		(profile) => profile.id === modelProfileId,
	);
	const selectedCapabilities = selectedProfile?.capabilities;
	const matchingContextUsage =
		contextUsage?.model_profile_id === modelProfileId ? contextUsage : null;
	const [contextOpen, setContextOpen] = useState(false);

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

	const addFiles = useCallback(
		(files: File[]) => {
			setAttachments((current) => {
				const slots = MAX_ATTACHMENTS_PER_MESSAGE - current.length;
				if (slots <= 0) {
					toast.error("You can attach up to 5 files per message.");
					return current;
				}
				const accepted: AttachmentDraft[] = [];
				for (const file of files.slice(0, slots)) {
					if (
						file.type.startsWith("image/") &&
						!selectedCapabilities?.image_input
					) {
						toast.error(
							`${selectedProfile?.label ?? "This profile"} cannot inspect images.`,
						);
						continue;
					}
					if (
						file.type === "application/pdf" &&
						!selectedCapabilities?.pdf_input
					) {
						toast.error(
							`${selectedProfile?.label ?? "This profile"} cannot inspect PDFs.`,
						);
						continue;
					}
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
		},
		[selectedCapabilities, selectedProfile],
	);

	const removeAttachment = useCallback((index: number) => {
		setAttachments((current) => {
			const draft = current[index];
			if (draft?.previewUrl) URL.revokeObjectURL(draft.previewUrl);
			return current.filter((_, draftIndex) => draftIndex !== index);
		});
	}, []);

	const handleSend = useCallback(async () => {
		const trimmedMessage = message.trim();
		if (
			!trimmedMessage &&
			mentions.length === 0 &&
			attachments.length === 0
		) {
			return;
		}
		if (disabled || isLoading || isSubmitting) return;

		const mentionPrefixes = mentions
			.map((mention) => `@[${mention.name}]`)
			.join(" ");
		const finalMessage = mentionPrefixes
			? `${mentionPrefixes} ${trimmedMessage}`.trim()
			: trimmedMessage;
		const submittedMessage = message;
		const submittedMentions = mentions;
		const submittedAttachments = attachments;
		setIsSubmitting(true);
		setSendError(null);
		setMessage("");
		setMentions([]);
		setAttachments([]);
		if (textareaRef.current) textareaRef.current.style.height = "auto";
		try {
			await onSend(
				finalMessage,
				submittedAttachments.map((draft) => draft.file),
				modelProfileId,
			);
			for (const draft of submittedAttachments) {
				if (draft.previewUrl) URL.revokeObjectURL(draft.previewUrl);
			}
		} catch (error) {
			setSendError(
				error instanceof Error
					? error.message
					: "Could not send this message. Try again.",
			);
			// Restore the submitted draft only when
			// the user has not already started composing a replacement.
			setMessage((current) => current || submittedMessage);
			setMentions((current) =>
				current.length > 0 ? current : submittedMentions,
			);
			setAttachments((current) =>
				current.length > 0 ? current : submittedAttachments,
			);
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
		modelProfileId,
		onSend,
	]);

	const handleKeyDown = useCallback(
		(event: KeyboardEvent<HTMLTextAreaElement>) => {
			if (
				mentionOpen &&
				["ArrowUp", "ArrowDown", "Enter", "Escape"].includes(event.key)
			) {
				return;
			}
			if (mentionOpen && event.key === "Tab") {
				event.preventDefault();
				return;
			}
			if (event.key === "Enter" && !event.shiftKey && !mentionOpen) {
				event.preventDefault();
				void handleSend();
			}
		},
		[handleSend, mentionOpen],
	);

	const handleInputChange = useCallback(
		(event: ChangeEvent<HTMLTextAreaElement>) => {
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
		},
		[],
	);

	const handleMentionSelect = useCallback(
		(agent: AgentSummary) => {
			if (mentionStart === null) return;
			const before = message.slice(0, mentionStart);
			const after = message.slice(
				mentionStart + 1 + mentionSearch.length,
			);
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
		(message.trim().length > 0 ||
			mentions.length > 0 ||
			attachments.length > 0) &&
		!busy;
	const contextWindow = matchingContextUsage?.context_window ?? null;
	const contextUsedPercent = contextWindow
		? Math.min(
				100,
				Math.round(
					(matchingContextUsage!.input_tokens / contextWindow) * 100,
				),
			)
		: null;
	const cacheReadPercent = matchingContextUsage
		? matchingContextUsage.input_tokens > 0
			? Math.min(
					100,
					Math.round(
						(matchingContextUsage.cache_read_tokens /
							matchingContextUsage.input_tokens) *
							100,
					),
				)
			: 0
		: null;

	return (
		<div className="flex max-h-[65%] min-h-0 shrink-0 flex-col px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2 sm:px-4 sm:pb-4">
			<div className="mx-auto flex min-h-0 w-full max-w-4xl flex-col">
				{sendError && (
					<p
						role="alert"
						className="mb-2 max-h-20 overflow-y-auto text-sm text-destructive [overflow-wrap:anywhere]"
					>
						Message not sent: {sendError}
					</p>
				)}
				<div
					onDrop={handleDrop}
					onDragOver={(event) => {
						event.preventDefault();
						setIsDragging(true);
					}}
					onDragLeave={() => setIsDragging(false)}
					className={cn(
						"relative flex min-h-0 flex-col rounded-[var(--bf-radius-feature)] border bg-card text-card-foreground shadow-sm transition-colors motion-reduce:transition-none",
						"focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/20",
						isDragging && "border-primary bg-primary/5",
					)}
				>
					<MentionPicker
						open={mentionOpen}
						onOpenChange={setMentionOpen}
						onSelect={handleMentionSelect}
						searchTerm={mentionSearch}
						onSearchChange={(value) => {
							if (mentionStart === null) return;
							setMessage(
								`${message.slice(0, mentionStart + 1)}${value}${message.slice(mentionStart + 1 + mentionSearch.length)}`,
							);
							setMentionSearch(value);
						}}
						position={{ x: 16, y: 0 }}
					/>

					<div className="min-h-0 overflow-y-auto">
						{attachments.length > 0 && (
							<div className="grid max-h-48 gap-2 overflow-y-auto px-3 pt-3 sm:grid-cols-2">
								{attachments.map((draft, index) => (
									<DraftAttachment
										key={`${draft.file.name}-${draft.file.lastModified}-${index}`}
										file={draft.file}
										previewUrl={draft.previewUrl}
										disabled={busy}
										onRemove={() => removeAttachment(index)}
									/>
								))}
							</div>
						)}

						{mentions.length > 0 && (
							<div className="flex flex-wrap gap-1.5 px-3 pt-3">
								{mentions.map((mention) => (
									<span
										key={mention.name}
										className="inline-flex max-w-full min-w-0 items-center gap-2 rounded-[var(--bf-radius-control)] bg-primary/10 pl-3 text-xs font-medium text-primary"
									>
										<Bot className="h-3 w-3 shrink-0" />
										<span className="min-w-0 [overflow-wrap:anywhere]">
											{mention.name}
										</span>
										<Button
											type="button"
											variant="ghost"
											size="icon-sm"
											className="size-11 shrink-0"
											disabled={busy}
											aria-label={`Remove ${mention.name}`}
											onClick={() =>
												setMentions((current) =>
													current.filter(
														(item) =>
															item.name !==
															mention.name,
													),
												)
											}
										>
											<X className="h-3 w-3" />
										</Button>
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
					</div>
					<div className="flex shrink-0 items-center justify-between gap-2 px-2.5 pb-2.5">
						<div className="flex min-w-0 flex-1 items-center gap-1">
							<input
								ref={fileInputRef}
								type="file"
								multiple
								className="hidden"
								accept={[
									selectedCapabilities?.image_input
										? "image/png,image/jpeg,image/webp,image/gif"
										: "",
									selectedCapabilities?.pdf_input
										? "application/pdf"
										: "",
									"text/*,application/json,application/csv",
								]
									.filter(Boolean)
									.join(",")}
								onChange={(event) => {
									addFiles(
										Array.from(event.target.files ?? []),
									);
									event.target.value = "";
								}}
							/>
							<Button
								type="button"
								variant="ghost"
								size="icon-sm"
								aria-label="Attach files"
								title="Attach files"
								className="size-11"
								disabled={
									busy ||
									attachments.length >=
										MAX_ATTACHMENTS_PER_MESSAGE
								}
								onClick={() => fileInputRef.current?.click()}
							>
								<Paperclip className="h-4 w-4" />
							</Button>
							{modelProfiles.length > 0 && modelProfileId && (
								<Select
									value={modelProfileId}
									onValueChange={(value) =>
										onModelProfileChange?.(
											value as ChatModelProfileId,
										)
									}
									disabled={busy}
								>
									<SelectTrigger
										aria-label="Response model"
										className="h-auto min-h-11 w-full min-w-0 max-w-72 border-0 bg-transparent px-2 text-xs shadow-none [&_[data-slot=select-value]]:truncate"
									>
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{modelProfiles.map((profile) => (
											<SelectItem
												key={profile.id}
												value={profile.id}
											>
												{profile.label}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							)}
							{matchingContextUsage && (
								<Popover
									open={contextOpen}
									onOpenChange={setContextOpen}
								>
									<PopoverTrigger asChild>
										<Button
											type="button"
											variant="ghost"
											size="icon-sm"
											className="size-11 shrink-0 rounded-full p-2"
											aria-label="Context window"
											onFocus={() => setContextOpen(true)}
											onMouseEnter={() => setContextOpen(true)}
											onMouseLeave={() => setContextOpen(false)}
											onBlur={() => setContextOpen(false)}
											onClick={(event) => {
												event.preventDefault();
												setContextOpen(true);
											}}
										>
											<span
												aria-hidden="true"
												className="grid size-5 place-items-center rounded-full bg-[conic-gradient(var(--primary)_calc(var(--context-used)*1%),var(--muted)_0)]"
												style={
													{
														"--context-used": contextUsedPercent ?? 0,
											} as CSSProperties
												}
											>
												<span className="size-3 rounded-full bg-card" />
											</span>
										</Button>
									</PopoverTrigger>
									<PopoverContent
										side="top"
										align="center"
										collisionPadding={16}
										aria-label="Context window"
										className="w-64 max-w-[calc(100vw-2rem)] gap-1.5 p-3 text-center"
										onOpenAutoFocus={(event) => event.preventDefault()}
										onCloseAutoFocus={(event) => event.preventDefault()}
									>
										<p className="text-xs text-muted-foreground">
											Context window
										</p>
										{contextWindow && contextUsedPercent !== null ? (
											<>
												<p className="text-sm font-medium">
													{contextUsedPercent}% used ({100 - contextUsedPercent}% left)
												</p>
												<p className="whitespace-nowrap text-xs">
													{formatContextTokens(matchingContextUsage.input_tokens)} / {formatContextTokens(contextWindow)} tokens{" "}
													<span className="text-muted-foreground">
														({cacheReadPercent}% cached)
													</span>
												</p>
											</>
										) : (
											<>
												<p className="text-sm font-medium">
													Capacity unavailable.
												</p>
												<p className="whitespace-nowrap text-xs">
													{formatContextTokens(matchingContextUsage.input_tokens)} tokens
													{" "}<span className="text-muted-foreground">
														({cacheReadPercent}% cached)
													</span>
												</p>
											</>
										)}
									</PopoverContent>
								</Popover>
							)}
						</div>

						{isLoading && onStop ? (
							<Button
								onClick={onStop}
								size="icon-sm"
								variant="destructive"
								aria-label="Stop generation"
								title="Stop generation"
								className="size-11 rounded-full"
							>
								<Square className="h-3 w-3 fill-current" />
							</Button>
						) : (
							<Button
								onClick={() => void handleSend()}
								disabled={!canSend}
								size="icon-sm"
								aria-label="Send message"
								className="size-11 rounded-full"
							>
								{isSubmitting ? (
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
								) : (
									<ArrowUp className="h-4 w-4" />
								)}
							</Button>
						)}
					</div>
				</div>
				<p className="mt-2 shrink-0 text-center text-[11px] text-muted-foreground">
					AI can make mistakes. Check important results.
				</p>
			</div>
		</div>
	);
}
