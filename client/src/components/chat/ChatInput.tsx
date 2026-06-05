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
	Loader2,
	Paperclip,
	Plus,
	Square,
	X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { MentionPicker } from "./MentionPicker";
import type { components } from "@/lib/v1";

type AgentSummary = components["schemas"]["AgentSummary"];

interface MentionChip {
	name: string;
	position: number; // Where in the message this mention starts
}

interface ChatInputProps {
	onSend: (message: string) => void;
	disabled?: boolean;
	isLoading?: boolean;
	placeholder?: string;
	onStop?: () => void;
}

export function ChatInput({
	onSend,
	disabled = false,
	isLoading = false,
	placeholder = "Reply...",
	onStop,
}: ChatInputProps) {
	const [message, setMessage] = useState("");
	const [mentions, setMentions] = useState<MentionChip[]>([]);
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const containerRef = useRef<HTMLDivElement>(null);

	// Mention picker state
	const [mentionOpen, setMentionOpen] = useState(false);
	const [mentionSearch, setMentionSearch] = useState("");
	const [mentionPosition, setMentionPosition] = useState({ x: 0, y: 0 });
	const [mentionStart, setMentionStart] = useState<number | null>(null);

	const handleSend = useCallback(() => {
		const trimmedMessage = message.trim();
		if (!trimmedMessage && mentions.length === 0) return;
		if (disabled || isLoading) return;

		// Build final message with mentions prepended
		const mentionPrefixes = mentions.map((m) => `@[${m.name}]`).join(" ");
		const finalMessage = mentionPrefixes
			? `${mentionPrefixes} ${trimmedMessage}`.trim()
			: trimmedMessage;

		onSend(finalMessage);
		setMessage("");
		setMentions([]);

		// Reset textarea height
		if (textareaRef.current) {
			textareaRef.current.style.height = "auto";
		}
	}, [message, mentions, disabled, isLoading, onSend]);

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
		!isLoading;

	return (
		<div className="p-4 pt-2">
			<div className="max-w-4xl mx-auto">
				{/* Floating input container */}
				<div
					ref={containerRef}
					className={cn(
						"relative rounded-2xl border bg-muted/50 shadow-lg",
						"transition-all duration-200",
						"focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2 focus-within:ring-offset-background",
					)}
				>
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
						<textarea
							ref={textareaRef}
							aria-label="Chat input"
							value={message}
							onChange={handleInputChange}
							onKeyDown={handleKeyDown}
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
							<Button
								type="button"
								variant="ghost"
								size="icon"
								className="h-8 w-8 rounded-full text-muted-foreground/50 cursor-not-allowed"
								disabled
								title="Coming soon"
							>
								<Plus className="h-5 w-5" />
							</Button>
							<Button
								type="button"
								variant="ghost"
								size="icon"
								className="h-8 w-8 rounded-full text-muted-foreground/50 cursor-not-allowed"
								disabled
								title="Coming soon"
							>
								<Paperclip className="h-4 w-4" />
							</Button>
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

				{/* Disclaimer */}
				<p className="text-center text-xs text-muted-foreground mt-2">
					Claude is AI and can make mistakes. Please double-check
					responses.
				</p>
			</div>
		</div>
	);
}
