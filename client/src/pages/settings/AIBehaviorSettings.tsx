import { useState } from "react";
import { Loader2, MessageSquareText, Save } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { $api } from "@/lib/api-client";

export function AIBehaviorSettings() {
	const { data, isLoading, refetch } = $api.useQuery("get", "/api/admin/ai/behavior");
	const update = $api.useMutation("put", "/api/admin/ai/behavior");
	const [prompt, setPrompt] = useState("");
	const [loadedData, setLoadedData] = useState<typeof data>(undefined);
	if (data !== loadedData) {
		setLoadedData(data);
		setPrompt(data?.default_system_prompt ?? "");
	}

	const save = async () => {
		try {
			await update.mutateAsync({ body: { default_system_prompt: prompt || null } });
			await refetch();
			toast.success("Chat instructions saved");
		} catch (error) {
			toast.error(error instanceof Error ? error.message : "Could not save Chat instructions");
		}
	};

	return (
		<div className="max-w-3xl space-y-6">
			<div><h2 className="text-2xl font-semibold tracking-tight">Chat instructions</h2><p className="mt-1 text-sm text-muted-foreground">Set the default behavior for agentless conversations. Agent prompts remain configured on each agent.</p></div>
			<Card>
				<CardHeader><div className="flex items-start gap-3"><div className="rounded-md bg-muted p-2"><MessageSquareText className="h-4 w-4" /></div><div><CardTitle className="text-base">Default system instructions</CardTitle><CardDescription>Applied when a conversation is not using a configured agent.</CardDescription></div></div></CardHeader>
				<CardContent className="space-y-4">
					<div className="space-y-2"><Label htmlFor="default-chat-instructions">Instructions</Label><Textarea id="default-chat-instructions" value={prompt} onChange={(event) => setPrompt(event.target.value)} disabled={isLoading} className="min-h-48" placeholder="Describe how the assistant should behave…" /></div>
					<div className="flex justify-end"><Button onClick={() => void save()} disabled={isLoading || update.isPending}>{update.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}Save instructions</Button></div>
				</CardContent>
			</Card>
		</div>
	);
}
