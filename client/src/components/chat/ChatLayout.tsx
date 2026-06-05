/**
 * ChatLayout Component
 *
 * Main container for the chat UI.
 * Provides a responsive layout with sidebar and chat window.
 */

import { useState, useEffect } from "react";
import { PanelLeftClose, PanelLeft, Cpu, DollarSign } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChatSidebar } from "./ChatSidebar";
import { ChatWindow } from "./ChatWindow";
import { useChatStore } from "@/stores/chatStore";
import { useConversation, useConversationStats } from "@/hooks/useChat";
import { useUserPermissions } from "@/hooks/useUserPermissions";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";

interface ChatLayoutProps {
	initialConversationId?: string;
}

export function ChatLayout({
	initialConversationId,
}: ChatLayoutProps) {
	const isDesktop = useMediaQuery("(min-width: 1024px)");
	const [sidebarState, setSidebarState] = useState<"auto" | "open" | "closed">(
		"auto",
	);
	const isSidebarOpen =
		sidebarState === "auto" ? isDesktop : sidebarState === "open";

	// Get active conversation from store
	const activeConversationId = useChatStore(
		(state) => state.activeConversationId,
	);
	const setActiveConversation = useChatStore(
		(state) => state.setActiveConversation,
	);

	// Set initial conversation if provided (in effect, not during render)
	useEffect(() => {
		if (initialConversationId && !activeConversationId) {
			setActiveConversation(initialConversationId);
		}
	}, [initialConversationId, activeConversationId, setActiveConversation]);

	// Get conversation details for header
	const { data: conversation } = useConversation(
		activeConversationId ?? undefined,
	);

	// Get conversation stats for platform admins
	const conversationStats = useConversationStats(
		activeConversationId ?? undefined,
	);
	const { isPlatformAdmin } = useUserPermissions();

	// Format token count for display (e.g., 12450 -> "12.5k")
	const formatTokens = (count: number): string => {
		if (count >= 1_000_000) {
			return `${(count / 1_000_000).toFixed(1)}M`;
		}
		if (count >= 1_000) {
			return `${(count / 1_000).toFixed(1)}k`;
		}
		return count.toString();
	};

	// Format cost for display
	const formatCost = (cost: number): string => {
		if (cost < 0.01) {
			return `$${cost.toFixed(4)}`;
		}
		return `$${cost.toFixed(2)}`;
	};

	return (
		<div className="flex h-full min-h-0 overflow-hidden bg-background">
			{/* Sidebar */}
			<div
				className={cn(
					"shrink-0 bg-background border-r shadow-xl transition-all duration-200 ease-in-out",
					isSidebarOpen
						? "fixed inset-y-0 left-0 z-50 translate-x-0 lg:relative lg:inset-auto lg:z-auto"
						: "w-0 -translate-x-full overflow-hidden lg:opacity-0",
				)}
				style={
					isSidebarOpen
						? {
								width: "20rem",
								maxWidth: "calc(100vw - 2rem)",
							}
						: undefined
				}
			>
				<div className="relative h-full">
					<ChatSidebar
						className="w-full"
						onClose={() => setSidebarState("closed")}
						onConversationSelected={() => {
							if (!isDesktop) {
								setSidebarState("closed");
							}
						}}
					/>
					{/* Close button (Desktop) */}
					<Button
						variant="ghost"
						size="icon-sm"
						aria-label="Close chat sidebar"
						className="absolute top-4 right-2 hidden lg:flex"
						onClick={() => setSidebarState("closed")}
					>
						<PanelLeftClose className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Mobile Overlay */}
			{isSidebarOpen && (
				<div
					className="fixed inset-0 bg-black/50 z-40 lg:hidden"
					onClick={() => setSidebarState("closed")}
				/>
			)}

			{/* Main Content */}
			<div className="flex-1 min-h-0 flex flex-col min-w-0 overflow-hidden">
				{/* Header - always show when sidebar is closed or conversation is active */}
				{(!isSidebarOpen || activeConversationId) && (
					<header className="h-14 border-b flex items-center px-4 gap-4 relative z-10">
						{!isSidebarOpen && (
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label="Open chat sidebar"
								onClick={() => setSidebarState("open")}
							>
								<PanelLeft className="h-4 w-4" />
							</Button>
						)}
						{activeConversationId && (
							<>
								<div className="flex-1 min-w-0">
									<h1 className="font-medium truncate">
										{conversation?.title ||
											conversation?.agent_name ||
											"Chat"}
									</h1>
									{conversation?.agent_name &&
										conversation?.title && (
											<p className="text-xs text-muted-foreground truncate">
												with {conversation.agent_name}
											</p>
										)}
								</div>

								{/* Platform Admin: Token/Cost Stats */}
								{isPlatformAdmin &&
									conversationStats &&
									conversationStats.totalTokens > 0 && (
										<div className="hidden sm:flex items-center gap-3 text-xs text-muted-foreground shrink-0">
											{/* Model */}
											{conversationStats.model && (
												<div
													className="flex items-center gap-1"
													title="Model"
												>
													<Cpu className="h-3 w-3" />
													<span className="font-mono">
														{conversationStats.model
															.split("-")
															.slice(0, 2)
															.join("-")}
													</span>
												</div>
											)}
											{/* Tokens */}
											<div
												className="flex items-center gap-1"
												title={`Input: ${conversationStats.totalInputTokens.toLocaleString()} | Output: ${conversationStats.totalOutputTokens.toLocaleString()}`}
											>
												<span className="font-mono">
													{formatTokens(
														conversationStats.totalTokens,
													)}{" "}
													tokens
												</span>
											</div>
											{/* Cost */}
											{conversationStats.estimatedCostUsd !==
												null && (
												<div
													className="flex items-center gap-1"
													title="Estimated cost"
												>
													<DollarSign className="h-3 w-3" />
													<span className="font-mono">
														{formatCost(
															conversationStats.estimatedCostUsd,
														)}
													</span>
												</div>
											)}
										</div>
									)}
							</>
						)}
					</header>
				)}

				{/* Chat Window */}
				<ChatWindow
					conversationId={activeConversationId ?? undefined}
					agentName={conversation?.agent_name}
				/>
			</div>
		</div>
	);
}
