/**
 * ChatMessage Component
 *
 * Renders a single chat message with role-based styling.
 * Clean, modern design similar to ChatGPT/Claude.
 * Supports full markdown rendering for AI responses.
 */

import { Bot, Check, Copy } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
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
		<span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-black/25 font-medium text-sm">
			<Bot className="h-3 w-3 shrink-0" />
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
	const createdAt = message.created_at ? new Date(message.created_at) : null;
	const timestamp =
		createdAt && !Number.isNaN(createdAt.valueOf())
			? createdAt.toLocaleString([], {
					dateStyle: "medium",
					timeStyle: "short",
				})
			: null;
	return (
		<div className="flex min-h-11 items-center gap-2 text-[11px] text-muted-foreground opacity-100 transition-opacity sm:min-h-0 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
			{timestamp && <time dateTime={message.created_at ?? undefined}>{timestamp}</time>}
			{message.content && (
				<button
					type="button"
					className="flex size-11 items-center justify-center rounded hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:size-6"
					onClick={async () => {
						await navigator.clipboard.writeText(message.content ?? "");
						setCopied(true);
						window.setTimeout(() => setCopied(false), 1500);
					}}
					aria-label={copied ? "Copied message" : "Copy message"}
				>
					{copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
				</button>
			)}
		</div>
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
			<div className="group flex flex-col items-end justify-end py-2 px-4">
				<div className="max-w-[80%] bg-primary text-primary-foreground rounded-2xl px-4 py-2.5 overflow-x-auto break-words">
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
			className="py-3 px-4 group"
			aria-busy={isStreaming || undefined}
		>
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

				<div className="mt-2 flex items-center gap-3">
					<MessageActions message={message} />
				{(message.token_count_input != null && message.token_count_input > 0 || message.token_count_output != null && message.token_count_output > 0) && (
					<div className="flex gap-3 text-xs text-muted-foreground opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100">
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
				)}
				</div>
			</div>
		</div>
	);
}
