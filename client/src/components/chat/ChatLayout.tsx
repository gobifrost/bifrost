/**
 * ChatLayout Component
 *
 * Main container for the chat UI.
 * Provides a responsive layout with sidebar and chat window.
 */

import { useState, useEffect, useRef } from "react";
import { PanelLeftClose, PanelLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChatSidebar } from "./ChatSidebar";
import { ChatWindow } from "./ChatWindow";
import { ArtifactsLibrary } from "./ArtifactsLibrary";
import { useChatStore } from "@/stores/chatStore";
import { useConversation } from "@/hooks/useChat";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";

interface ChatLayoutProps {
	initialConversationId?: string;
	view?: "chat" | "artifacts";
}

export function ChatLayout({
	initialConversationId,
	view = "chat",
}: ChatLayoutProps) {
	const sidebarTriggerRef = useRef<HTMLButtonElement>(null);
	const isDesktop = useMediaQuery("(min-width: 1024px)");
	const [sidebarState, setSidebarState] = useState<
		"auto" | "open" | "closed"
	>("auto");
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

	return (
		<div className="flex h-full min-h-0 overflow-hidden bg-background">
			{isDesktop ? (
				isSidebarOpen && (
					<aside
						className="relative h-full w-80 shrink-0 border-r bg-background"
						aria-label="Chat navigation"
					>
						<ChatSidebar className="w-full" />
						<Button
							type="button"
							variant="ghost"
							size="icon-sm"
							aria-label="Close chat sidebar"
							className="absolute top-2 right-2 size-11"
							onClick={() => setSidebarState("closed")}
						>
							<PanelLeftClose className="h-4 w-4" />
						</Button>
					</aside>
				)
			) : (
				<Sheet
					open={isSidebarOpen}
					onOpenChange={(open) =>
						setSidebarState(open ? "open" : "closed")
					}
				>
					<SheetContent
						side="left"
						showCloseButton={false}
						className="w-80 max-w-[calc(100vw-2rem)] p-0"
						aria-describedby={undefined}
						onCloseAutoFocus={(event) => {
							event.preventDefault();
							sidebarTriggerRef.current?.focus();
						}}
					>
						<SheetTitle className="sr-only">
							Chat navigation
						</SheetTitle>
						<ChatSidebar
							className="w-full"
							onClose={() => setSidebarState("closed")}
							onConversationSelected={() =>
								setSidebarState("closed")
							}
						/>
					</SheetContent>
				</Sheet>
			)}

			{/* Main Content */}
			<div className="flex-1 min-h-0 flex flex-col min-w-0 overflow-hidden">
				{view === "artifacts" && (!isSidebarOpen || !isDesktop) && (
					<header className="flex h-14 shrink-0 items-center gap-3 border-b px-4">
						<Button
							variant="ghost"
							size="icon-sm"
							className="-ml-2 size-11"
							ref={sidebarTriggerRef}
							aria-label="Open chat sidebar"
							onClick={() => setSidebarState("open")}
						>
							<PanelLeft className="h-4 w-4" />
						</Button>
						<span className="text-sm font-medium">Chat</span>
					</header>
				)}
				{/* Header - always show when sidebar is closed or conversation is active */}
				{view === "chat" &&
					(!isSidebarOpen || !isDesktop || activeConversationId) && (
						<header className="min-h-16 shrink-0 border-b flex items-start px-4 py-3 gap-3 relative z-10 sm:gap-4">
							{(!isSidebarOpen || !isDesktop) && (
								<Button
									variant="ghost"
									size="icon-sm"
									className="-ml-2 size-11 sm:ml-0"
									ref={sidebarTriggerRef}
									aria-label="Open chat sidebar"
									onClick={() => setSidebarState("open")}
								>
									<PanelLeft className="h-4 w-4" />
								</Button>
							)}
							{activeConversationId && (
								<>
									<div className="flex-1 min-w-0">
										<h1
											className="line-clamp-2 font-display font-medium [overflow-wrap:anywhere] sm:line-clamp-none"
											title={
												conversation?.title ||
												conversation?.agent_name ||
												"Chat"
											}
										>
											{conversation?.title ||
												conversation?.agent_name ||
												"Chat"}
										</h1>
										{conversation?.agent_name &&
											conversation?.title && (
												<p
													className="line-clamp-1 text-xs text-muted-foreground [overflow-wrap:anywhere] sm:line-clamp-none"
													title={
														conversation.agent_name
													}
												>
													with{" "}
													{conversation.agent_name}
												</p>
											)}
									</div>
								</>
							)}
						</header>
					)}

				{view === "artifacts" ? (
					<ArtifactsLibrary />
				) : (
					<ChatWindow
						conversationId={activeConversationId ?? undefined}
						agentName={conversation?.agent_name}
					/>
				)}
			</div>
		</div>
	);
}
