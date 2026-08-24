import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Bot,
	Check,
	Loader2,
	MessageSquareText,
	Plus,
	Sparkles,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	createModelProfile,
	listModelProfiles,
	listProviderConnections,
	type AIModelProfile,
} from "@/services/aiModels";

const PROFILE_QUERY_KEY = ["ai", "model-profiles"] as const;
const CONNECTION_QUERY_KEY = ["ai", "provider-connections"] as const;

export interface ModelProfileSelectorProps {
	id?: string;
	label?: string;
	value?: string | null;
	onValueChange: (profileId: string) => void;
	placeholder?: string;
	disabled?: boolean;
	chatOnly?: boolean;
	isSaving?: boolean;
}

function profileDescription(profile: AIModelProfile): string {
	return `${profile.connection.name} · ${profile.model}`;
}

export function ModelProfileSelector({
	id,
	label = "Model Profile",
	value,
	onValueChange,
	placeholder = "Select a profile",
	disabled = false,
	chatOnly = false,
	isSaving = false,
}: ModelProfileSelectorProps) {
	const [creating, setCreating] = useState(false);
	const [newName, setNewName] = useState("");
	const [newConnectionId, setNewConnectionId] = useState("");
	const [newModel, setNewModel] = useState("");
	const queryClient = useQueryClient();

	const profilesQuery = useQuery({
		queryKey: PROFILE_QUERY_KEY,
		queryFn: listModelProfiles,
	});
	const connectionsQuery = useQuery({
		queryKey: CONNECTION_QUERY_KEY,
		queryFn: listProviderConnections,
	});

	const profiles = useMemo(
		() =>
			(profilesQuery.data ?? []).filter(
				(profile) => !chatOnly || profile.enabled_for_chat,
			),
		[chatOnly, profilesQuery.data],
	);
	const profileOptions = profiles.map((profile) => ({
		value: profile.id,
		label: profile.name,
		description: profileDescription(profile),
	}));
	const connections = connectionsQuery.data ?? [];
	const canCreate = connections.length > 0;

	const createMutation = useMutation({
		mutationFn: createModelProfile,
		onSuccess: (profile) => {
			queryClient.setQueryData<AIModelProfile[]>(
				PROFILE_QUERY_KEY,
				(existing = []) => [...existing, profile],
			);
			onValueChange(profile.id);
			setCreating(false);
			setNewName("");
			setNewConnectionId("");
			setNewModel("");
			toast.success("Model profile created");
		},
		onError: (error) => {
			toast.error("Could not create profile", {
				description:
					error instanceof Error
						? error.message
						: "Check the provider connection and model name.",
			});
		},
	});

	const selectedConnection = connections.find(
		(connection) => connection.id === newConnectionId,
	);
	const inferredName =
		newName.trim() ||
		[selectedConnection?.name, newModel.trim()].filter(Boolean).join(" · ");
	const formReady =
		Boolean(newConnectionId) &&
		Boolean(newModel.trim()) &&
		Boolean(inferredName);

	const submitCreate = () => {
		if (!formReady) return;
		createMutation.mutate({
			name: inferredName,
			connection_id: newConnectionId,
			model: newModel.trim(),
			capabilities: null,
			enabled_for_chat: chatOnly,
		});
	};

	return (
		<div className="space-y-2">
			<div className="flex items-center justify-between gap-3">
				<Label htmlFor={id}>{label}</Label>
				{isSaving ? (
					<span
						className="flex animate-in items-center gap-1.5 text-xs text-muted-foreground fade-in-0 motion-reduce:animate-none"
						role="status"
					>
						<Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
						Saving assignment…
					</span>
				) : (
					<Button
						type="button"
						variant="ghost"
						size="sm"
						className="h-7 gap-1.5 px-2 text-xs"
						onClick={() => setCreating(true)}
						disabled={disabled || connectionsQuery.isLoading}
					>
						<Plus className="h-3.5 w-3.5" />
						Create profile
					</Button>
				)}
			</div>
			<Combobox
				id={id}
				value={value ?? ""}
				onValueChange={(nextValue) => {
					if (nextValue) onValueChange(nextValue);
				}}
				options={profileOptions}
				placeholder={placeholder}
				searchPlaceholder="Search profiles..."
				emptyText={
					chatOnly
						? "No chat-enabled profiles found."
						: "No profiles found."
				}
				isLoading={profilesQuery.isLoading}
				disabled={disabled || isSaving}
			/>

			<Dialog open={creating} onOpenChange={setCreating}>
				<DialogContent className="sm:max-w-[560px]">
					<DialogHeader>
						<div className="mb-2 flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
							<Sparkles className="h-4 w-4" />
						</div>
						<DialogTitle>Create Model Profile</DialogTitle>
						<DialogDescription>
							Profiles are reusable model choices. Assign this
							profile wherever Bifrost needs a model.
						</DialogDescription>
					</DialogHeader>

					{canCreate ? (
						<div className="grid gap-4 py-2">
							<div className="space-y-2">
								<Label htmlFor="model-profile-name">
									Profile Name
								</Label>
								<Input
									id="model-profile-name"
									value={newName}
									onChange={(event) =>
										setNewName(event.target.value)
									}
									placeholder="Support Chat"
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="model-profile-connection">
									Provider Connection
								</Label>
								<Select
									value={newConnectionId}
									onValueChange={setNewConnectionId}
								>
									<SelectTrigger
										id="model-profile-connection"
										className="w-full rounded-lg"
									>
										<SelectValue placeholder="Select a provider connection" />
									</SelectTrigger>
									<SelectContent>
										{connections.map((connection) => (
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
							<div className="space-y-2">
								<Label htmlFor="model-profile-model">
									Model
								</Label>
								<Input
									id="model-profile-model"
									value={newModel}
									onChange={(event) =>
										setNewModel(event.target.value)
									}
									placeholder="gpt-5-mini"
								/>
							</div>
							{chatOnly && (
								<div className="flex items-start gap-2 rounded-lg bg-muted/60 p-3 text-sm">
									<MessageSquareText className="mt-0.5 h-4 w-4 text-primary" />
									<p className="text-muted-foreground">
										New profiles created here are enabled
										for chat.
									</p>
								</div>
							)}
						</div>
					) : (
						<div className="flex items-start gap-3 rounded-lg border bg-muted/40 p-4">
							<Bot className="mt-0.5 h-4 w-4 text-muted-foreground" />
							<div>
								<p className="text-sm font-medium">
									Create a provider connection first
								</p>
								<p className="mt-1 text-sm text-muted-foreground">
									Profiles need a saved provider connection
									before they can be reused.
								</p>
							</div>
						</div>
					)}

					<DialogFooter>
						<Button
							type="button"
							variant="outline"
							onClick={() => setCreating(false)}
						>
							Cancel
						</Button>
						<Button
							type="button"
							onClick={submitCreate}
							disabled={!formReady || createMutation.isPending}
						>
							{createMutation.isPending ? (
								<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
							) : (
								<Check className="h-4 w-4" />
							)}
							{createMutation.isPending ? "Creating…" : "Create"}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
