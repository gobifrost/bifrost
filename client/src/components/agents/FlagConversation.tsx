/**
 * Per-flag improvement conversation.
 *
 * Presentational only — the parent owns the fetch + send mutation
 * (RunReviewSheet, AgentRunDetailPage). The component renders the message
 * stream and a ChatComposer; parent passes `pending` to lock the composer
 * while a send is in flight.
 *
 * Philosophy: every flag is a conversation, never a dead-end text box.
 * The review assistant always responds — diagnoses, asks clarifying
 * questions, proposes a change when ready. Changes are NOT applied
 * from here; that happens in the quality workbench.
 */

import { useEffect, useRef } from "react";
import { Loader2, PlayCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ChatComposer } from "@/components/ui/chat-composer";
import { ChatBubble, ChatBubbleSlot } from "@/components/agents/ChatBubble";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

type FlagConversationResponse =
	components["schemas"]["FlagConversationResponse"];
type ConversationMessage = FlagConversationResponse["messages"][number];
type UserTurn = components["schemas"]["UserTurn"];
type AssistantTurn = components["schemas"]["AssistantTurn"];
type ProposalTurn = components["schemas"]["ProposalTurn"];
type DryRunTurn = components["schemas"]["DryRunTurn"];

export interface FlagConversationProps {
	conversation: FlagConversationResponse | null;
	onSend: (text: string) => void | Promise<void>;
	pending?: boolean;
	disabled?: boolean;
	onTestAgainstRun?: () => void;
}

export function FlagConversation({
	conversation,
	onSend,
	pending = false,
	disabled = false,
	onTestAgainstRun,
}: FlagConversationProps) {
	const messages = conversation?.messages ?? [];
	const scrollRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		const el = scrollRef.current;
		if (!el) return;
		const reducedMotionQuery = window.matchMedia?.(
			"(prefers-reduced-motion: reduce)",
		);
		const prefersReducedMotion = reducedMotionQuery?.matches ?? false;
		el.scrollTo({
			top: el.scrollHeight,
			behavior: prefersReducedMotion ? "auto" : "smooth",
		});
	}, [messages.length, pending]);

	return (
		<div
			className="flex h-full min-h-0 flex-col"
			data-slot="flag-conversation"
		>
			<div
				ref={scrollRef}
				className="flex-1 min-h-0 space-y-3 overflow-y-auto px-3 py-3 sm:px-4"
			>
				{messages.length === 0 ? (
					<EmptyState />
				) : (
					messages.map((m, i) => (
						<Bubble
							key={i}
							msg={m}
							onTestAgainstRun={onTestAgainstRun}
						/>
					))
				)}
				{pending ? (
					<div
						role="status"
						className="inline-flex min-h-11 items-center gap-2 rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/30 px-3 text-xs leading-5 text-muted-foreground"
					>
						<Loader2
							size={13}
							className="animate-spin motion-reduce:animate-none"
						/>
						Thinking…
					</div>
				) : null}
			</div>
			<fieldset
				disabled={disabled}
				className="border-t border-border/70 bg-background/95 p-3 backdrop-blur supports-[backdrop-filter]:bg-background/90"
			>
				<ChatComposer
					placeholder="What should it have done?"
					onSend={onSend}
					pending={pending}
				/>
			</fieldset>
		</div>
	);
}

function EmptyState() {
	return (
		<div className="rounded-[var(--bf-radius-surface)] border border-dashed border-border/70 bg-muted/20 px-4 py-3 text-sm leading-6 text-muted-foreground">
			Flag this run and tell me what went wrong. I&apos;ll help diagnose
			and propose a change — nothing touches the live prompt until you
			review and apply it.
		</div>
	);
}

function Bubble({
	msg,
	onTestAgainstRun,
}: {
	msg: ConversationMessage;
	onTestAgainstRun?: () => void;
}) {
	if (msg.kind === "user") {
		return <UserBubble msg={msg as UserTurn} />;
	}
	if (msg.kind === "assistant") {
		return <AssistantBubble msg={msg as AssistantTurn} />;
	}
	if (msg.kind === "proposal") {
		return (
			<ProposalBubble
				msg={msg as ProposalTurn}
				onTestAgainstRun={onTestAgainstRun}
			/>
		);
	}
	if (msg.kind === "dryrun") {
		return <DryRunBubble msg={msg as DryRunTurn} />;
	}
	return null;
}

function UserBubble({ msg }: { msg: UserTurn }) {
	return (
		<ChatBubble kind="user" className="mt-1" data-bubble-kind="user">
			{msg.content}
		</ChatBubble>
	);
}

function AssistantBubble({ msg }: { msg: AssistantTurn }) {
	return (
		<ChatBubble
			kind="assistant"
			className="mt-1"
			data-bubble-kind="assistant"
		>
			{msg.content}
		</ChatBubble>
	);
}

function ProposalBubble({
	msg,
	onTestAgainstRun,
}: {
	msg: ProposalTurn;
	onTestAgainstRun?: () => void;
}) {
	return (
		<ChatBubble
			kind="assistant"
			className="mt-1"
			data-bubble-kind="proposal"
			slots={
				<ChatBubbleSlot title="Proposed change">
					<div className="space-y-3">
						<div className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
							{msg.summary}
						</div>
						<div className="rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/20 p-3 font-mono text-xs leading-6 text-foreground whitespace-pre-wrap [overflow-wrap:anywhere]">
							{msg.diff.map((d, i) => (
								<div
									key={i}
									className={cn(
										"flex gap-2 rounded px-1 py-0.5",
										d.op === "add" &&
											"bg-[var(--bf-success-soft)] text-[var(--bf-success)]",
										d.op === "remove" &&
											"bg-[var(--bf-danger-soft)] text-[var(--bf-danger)]",
										d.op === "keep" &&
											"text-muted-foreground",
									)}
								>
									<span
										aria-hidden="true"
										className="shrink-0 opacity-70"
									>
										{d.op === "add"
											? "+"
											: d.op === "remove"
												? "−"
												: " "}
									</span>
									<span className="min-w-0 flex-1">
										{d.text}
									</span>
								</div>
							))}
						</div>
						{onTestAgainstRun ? (
							<div className="flex flex-wrap items-center gap-2">
								<Button
									type="button"
									variant="outline"
									size="lg"
									onClick={onTestAgainstRun}
									className="min-h-11"
								>
									<PlayCircle size={12} />
									Test against this run
								</Button>
								<span className="text-xs leading-5 text-muted-foreground">
									Sandbox
								</span>
							</div>
						) : null}
					</div>
				</ChatBubbleSlot>
			}
		>
			{null}
		</ChatBubble>
	);
}

function DryRunBubble({ msg }: { msg: DryRunTurn }) {
	const passed = msg.predicted === "up";
	return (
		<ChatBubble
			kind="assistant"
			className="mt-1"
			data-bubble-kind="dryrun"
			slots={
				<ChatBubbleSlot
					title={passed ? "Dry-run passed" : "Dry-run still wrong"}
					titleTone={passed ? "emerald" : "yellow"}
				>
					<div className="grid gap-2 sm:grid-cols-2">
						<div className="space-y-1">
							<div className="text-[10.5px] uppercase tracking-wider text-muted-foreground">
								Before
							</div>
							<div className="min-h-11 rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/20 px-3 py-2 text-xs leading-5 whitespace-pre-wrap [overflow-wrap:anywhere]">
								{msg.before}
							</div>
						</div>
						<div className="space-y-1">
							<div className="text-[10.5px] uppercase tracking-wider text-muted-foreground">
								After
							</div>
							<div
								className={cn(
									"min-h-11 rounded-[var(--bf-radius-surface)] border px-3 py-2 text-xs leading-5 whitespace-pre-wrap [overflow-wrap:anywhere]",
									passed
										? "border-[var(--bf-success)]/20 bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
										: "border-[var(--bf-warning)]/20 bg-[var(--bf-warning-soft)] text-[var(--bf-warning)]",
								)}
							>
								{msg.after}
							</div>
						</div>
					</div>
				</ChatBubbleSlot>
			}
		>
			{passed
				? "Prediction matched the run."
				: "Prediction still disagreed with the run."}
		</ChatBubble>
	);
}
