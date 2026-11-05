import { useState } from "react";
import { Loader2, MessageSquareText, Save } from "lucide-react";
import { toast } from "sonner";

import { SettingsLoadError } from "@/components/shared/SettingsLoadError";
import { SettingsToggleRow } from "@/components/shared/SettingsToggleRow";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { $api } from "@/lib/api-client";

export function AIBehaviorSettings() {
	const { data, isLoading, isError, isFetching, refetch } = $api.useQuery(
		"get",
		"/api/admin/ai/behavior",
	);
	const update = $api.useMutation("put", "/api/admin/ai/behavior");
	const [prompt, setPrompt] = useState("");
	const [dirty, setDirty] = useState(false);
	const [saveError, setSaveError] = useState(false);
	const [executionMode, setExecutionMode] = useState<
		"delegated" | "automatic" | null
	>(null);
	const [savingExecutionMode, setSavingExecutionMode] = useState(false);
	const [executionModeError, setExecutionModeError] = useState(false);
	const [loadedData, setLoadedData] = useState<typeof data>(undefined);
	const [loadedExecutionMode, setLoadedExecutionMode] = useState<
		"delegated" | "automatic" | undefined
	>(undefined);
	if (data !== loadedData) {
		setLoadedData(data);
		if (!dirty) setPrompt(data?.default_system_prompt ?? "");
	}
	if (data?.agent_execution_mode !== loadedExecutionMode) {
		setLoadedExecutionMode(data?.agent_execution_mode);
		if (!savingExecutionMode && !executionModeError) setExecutionMode(null);
	}

	const currentExecutionMode = executionMode ?? data?.agent_execution_mode;

	const save = async () => {
		if (!data || update.isPending) return;
		setSaveError(false);
		try {
			await update.mutateAsync({
				body: { default_system_prompt: prompt || null },
			});
			setDirty(false);
			const refreshed = await refetch();
			if (!refreshed.error) setExecutionMode(null);
			toast.success("Chat instructions saved");
		} catch {
			setSaveError(true);
		}
	};

	const updateExecutionMode = async (automatic: boolean) => {
		if (!data || savingExecutionMode || update.isPending) return;
		const previousMode = executionMode ?? data.agent_execution_mode;
		const nextMode = automatic ? "automatic" : "delegated";
		setExecutionMode(nextMode);
		setExecutionModeError(false);
		setSavingExecutionMode(true);
		try {
			const response = await update.mutateAsync({
				body: { agent_execution_mode: nextMode },
			});
			setExecutionMode(response.agent_execution_mode);
			void refetch();
			toast.success(
				automatic
					? "Automatic agent execution enabled"
					: "Automatic agent execution disabled",
			);
		} catch {
			setExecutionMode(previousMode);
			setExecutionModeError(true);
		} finally {
			setSavingExecutionMode(false);
		}
	};

	const reloadExecutionMode = async () => {
		const refreshed = await refetch();
		if (!refreshed.error) {
			setExecutionMode(null);
			setExecutionModeError(false);
		}
	};

	return (
		<div className="min-w-0 w-full space-y-6">
			<div>
				<h2 className="font-display text-2xl font-semibold tracking-tight">
					AI Behavior
				</h2>
				<p className="mt-1 text-sm text-muted-foreground">
					Set platform-wide agent execution and default instructions
					for agentless conversations.
				</p>
			</div>
			{isError &&
				(data ? (
					<div
						role="alert"
						className="space-y-3 rounded-[var(--bf-radius-surface)] border bg-[var(--bf-warning-soft)] p-4 text-sm"
					>
						<p>
							Could not refresh AI behavior. Your current draft is
							still available.
						</p>
						<Button
							variant="outline"
							className="min-h-11"
							disabled={isFetching}
							onClick={() => void refetch()}
						>
							{isFetching ? "Retrying…" : "Retry AI behavior"}
						</Button>
					</div>
				) : (
					<SettingsLoadError
						name="AI behavior"
						onRetry={() => void refetch()}
					/>
				))}
			<Card className="min-w-0">
				<CardHeader>
					<CardTitle className="text-base">Agent Execution</CardTitle>
					<CardDescription>
						Experimental. This platform-wide setting applies to new
						agent runs.
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-3">
					<SettingsToggleRow
						id="automatic-agent-execution"
						label="Automatic Agent Execution"
						description="When enabled, the lead agent can load specialist guidance and use approved tools directly. It can still start isolated child agents when helpful. When off (the default), specialist tools are used through child agents. Entry tools are available in both modes."
						checked={currentExecutionMode === "automatic"}
						disabled={
							!data || isLoading || savingExecutionMode || update.isPending
						}
						busy={
							isLoading
								? "loading"
								: savingExecutionMode
									? "saving"
									: undefined
						}
						onChange={(checked) =>
							void updateExecutionMode(checked)
						}
					/>
					{executionModeError && (
						<div
							role="alert"
							className="flex flex-wrap items-center justify-between gap-3 text-sm text-destructive"
						>
							<p>
								Could not confirm the setting was saved. Reload to
								check the current setting, then try again.
							</p>
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								disabled={isFetching}
								onClick={() => void reloadExecutionMode()}
							>
								Reload Setting
							</Button>
						</div>
					)}
				</CardContent>
			</Card>
			<Card className="min-w-0">
				<CardHeader>
					<div className="flex items-start gap-3">
						<div className="rounded-[var(--bf-radius-control)] bg-muted p-2">
							<MessageSquareText className="h-4 w-4" />
						</div>
						<div className="min-w-0">
							<CardTitle className="text-base">
								Default system instructions
							</CardTitle>
							<CardDescription>
								Applied when a conversation is not using a
								configured agent.
							</CardDescription>
						</div>
					</div>
				</CardHeader>
				<CardContent>
					<form
						className="space-y-4"
						onSubmit={(event) => {
							event.preventDefault();
							void save();
						}}
					>
						{isLoading && (
							<p
								role="status"
								className="text-sm text-muted-foreground"
							>
								Loading instructions…
							</p>
						)}
						<div className="space-y-2">
							<Label htmlFor="default-chat-instructions">
								Instructions
							</Label>
							<Textarea
								id="default-chat-instructions"
								value={prompt}
								onChange={(event) => {
									setPrompt(event.target.value);
									setDirty(true);
									setSaveError(false);
								}}
								disabled={
									!data || isLoading || update.isPending
								}
								className="min-h-64 text-base leading-relaxed sm:text-sm"
								placeholder="Describe how the assistant should behave…"
							/>
						</div>
						{saveError && (
							<p
								role="alert"
								className="text-sm text-destructive"
							>
								Could not save instructions. Your draft is
								preserved; try again.
							</p>
						)}
						<div className="flex flex-wrap items-center justify-between gap-3">
							<p className="text-xs text-muted-foreground">
								{dirty
									? "Unsaved changes"
									: "Leave blank to use the default instructions."}
							</p>
							<Button
								type="submit"
								className="min-h-11 w-full sm:w-auto"
								disabled={
									!data || isLoading || update.isPending
								}
							>
								{update.isPending ? (
									<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
								) : (
									<Save className="mr-2 h-4 w-4" />
								)}
								Save instructions
							</Button>
						</div>
					</form>
				</CardContent>
			</Card>
		</div>
	);
}
