/**
 * ChatSidebar Component
 *
 * Left sidebar showing:
 * - List of available agents
 * - Recent conversations
 * - New conversation button
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, MessageSquare, Trash2, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chatStore";
import { useConversations, useDeleteConversation } from "@/hooks/useChat";
import type { ConversationSummary } from "@/hooks/useChat";

interface ChatSidebarProps {
	className?: string;
	onClose?: () => void;
	onConversationSelected?: () => void;
}

export function ChatSidebar({
	className,
	onClose,
	onConversationSelected,
}: ChatSidebarProps) {
	const navigate = useNavigate();
	const [searchTerm, setSearchTerm] = useState("");
	const [deleteTarget, setDeleteTarget] =
		useState<ConversationSummary | null>(null);

	// Store state
	const { activeConversationId, setActiveConversation, setActiveAgent } =
		useChatStore();

	// API hooks
	const { data: conversations, isLoading: isLoadingConversations } =
		useConversations();
	const deleteConversation = useDeleteConversation();

	// Filter conversations by search term
	const filteredConversations = conversations?.filter((conv) => {
		if (!searchTerm) return true;
		const term = searchTerm.toLowerCase();
		return (
			conv.title?.toLowerCase().includes(term) ||
			conv.agent_name?.toLowerCase().includes(term) ||
			conv.last_message_preview?.toLowerCase().includes(term)
		);
	});

	// Handle starting new conversation
	const handleNewChat = () => {
		setActiveConversation(null);
		setActiveAgent(null);
		navigate("/chat");
		onConversationSelected?.();
	};

	// Handle selecting existing conversation
	const handleSelectConversation = (conv: ConversationSummary) => {
		setActiveConversation(conv.id);
		setActiveAgent(conv.agent_id ?? null);
		// Update URL to enable bookmarking/sharing
		navigate(`/chat/${conv.id}`);
		onConversationSelected?.();
	};

	// Handle delete confirmation
	const handleDeleteConfirm = () => {
		if (deleteTarget) {
			const wasActive = activeConversationId === deleteTarget.id;
			deleteConversation.mutate({
				params: { path: { conversation_id: deleteTarget.id } },
			});
			setDeleteTarget(null);
			// Navigate to clean /chat URL so it looks like a fresh screen
			if (wasActive) {
				navigate("/chat");
			}
		}
	};

	// Format relative time
	const formatTime = (dateStr: string) => {
		const date = new Date(dateStr);
		const now = new Date();
		const diffMs = now.getTime() - date.getTime();
		const diffMins = Math.floor(diffMs / 60000);
		const diffHours = Math.floor(diffMs / 3600000);
		const diffDays = Math.floor(diffMs / 86400000);

		if (diffMins < 1) return "now";
		if (diffMins < 60) return `${diffMins}m`;
		if (diffHours < 24) return `${diffHours}h`;
		if (diffDays < 7) return `${diffDays}d`;
		return date.toLocaleDateString();
	};

	return (
		<div
			className={cn(
				"flex flex-col h-full bg-background border-r",
				className,
			)}
		>
			{/* Header */}
			<div className="p-4 border-b space-y-3">
				<div className="flex items-center justify-between">
					<h2 className="font-semibold text-lg">Chat</h2>
					{onClose && (
						<Button
							variant="ghost"
							size="icon-sm"
							className="lg:hidden"
							onClick={onClose}
							aria-label="Close chat sidebar"
						>
							<X className="h-4 w-4" />
						</Button>
					)}
				</div>
				<Button
					className="w-full gap-2"
					onClick={handleNewChat}
				>
					<Plus className="h-4 w-4" />
					New Chat
				</Button>
				<div className="relative">
					<Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
					<Input
						placeholder="Search conversations..."
						value={searchTerm}
						onChange={(e) => setSearchTerm(e.target.value)}
						className="pl-9"
					/>
				</div>
			</div>

			<div className="flex-1 overflow-y-auto p-4">
				{/* Conversations Section */}
				<div>
					<h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">
						Recent Conversations
					</h3>
					{isLoadingConversations ? (
						<div className="space-y-2">
							{[1, 2, 3].map((i) => (
								<Skeleton key={i} className="h-14 w-full" />
							))}
						</div>
					) : filteredConversations &&
					  filteredConversations.length > 0 ? (
						<div className="space-y-1">
							{filteredConversations.map((conv) => (
								<div
									key={conv.id}
									className={cn(
										"group flex items-start gap-2 p-2 rounded-lg cursor-pointer hover:bg-accent transition-colors",
										activeConversationId === conv.id &&
											"bg-accent",
									)}
									onClick={() =>
										handleSelectConversation(conv)
									}
								>
									<MessageSquare className="h-4 w-4 mt-1 text-muted-foreground shrink-0" />
									<div className="flex-1 min-w-0">
										<div className="flex items-center justify-between gap-2">
											<span className="font-medium text-sm truncate">
												{conv.title ||
													conv.agent_name ||
													"Untitled"}
											</span>
											<span className="text-xs text-muted-foreground shrink-0">
												{formatTime(conv.updated_at)}
											</span>
										</div>
										{conv.last_message_preview && (
											<p className="text-xs text-muted-foreground truncate">
												{conv.last_message_preview}
											</p>
										)}
									</div>
									<Button
										variant="ghost"
										size="icon-sm"
										className="opacity-100 sm:opacity-0 sm:group-hover:opacity-100 transition-opacity shrink-0"
										aria-label={`Delete ${conv.title || conv.agent_name || "Untitled"}`}
										onClick={(e) => {
											e.stopPropagation();
											setDeleteTarget(conv);
										}}
									>
										<Trash2 className="h-3 w-3" />
									</Button>
								</div>
							))}
						</div>
					) : (
						<p className="text-sm text-muted-foreground py-2">
							{searchTerm
								? "No matching conversations"
								: "No conversations yet"}
						</p>
					)}
				</div>
			</div>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={!!deleteTarget}
				onOpenChange={(open) => !open && setDeleteTarget(null)}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete Conversation?
						</AlertDialogTitle>
						<AlertDialogDescription>
							This will delete the conversation "
							{deleteTarget?.title ||
								deleteTarget?.agent_name ||
								"Untitled"}
							". This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDeleteConfirm}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
