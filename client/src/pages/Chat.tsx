/**
 * Chat Page
 *
 * Main chat interface for interacting with AI agents.
 * Supports conversation management and real-time streaming.
 */

import { useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { Bot, Settings } from "lucide-react";
import { ChatLayout } from "@/components/chat";
import { useChatStore } from "@/stores/chatStore";
import { useLLMConfig } from "@/hooks/useLLMConfig";
import { Button } from "@/components/ui/button";
import { PageLoader } from "@/components/PageLoader";

export function Chat() {
	const { conversationId } = useParams<{ conversationId?: string }>();
	const { setActiveConversation, reset } = useChatStore();
	const {
		isConfigured,
		isPlatformAdmin,
		isLoading: configLoading,
	} = useLLMConfig();

	// Set active conversation from URL param
	useEffect(() => {
		setActiveConversation(conversationId ?? null);
	}, [conversationId, setActiveConversation]);

	// Reset store on unmount
	useEffect(() => {
		return () => {
			reset();
		};
	}, [reset]);

	// Show loading while checking config
	if (configLoading) {
		return <PageLoader message="Loading chat..." />;
	}

	// LLM not configured - show setup prompt
	if (isPlatformAdmin && isConfigured === false) {
		return (
			<div className="h-full flex items-center justify-center">
				<div className="max-w-md text-center space-y-6 p-8">
					<div className="mx-auto w-16 h-16 rounded-full bg-muted flex items-center justify-center">
						<Bot className="h-8 w-8 text-muted-foreground" />
					</div>
					<div className="space-y-2">
						<h1 className="text-2xl font-semibold">
							AI Chat Not Configured
						</h1>
						<p className="text-muted-foreground">
							To enable AI chat, you need to configure an LLM
							provider (OpenAI or Anthropic) with a valid API key.
						</p>
					</div>
					<Button asChild>
						<Link to="/settings/ai">
							<Settings className="h-4 w-4 mr-2" />
							Configure AI Provider
						</Link>
					</Button>
				</div>
			</div>
		);
	}

	// Non-admin and chat might not work - they'll see errors when trying
	// For now, we let them through and errors will be handled by the chat components

	return (
		<div className="h-full">
			<ChatLayout initialConversationId={conversationId} />
		</div>
	);
}

export default Chat;
