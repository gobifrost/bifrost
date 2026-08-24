import { useMemo, useState } from "react";
import { Database, Loader2, Save } from "lucide-react";
import { toast } from "sonner";

import { ProviderModelField } from "@/components/ai/ProviderModelField";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { $api } from "@/lib/api-client";

export function AIEmbeddingSettings() {
	const { data: connections = [], isLoading: loadingConnections } =
		$api.useQuery("get", "/api/admin/ai/connections");
	const {
		data: config,
		isLoading: loadingConfig,
		refetch,
	} = $api.useQuery("get", "/api/admin/llm/embedding-config");
	const saveEmbedding = $api.useMutation(
		"post",
		"/api/admin/llm/embedding-config",
	);
	const [connectionId, setConnectionId] = useState("");
	const [model, setModel] = useState("");
	const [confirmReindex, setConfirmReindex] = useState(false);
	const [loadedConfig, setLoadedConfig] = useState<typeof config>(undefined);
	const compatibleConnections = useMemo(
		() =>
			connections.filter(
				(connection) =>
					connection.provider === "openai" ||
					connection.provider === "openrouter" ||
					connection.provider === "openai_compatible",
			),
		[connections],
	);

	if (config !== loadedConfig) {
		setLoadedConfig(config);
		setConnectionId(config?.connection_id ?? "");
		setModel(config?.model ?? "");
	}

	const save = async (confirmed = false) => {
		if (!connectionId || !model.trim()) return;
		try {
			const result = await saveEmbedding.mutateAsync({
				body: {
					connection_id: connectionId,
					model: model.trim(),
					confirm_reindex: confirmed,
				},
			});
			if (result.needs_reindex_confirmation) {
				setConfirmReindex(true);
				return;
			}
			setConfirmReindex(false);
			await refetch();
			toast.success(
				result.notification_id
					? "Embedding configuration saved; reindexing has started"
					: "Embedding configuration saved",
			);
		} catch (error) {
			toast.error(
				error instanceof Error
					? error.message
					: "Could not save embedding configuration",
			);
		}
	};

	const loading = loadingConnections || loadingConfig;
	return (
		<div className="max-w-3xl space-y-6 pb-8">
			<div>
				<h2 className="text-2xl font-semibold tracking-tight">
					Embeddings
				</h2>
				<p className="mt-1 text-sm text-muted-foreground">
					Choose a provider connection and model directly. Embeddings
					stay separate from reusable chat and agent profiles.
				</p>
			</div>
			<Card>
				<CardHeader>
					<div className="flex items-start gap-3">
						<div className="rounded-md bg-muted p-2">
							<Database className="h-4 w-4" />
						</div>
						<div>
							<CardTitle className="text-base">
								Knowledge embeddings
							</CardTitle>
							<CardDescription>
								Changing models may require re-embedding
								existing knowledge.
							</CardDescription>
						</div>
					</div>
				</CardHeader>
				<CardContent className="space-y-5">
					{compatibleConnections.length === 0 && !loading && (
						<Alert>
							<AlertTitle>
								No compatible provider connection
							</AlertTitle>
							<AlertDescription>
								Add an OpenAI, OpenRouter, or OpenAI-compatible
								connection first.
							</AlertDescription>
						</Alert>
					)}
					<div className="space-y-2">
						<Label htmlFor="embedding-connection">
							Provider connection
						</Label>
						<Select
							value={connectionId}
							onValueChange={(nextConnectionId) => {
								setConnectionId(nextConnectionId);
								if (nextConnectionId !== connectionId)
									setModel("");
							}}
							disabled={loading}
						>
							<SelectTrigger id="embedding-connection">
								<SelectValue placeholder="Select a connection" />
							</SelectTrigger>
							<SelectContent>
								{compatibleConnections.map((connection) => (
									<SelectItem
										key={connection.id}
										value={connection.id}
									>
										{connection.name} ·{" "}
										{connection.provider}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
					</div>
					<ProviderModelField
						id="embedding-model"
						connectionId={connectionId}
						value={model}
						onValueChange={setModel}
					/>
					<div className="flex justify-end">
						<Button
							onClick={() => void save()}
							disabled={
								!connectionId ||
								!model.trim() ||
								saveEmbedding.isPending
							}
						>
							{saveEmbedding.isPending ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							) : (
								<Save className="mr-2 h-4 w-4" />
							)}
							Save embeddings
						</Button>
					</div>
				</CardContent>
			</Card>
			<AlertDialog open={confirmReindex} onOpenChange={setConfirmReindex}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Re-embed existing knowledge?
						</AlertDialogTitle>
						<AlertDialogDescription>
							This model is incompatible with some stored vectors.
							Saving will start a background reindex so knowledge
							search remains accurate.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={() => void save(true)}>
							Save and reindex
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
