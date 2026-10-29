/**
 * ChatMessage Component
 *
 * Renders a single chat message with role-based styling.
 * Clean, modern design similar to ChatGPT/Claude.
 * Supports full markdown rendering for AI responses.
 */

import { Bot, Check, Copy } from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { copyToClipboard } from "@/lib/clipboard";
import type { components } from "@/lib/v1";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { ChatAttachmentList } from "./ChatAttachmentList";

type MessagePublic = components["schemas"]["MessagePublic"];

/**
 * Detect if text is a progress/status update rather than the final result.
 * Progress updates are rendered with subdued styling.
 */
function isProgressUpdate(text: string): boolean {
	const trimmed = text.trim();

	// Patterns that indicate progress/status updates
	const progressPatterns = [
		// Starting patterns - agent announcing what it's about to do
		/^(Let me|I'll|I will|Now I'm|I'm going to|I'm now|Now let me)/i,
		/^(Searching|Looking|Checking|Analyzing|Reading|Processing|Fetching|Loading)/i,
		/^(First,|Next,|Then,|Finally,|Now,|Alright,|Okay,)/i,

		// Transitional/enthusiastic openers
		/^(Excellent|Great|Perfect|Good|Wonderful|Alright)(!|,)/i,

		// Short status updates (under 100 chars and matches pattern)
		/^(I found|I see|I notice|I can see|I've found|I've located)/i,
	];

	// Check if matches any progress pattern
	if (progressPatterns.some((p) => p.test(trimmed))) {
		return true;
	}

	return false;
}

/**
 * Convert canonical agent mentions to HTML spans for markdown rendering.
 * Bare @words are ordinary message content so email addresses are preserved.
 */
function preprocessMentions(content: string): string {
	const mentionRegex = /@\[([^\]\r\n]{1,256})\]/g;
	return content.replace(mentionRegex, (_, agentName: string) => {
		// Use data attribute to mark as mention for custom rendering
		const escapedName = agentName
			.replaceAll("&", "&amp;")
			.replaceAll('"', "&quot;")
			.replaceAll("<", "&lt;")
			.replaceAll(">", "&gt;");
		return `<span data-mention="${escapedName}"></span>`;
	});
}

/**
 * Mention badge component for use in markdown
 */
function MentionBadge({ name }: { name: string }) {
	return (
		<span className="inline-flex min-h-11 items-center gap-1 rounded-[var(--bf-radius-control)] bg-black/25 px-2 py-1 font-medium text-sm leading-6">
			<Bot className="h-4 w-4 shrink-0" />
			{name}
		</span>
	);
}

function SafeChatLink({
	href,
	children,
	className,
}: {
	href?: string;
	children: React.ReactNode;
	className: string;
}) {
	const value = href?.trim() ?? "";
	const allowed =
		value.startsWith("https://") ||
		value.startsWith("http://") ||
		value.startsWith("mailto:") ||
		value.startsWith("/") ||
		value.startsWith("#");
	if (!allowed) {
		return (
			<span className="text-muted-foreground" title="Local file links are unavailable">
				{children}
			</span>
		);
	}
	return (
		<a
			href={value}
			target={value.startsWith("http") ? "_blank" : undefined}
			rel={value.startsWith("http") ? "noopener noreferrer" : undefined}
			className={className}
		>
			{children}
		</a>
	);
}

function MessageActions({ message }: { message: MessagePublic }) {
	const [copied, setCopied] = useState(false);
	const [copyError, setCopyError] = useState(false);
	const [copying, setCopying] = useState(false);
	const active = useRef(true);
	const copyingRef = useRef(false);
	const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
	useEffect(() => {
		active.current = true;
		return () => { active.current = false; if (timer.current) clearTimeout(timer.current); };
	}, []);
	const createdAt = message.created_at ? new Date(message.created_at) : null;
	const timestamp =
		createdAt && !Number.isNaN(createdAt.valueOf())
			? createdAt.toLocaleString([], {
					dateStyle: "medium",
					timeStyle: "short",
				})
			: null;
	return (
		<div className="flex min-h-11 flex-wrap items-center gap-2 text-sm leading-6 text-muted-foreground opacity-100 transition-opacity motion-reduce:transition-none sm:min-h-0 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
			{timestamp && <time dateTime={message.created_at ?? undefined}>{timestamp}</time>}
			{message.content && (
				<button
					type="button"
					className="flex size-11 items-center justify-center rounded-[var(--bf-radius-control)] hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:size-6"
					onClick={async () => {
						if (copyingRef.current) return;
						copyingRef.current = true;
						setCopying(true);
						setCopyError(false);
						const success = await copyToClipboard(message.content ?? "");
						copyingRef.current = false;
						if (!active.current) return;
						setCopying(false);
						setCopied(success);
						setCopyError(!success);
						if (timer.current) clearTimeout(timer.current);
						if (success) timer.current = setTimeout(() => setCopied(false), 1500);
					}}
					disabled={copying}
					aria-label={copied ? "Copied message" : "Copy message"}
				>
					{copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
				</button>
			)}
			{copyError && <span role="alert" className="text-xs text-[var(--bf-danger)]">Couldn’t copy. Select the message text or try again.</span>}
		</div>
	);
}

interface ChatMessageProps {
	message: MessagePublic;
	isStreaming?: boolean;
}

function ChatMessageView({
	message,
	isStreaming,
}: ChatMessageProps) {
	const isUser = message.role === "user";

	// User message - right-aligned bubble with markdown rendering
	if (isUser) {
		return (
			<div className="group flex flex-col items-end justify-end py-2 px-4">
				<div className="max-w-full overflow-x-auto break-words rounded-[var(--bf-radius-feature)] bg-primary px-4 py-3 text-primary-foreground sm:max-w-[80%]">
					<ChatAttachmentList
						conversationId={message.conversation_id}
						attachments={message.attachments ?? []}
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
												className="rounded-[var(--bf-radius-control)] !my-2"
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
									<SafeChatLink
										href={href}
										className="text-primary-foreground underline hover:opacity-80"
									>
										{children}
									</SafeChatLink>
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
				<div className="mt-1 mr-1"><MessageActions message={message} /></div>
			</div>
		);
	}

	// Assistant message - full markdown rendering
	return (
		<div
			className="group px-4 py-3"
			role="article"
			aria-label="Assistant message"
			aria-busy={isStreaming || undefined}
		>
			<div className="max-w-4xl">
				{/* Markdown Content */}
				<div className="prose prose-slate dark:prose-invert max-w-none break-words prose-p:my-2 prose-p:leading-7 prose-headings:font-semibold prose-h1:text-xl prose-h2:text-lg prose-h3:text-base prose-ul:my-2 prose-ol:my-2 prose-li:my-0.5 prose-pre:my-2 prose-pre:p-0 prose-pre:bg-transparent">
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
									if (isStreaming) {
										return (
											<pre className="my-2 overflow-x-auto rounded-[var(--bf-radius-control)] bg-slate-950 p-3 text-slate-100">
												<code>{content}</code>
											</pre>
										);
									}
									return (
										<SyntaxHighlighter
											style={oneDark}
											language={match?.[1] || "text"}
											PreTag="div"
											className="rounded-[var(--bf-radius-control)] !my-2"
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
								<SafeChatLink
									href={href}
									className="text-primary hover:underline"
								>
									{children}
								</SafeChatLink>
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
									<table className="w-full border-collapse border border-border">
										{children}
									</table>
								</div>
							),
							th: ({ children }) => (
								<th className="min-w-24 border border-border px-3 py-2 bg-muted font-semibold text-left align-top [overflow-wrap:anywhere]">
									{children}
								</th>
							),
							td: ({ children }) => (
								<td className="min-w-24 border border-border px-3 py-2 align-top [overflow-wrap:anywhere]">
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

				<div className="mt-2 flex items-center gap-3">
					<MessageActions message={message} />
					{message.duration_ms != null && message.duration_ms > 0 && (
						<div className="flex flex-wrap gap-3 text-sm leading-6 text-muted-foreground opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
							<span>{message.duration_ms}ms</span>
						</div>
					)}
				</div>
			</div>
		</div>
	);
}

/**
 * Streaming only changes the active message object. Keeping settled messages
 * memoized prevents every token batch from reparsing the entire transcript.
 */
export const ChatMessage = memo(ChatMessageView);
