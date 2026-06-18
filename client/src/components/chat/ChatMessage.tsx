/**
 * ChatMessage Component
 *
 * Renders a single chat message with role-based styling.
 * Clean, modern design similar to ChatGPT/Claude.
 * Supports full markdown rendering for AI responses.
 */

import {
	Bot,
	FileImage,
	FileSpreadsheet,
	FileText,
	Gauge,
	Gem,
	Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";
import { isImageAttachment } from "@/services/chatAttachments";
import {
	COST_TIER_LABEL,
	type CostTier,
} from "@/services/platformModels";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

type MessagePublic = components["schemas"]["MessagePublic"];
type AttachmentPublic = components["schemas"]["AttachmentPublic"];

function attachmentIcon(contentType: string) {
	if (isImageAttachment(contentType)) return FileImage;
	if (contentType === "text/csv" || contentType === "application/csv")
		return FileSpreadsheet;
	return FileText;
}

/** Renders the chips for a message's bound attachments. */
function MessageAttachments({
	attachments,
	tone,
}: {
	attachments: AttachmentPublic[];
	tone: "user" | "assistant";
}) {
	if (attachments.length === 0) return null;
	return (
		<div className="flex flex-wrap gap-1.5 mb-2">
			{attachments.map((att) => {
				const Icon = attachmentIcon(att.content_type);
				return (
					<span
						key={att.id}
						className={cn(
							"inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs",
							tone === "user"
								? "bg-black/20 text-primary-foreground"
								: "bg-muted text-foreground",
						)}
						title={att.filename}
					>
						<Icon className="h-3.5 w-3.5 shrink-0 opacity-80" />
						<span className="max-w-[180px] truncate">{att.filename}</span>
					</span>
				);
			})}
		</div>
	);
}

/**
 * Detect if a paragraph is a throwaway progress/status line (e.g. "Let me check
 * that.") rather than real answer content, so it can be rendered subdued.
 *
 * A progress opener alone is NOT enough — those phrases ("Let me", "Now,",
 * "Great!", "I found") routinely begin substantive final answers too. We only
 * subdue a paragraph that is BOTH a progress opener AND short: a genuine status
 * line is a brief single sentence, so anything past ~80 chars or containing
 * multiple sentences is treated as real content and left at full emphasis.
 */
const PROGRESS_MAX_CHARS = 80;

function isProgressUpdate(text: string): boolean {
	const trimmed = text.trim();

	// Only ever subdue brief, single-sentence lines. A long or multi-sentence
	// paragraph is content, regardless of how it opens.
	if (trimmed.length > PROGRESS_MAX_CHARS) {
		return false;
	}
	// More than one sentence-ending punctuation mark => it's saying something,
	// not just announcing an action.
	if ((trimmed.match(/[.!?](\s|$)/g)?.length ?? 0) > 1) {
		return false;
	}

	const progressPatterns = [
		// Agent announcing what it's about to do.
		/^(Let me|I'll|I will|Now I'm|I'm going to|I'm now|Now let me)/i,
		/^(Searching|Looking|Checking|Analyzing|Reading|Processing|Fetching|Loading)/i,
		/^(First,|Next,|Then,|Finally,|Now,|Alright,|Okay,)/i,
		// Transitional/enthusiastic openers.
		/^(Excellent|Great|Perfect|Good|Wonderful|Alright)(!|,)/i,
		// Brief status updates.
		/^(I found|I see|I notice|I can see|I've found|I've located)/i,
	];

	return progressPatterns.some((p) => p.test(trimmed));
}

/**
 * Convert @mentions to HTML spans for markdown rendering
 * Supports both @[Agent Name] (new) and @AgentName (legacy) formats
 */
function preprocessMentions(content: string): string {
	// Match both formats:
	// 1. @[Agent Name] - bracketed format (preferred)
	// 2. @Word - single word without brackets (legacy fallback)
	const mentionRegex = /@\[([^\]]+)\]|@(\w+)/g;
	return content.replace(mentionRegex, (_, bracketName, wordName) => {
		const agentName = bracketName || wordName;
		// Use data attribute to mark as mention for custom rendering
		return `<span data-mention="${agentName}"></span>`;
	});
}

/**
 * Mention badge component for use in markdown
 */
function MentionBadge({ name }: { name: string }) {
	return (
		<span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/25 font-medium text-sm">
			<Bot className="h-3 w-3 shrink-0" />
			{name}
		</span>
	);
}

const COST_TIERS: ReadonlySet<string> = new Set<CostTier>([
	"fast",
	"balanced",
	"premium",
]);

/**
 * Symbolic per-message cost-tier badge (§5.6 / §16.5): ⚡ / ⚖ / 💎. Dollars
 * stay in the admin dashboard — chat is anxiety-free. Renders nothing for an
 * unknown/missing tier.
 */
// Lucide icons (not emoji) so the three tiers read as one coherent, monochrome
// set — the emoji glyphs (⚡/⚖/💎) rendered inconsistently (color vs thin mono).
const COST_TIER_ICON: Record<CostTier, LucideIcon> = {
	fast: Zap,
	balanced: Gauge,
	premium: Gem,
};

function CostTierBadge({ tier }: { tier: string | null | undefined }) {
	if (!tier || !COST_TIERS.has(tier)) return null;
	const t = tier as CostTier;
	const Icon = COST_TIER_ICON[t];
	return (
		<TooltipProvider delayDuration={200}>
			<Tooltip>
				<TooltipTrigger asChild>
					<span
						className="cursor-default select-none text-muted-foreground"
						aria-label={`${COST_TIER_LABEL[t]} tier`}
					>
						<Icon className="size-3.5" />
					</span>
				</TooltipTrigger>
				<TooltipContent>{COST_TIER_LABEL[t]} tier</TooltipContent>
			</Tooltip>
		</TooltipProvider>
	);
}

interface ChatMessageProps {
	message: MessagePublic;
	isStreaming?: boolean;
}

export function ChatMessage({
	message,
	isStreaming,
}: ChatMessageProps) {
	const isUser = message.role === "user";

	// User message - right-aligned bubble with markdown rendering
	if (isUser) {
		return (
			<div className="flex justify-end py-2 px-4">
				<div className="max-w-[80%] bg-primary text-primary-foreground rounded-2xl px-4 py-2.5 overflow-x-auto break-words">
					<MessageAttachments
						attachments={message.attachments ?? []}
						tone="user"
					/>
					<div className="prose prose-invert prose-sm max-w-none prose-p:my-1 prose-p:leading-relaxed prose-p:text-primary-foreground prose-headings:text-primary-foreground prose-strong:text-primary-foreground prose-code:text-primary-foreground prose-pre:my-2 prose-pre:p-0 prose-pre:bg-transparent">
						<ReactMarkdown
							remarkPlugins={[remarkGfm]}
							rehypePlugins={[rehypeRaw]}
							components={{
								code({ className, children }) {
									const match = /language-(\w+)/.exec(
										className || "",
									);
									const content = String(children).replace(
										/\n$/,
										"",
									);
									const isCodeBlock =
										content.includes("\n") || className;

									if (isCodeBlock) {
										return (
											<SyntaxHighlighter
												style={oneDark}
												language={match?.[1] || "text"}
												PreTag="div"
												className="rounded-md !my-2"
											>
												{content}
											</SyntaxHighlighter>
										);
									}

									// Inline code - darker bg within blue bubble
									return (
										<code className="bg-black/20 px-1.5 py-0.5 rounded text-sm font-mono">
											{children}
										</code>
									);
								},
								p: ({ children }) => (
									<p className="my-1 leading-relaxed">
										{children}
									</p>
								),
								// Links in user messages
								a: ({ href, children }) => (
									<a
										href={href}
										target="_blank"
										rel="noopener noreferrer"
										className="text-primary-foreground underline hover:opacity-80"
									>
										{children}
									</a>
								),
								// Handle @mention spans
								span: ({ node, ...props }) => {
									const mention = (
										node?.properties as Record<
											string,
											unknown
										>
									)?.dataMention as string | undefined;
									if (mention) {
										return <MentionBadge name={mention} />;
									}
									return <span {...props} />;
								},
							}}
						>
							{preprocessMentions(message.content || "")}
						</ReactMarkdown>
					</div>
				</div>
			</div>
		);
	}

	// Assistant message - full markdown rendering
	return (
		<div className={cn("py-3 px-4 group", isStreaming && "animate-pulse")}>
			<div className="max-w-4xl">
				{/* Markdown Content */}
				<div className="prose prose-slate dark:prose-invert max-w-none prose-p:my-2 prose-p:leading-7 prose-headings:font-semibold prose-h1:text-xl prose-h2:text-lg prose-h3:text-base prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5 prose-pre:my-2 prose-pre:p-0 prose-pre:bg-transparent">
					<ReactMarkdown
						remarkPlugins={[remarkGfm]}
						rehypePlugins={[rehypeRaw]}
						components={{
							code({ className, children }) {
								const match = /language-(\w+)/.exec(
									className || "",
								);
								const content = String(children).replace(
									/\n$/,
									"",
								);

								// Check if it's a code block (has newlines or className)
								const isCodeBlock =
									content.includes("\n") || className;

								if (isCodeBlock) {
									return (
										<SyntaxHighlighter
											style={oneDark}
											language={match?.[1] || "text"}
											PreTag="div"
											className="rounded-md !my-2"
										>
											{content}
										</SyntaxHighlighter>
									);
								}

								// Inline code
								return (
									<code className="bg-muted px-1.5 py-0.5 rounded text-sm font-mono">
										{children}
									</code>
								);
							},
							// Tighter spacing for chat context
							// Apply subdued styling for progress updates
							p: ({ children }) => {
								const text =
									typeof children === "string"
										? children
										: Array.isArray(children)
											? children
													.filter(
														(c) =>
															typeof c ===
															"string",
													)
													.join("")
											: "";
								const isProgress = isProgressUpdate(text);
								return (
									<p
										className={cn(
											"my-2 leading-7",
											isProgress &&
												"text-sm text-muted-foreground",
										)}
									>
										{children}
									</p>
								);
							},
							ul: ({ children }) => (
								<ul className="my-2 ml-4 list-disc space-y-1">
									{children}
								</ul>
							),
							ol: ({ children }) => (
								<ol className="my-2 ml-4 list-decimal space-y-1">
									{children}
								</ol>
							),
							li: ({ children }) => (
								<li className="leading-6">{children}</li>
							),
							// Links
							a: ({ href, children }) => (
								<a
									href={href}
									target="_blank"
									rel="noopener noreferrer"
									className="text-primary hover:underline"
								>
									{children}
								</a>
							),
							// Blockquotes
							blockquote: ({ children }) => (
								<blockquote className="border-l-2 border-muted-foreground/30 pl-4 my-2 italic text-muted-foreground">
									{children}
								</blockquote>
							),
							// Tables
							table: ({ children }) => (
								<div className="my-2 overflow-x-auto">
									<table className="min-w-full border-collapse border border-border">
										{children}
									</table>
								</div>
							),
							th: ({ children }) => (
								<th className="border border-border px-3 py-2 bg-muted font-semibold text-left">
									{children}
								</th>
							),
							td: ({ children }) => (
								<td className="border border-border px-3 py-2">
									{children}
								</td>
							),
							// Horizontal rule
							hr: () => <hr className="my-4 border-border" />,
						}}
					>
						{message.content || ""}
					</ReactMarkdown>
				</div>

				{/* Footer: cost tier (always visible) + token usage (on hover) */}
				{(message.cost_tier ||
					(message.token_count_input != null &&
						message.token_count_input > 0) ||
					(message.token_count_output != null &&
						message.token_count_output > 0)) && (
					<div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
						<CostTierBadge tier={message.cost_tier} />
						<div className="flex gap-3 opacity-0 group-hover:opacity-100 transition-opacity">
							{!!message.token_count_input && (
								<span>In: {message.token_count_input}</span>
							)}
							{!!message.token_count_output && (
								<span>Out: {message.token_count_output}</span>
							)}
							{!!message.duration_ms && (
								<span>{message.duration_ms}ms</span>
							)}
						</div>
					</div>
				)}
			</div>
		</div>
	);
}
