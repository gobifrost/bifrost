/**
 * ChatSystemEvent Component
 *
 * Renders inline system events in the chat flow like:
 * - Agent switches (routing, @mentions)
 * - Errors
 * - Status updates
 *
 * These appear as subtle, centered cards that maintain conversation context.
 */

import { Bot, AlertCircle, ArrowRight, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export type SystemEventType = "agent_switch" | "error" | "info";

export interface SystemEvent {
	id: string;
	type: SystemEventType;
	timestamp: string;
	/** Client message ID for the user turn that caused this event. */
	turnId?: string;
	// For agent switches
	agentName?: string;
	agentId?: string;
	reason?: "routed" | "@mention";
	// For errors
	error?: string;
	// For general info
	message?: string;
}

interface ChatSystemEventProps {
	event: SystemEvent;
}

export function ChatSystemEvent({ event }: ChatSystemEventProps) {
	if (event.type === "agent_switch") {
		return <AgentSwitchEvent event={event} />;
	}

	if (event.type === "error") {
		return <ErrorEvent event={event} />;
	}

	return <InfoEvent event={event} />;
}

function AgentSwitchEvent({ event }: { event: SystemEvent }) {
	const isRouted = event.reason === "routed";

	return (
		<div className="flex justify-center py-3 px-4">
			<div
				className={cn(
					"inline-flex items-center gap-2 px-4 py-2 rounded-full",
					"bg-primary/10 text-primary text-sm",
					"border border-primary/20",
				)}
			>
				{isRouted ? (
					<>
						<Sparkles className="h-3.5 w-3.5" />
						<span className="text-muted-foreground">Routed to</span>
					</>
				) : (
					<>
						<ArrowRight className="h-3.5 w-3.5" />
						<span className="text-muted-foreground">
							Switched to
						</span>
					</>
				)}
				<span className="font-medium flex items-center gap-1.5">
					<Bot className="h-3.5 w-3.5" />
					{event.agentName}
				</span>
			</div>
		</div>
	);
}

function ErrorEvent({ event }: { event: SystemEvent }) {
	return (
		<div className="flex justify-center py-3 px-4">
			<div
				className={cn(
					"inline-flex items-center gap-2 px-4 py-2 rounded-lg",
					"bg-destructive/10 text-destructive text-sm",
					"border border-destructive/20",
					"max-w-[80%]",
				)}
			>
				<AlertCircle className="h-4 w-4 shrink-0" />
				<span>{event.error || "An error occurred"}</span>
			</div>
		</div>
	);
}

function InfoEvent({ event }: { event: SystemEvent }) {
	return (
		<div className="flex justify-center py-2 px-4">
			<div className="text-xs text-muted-foreground">{event.message}</div>
		</div>
	);
}
