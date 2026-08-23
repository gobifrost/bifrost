import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Bot,
	Brain,
	CheckCircle2,
	CircleAlert,
	KeyRound,
	MessageSquareText,
	Pencil,
	Plus,
	RefreshCw,
	Settings2,
	ShieldCheck,
	Sparkles,
	Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { ModelProfileSelector } from "@/components/ai/ModelProfileSelector";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardAction,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
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
import { Switch } from "@/components/ui/switch";
import {
	createModelProfile,
	createProviderConnection,
	deleteModelProfile,
	deleteProviderConnection,
	listModelAssignments,
	listModelProfiles,
	listProviderConnections,
	setModelAssignment,
	testProviderConnection,
	updateProviderConnection,
	updateModelProfile,
	type AIModelAssignmentKey,
	type AIModelProfile,
	type AIProviderKind,
} from "@/services/aiModels";

const PROVIDER_QUERY_KEY = ["ai", "provider-connections"] as const;
const PROFILE_QUERY_KEY = ["ai", "model-profiles"] as const;
const ASSIGNMENT_QUERY_KEY = ["ai", "model-assignments"] as const;

const PROVIDERS: { value: AIProviderKind; label: string }[] = [
	{ value: "openai", label: "OpenAI" },
	{ value: "anthropic", label: "Anthropic" },
	{ value: "google", label: "Google" },
	{ value: "openrouter", label: "OpenRouter" },
	{ value: "openai_compatible", label: "OpenAI-compatible" },
];

const ASSIGNMENTS: {
	key: AIModelAssignmentKey;
	label: string;
	description: string;
	icon: typeof Brain;
}[] = [
	{
		key: "primary",
		label: "Primary",
		description: "Default model for general AI work.",
		icon: Brain,
	},
	{
		key: "summarization",
		label: "Summarization",
		description: "Used for transcript and run summaries.",
		icon: Sparkles,
	},
	{
		key: "tuning",
		label: "Agent Tuning",
		description: "Used by tuning and improvement workflows.",
		icon: Settings2,
	},
	{
		key: "image_generation",
		label: "Image Generation",
		description: "Dedicated profile for image generation.",
		icon: Bot,
	},
	{
		key: "video_generation",
		label: "Video Generation",
		description: "Dedicated profile for video generation.",
		icon: Bot,
	},
	{
		key: "chat_default",
		label: "Default Chat",
		description: "The selected profile when chat starts.",
		icon: MessageSquareText,
	},
];

function providerLabel(provider: AIProviderKind): string {
	return (
		PROVIDERS.find((option) => option.value === provider)?.label ?? provider
	);
}

function profileLine(profile: AIModelProfile): string {
	return `${profile.connection.name} · ${profile.model}`;
}

export function AIModelSettings() {
	const queryClient = useQueryClient();
	const [providerName, setProviderName] = useState("");
	const [providerKind, setProviderKind] = useState<AIProviderKind>("openai");
	const [providerEndpoint, setProviderEndpoint] = useState("");
	const [providerKey, setProviderKey] = useState("");
	const [profileName, setProfileName] = useState("");
	const [profileConnectionId, setProfileConnectionId] = useState("");
	const [profileModel, setProfileModel] = useState("");
	const [profileMaxTokens, setProfileMaxTokens] = useState("16384");
	const [profileChatEnabled, setProfileChatEnabled] = useState(false);
	const [providerEdit, setProviderEdit] = useState<{
		id: string;
		name: string;
		provider: AIProviderKind;
		endpoint: string;
		apiKey: string;
	} | null>(null);
	const [profileEdit, setProfileEdit] = useState<{
		id: string;
		name: string;
		connectionId: string;
		model: string;
		maxTokens: string;
	} | null>(null);

	const providersQuery = useQuery({
		queryKey: PROVIDER_QUERY_KEY,
		queryFn: listProviderConnections,
	});
	const profilesQuery = useQuery({
		queryKey: PROFILE_QUERY_KEY,
		queryFn: listModelProfiles,
	});
	const assignmentsQuery = useQuery({
		queryKey: ASSIGNMENT_QUERY_KEY,
		queryFn: listModelAssignments,
	});

	const providers = providersQuery.data ?? [];
	const profiles = profilesQuery.data ?? [];
	const assignmentsByKey = useMemo(
		() =>
			new Map(
				(assignmentsQuery.data ?? []).map((assignment) => [
					assignment.assignment_key,
					assignment,
				]),
			),
		[assignmentsQuery.data],
	);
	const chatDefaultId = assignmentsByKey.get("chat_default")?.profile_id;

	const invalidateAI = () => {
		void queryClient.invalidateQueries({ queryKey: PROVIDER_QUERY_KEY });
		void queryClient.invalidateQueries({ queryKey: PROFILE_QUERY_KEY });
		void queryClient.invalidateQueries({ queryKey: ASSIGNMENT_QUERY_KEY });
	};

	const createProviderMutation = useMutation({
		mutationFn: createProviderConnection,
		onSuccess: (connection) => {
			setProviderName("");
			setProviderEndpoint("");
			setProviderKey("");
			queryClient.setQueryData(
				PROVIDER_QUERY_KEY,
				(existing: typeof providers | undefined) => [
					...(existing ?? []),
					connection,
				],
			);
			invalidateAI();
			toast.success("Provider connection saved");
		},
		onError: (error) =>
			toast.error("Could not save provider", {
				description:
					error instanceof Error
						? error.message
						: "Check the fields and try again.",
			}),
	});

	const testProviderMutation = useMutation({
		mutationFn: testProviderConnection,
		onSuccess: (result) => {
			const count = result.models?.length ?? 0;
			toast[result.success ? "success" : "error"](
				result.success ? "Provider verified" : "Provider test failed",
				{
					description: count
						? `${result.message} ${count} models returned.`
						: result.message,
				},
			);
		},
		onError: (error) =>
			toast.error("Provider test failed", {
				description:
					error instanceof Error
						? error.message
						: "Confirm credentials and endpoint.",
			}),
	});
	const updateProviderMutation = useMutation({
		mutationFn: (edit: NonNullable<typeof providerEdit>) =>
			updateProviderConnection(edit.id, {
				name: edit.name.trim(),
				provider: edit.provider,
				endpoint: edit.endpoint.trim() || null,
				...(edit.apiKey.trim() ? { api_key: edit.apiKey } : {}),
			}),
		onSuccess: () => {
			setProviderEdit(null);
			invalidateAI();
			toast.success("Provider connection updated");
		},
		onError: (error) =>
			toast.error("Could not update provider", {
				description:
					error instanceof Error
						? error.message
						: "Check the fields and try again.",
			}),
	});

	const createProfileMutation = useMutation({
		mutationFn: createModelProfile,
		onSuccess: (profile) => {
			setProfileName("");
			setProfileConnectionId("");
			setProfileModel("");
			setProfileMaxTokens("16384");
			setProfileChatEnabled(false);
			queryClient.setQueryData(
				PROFILE_QUERY_KEY,
				(existing: typeof profiles | undefined) => [
					...(existing ?? []),
					profile,
				],
			);
			invalidateAI();
			toast.success("Model profile created");
		},
		onError: (error) =>
			toast.error("Could not create profile", {
				description:
					error instanceof Error
						? error.message
						: "Check the fields and try again.",
			}),
	});

	const updateProfileMutation = useMutation({
		mutationFn: ({
			profileId,
			enabledForChat,
		}: {
			profileId: string;
			enabledForChat: boolean;
		}) =>
			updateModelProfile(profileId, { enabled_for_chat: enabledForChat }),
		onSuccess: invalidateAI,
		onError: (error) =>
			toast.error("Could not update profile", {
				description:
					error instanceof Error
						? error.message
						: "Try again in a moment.",
			}),
	});
	const editProfileMutation = useMutation({
		mutationFn: (edit: NonNullable<typeof profileEdit>) =>
			updateModelProfile(edit.id, {
				name: edit.name.trim(),
				connection_id: edit.connectionId,
				model: edit.model.trim(),
				max_tokens: Number(edit.maxTokens),
			}),
		onSuccess: () => {
			setProfileEdit(null);
			invalidateAI();
			toast.success("Model profile updated");
		},
		onError: (error) =>
			toast.error("Could not update profile", {
				description:
					error instanceof Error
						? error.message
						: "Check the fields and try again.",
			}),
	});

	const assignMutation = useMutation({
		mutationFn: ({
			assignmentKey,
			profileId,
		}: {
			assignmentKey: AIModelAssignmentKey;
			profileId: string;
		}) => setModelAssignment(assignmentKey, profileId),
		onSuccess: invalidateAI,
		onError: (error) =>
			toast.error("Could not update assignment", {
				description:
					error instanceof Error
						? error.message
						: "Try another profile.",
			}),
	});

	const deleteProviderMutation = useMutation({
		mutationFn: deleteProviderConnection,
		onSuccess: invalidateAI,
		onError: (error) =>
			toast.error("Could not delete provider", {
				description:
					error instanceof Error
						? error.message
						: "Remove dependent profiles first.",
			}),
	});

	const deleteProfileMutation = useMutation({
		mutationFn: deleteModelProfile,
		onSuccess: invalidateAI,
		onError: (error) =>
			toast.error("Could not delete profile", {
				description:
					error instanceof Error
						? error.message
						: "Remove assignments first.",
			}),
	});

	const providerReady = providerName.trim() && providerKey.trim();
	const profileReady =
		profileName.trim() &&
		profileConnectionId &&
		profileModel.trim() &&
		Number(profileMaxTokens) > 0;

	return (
		<div className="space-y-8">
			<section className="space-y-3">
				<div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
					<div>
						<h2 className="text-xl font-semibold">
							AI Model Settings
						</h2>
						<p className="mt-1 max-w-3xl text-sm text-muted-foreground">
							Connect providers once, wrap models in reusable
							profiles, then assign those profiles to Bifrost
							features.
						</p>
					</div>
					<Badge variant="outline" className="w-fit">
						<ShieldCheck className="h-3 w-3" />
						Profiles required for assignments
					</Badge>
				</div>
			</section>

			<section className="grid gap-4 xl:grid-cols-[minmax(320px,420px)_1fr]">
				<Card className="rounded-xl">
					<CardHeader>
						<CardTitle className="flex items-center gap-2">
							<KeyRound className="h-4 w-4 text-primary" />
							Provider Connections
						</CardTitle>
						<CardDescription>
							Store each provider or compatible endpoint
							independently.
						</CardDescription>
					</CardHeader>
					<CardContent className="space-y-4">
						<div className="space-y-2">
							<Label htmlFor="ai-provider-name">
								Provider Name
							</Label>
							<Input
								id="ai-provider-name"
								value={providerName}
								onChange={(event) =>
									setProviderName(event.target.value)
								}
								placeholder="OpenRouter Production"
							/>
						</div>
						<div className="space-y-2">
							<Label htmlFor="ai-provider-kind">Provider</Label>
							<Select
								value={providerKind}
								onValueChange={(value) =>
									setProviderKind(value as AIProviderKind)
								}
							>
								<SelectTrigger
									id="ai-provider-kind"
									className="w-full rounded-lg"
								>
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									{PROVIDERS.map((provider) => (
										<SelectItem
											key={provider.value}
											value={provider.value}
										>
											{provider.label}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</div>
						<div className="space-y-2">
							<Label htmlFor="ai-provider-endpoint">
								Endpoint
							</Label>
							<Input
								id="ai-provider-endpoint"
								value={providerEndpoint}
								onChange={(event) =>
									setProviderEndpoint(event.target.value)
								}
								placeholder="Optional base URL"
							/>
						</div>
						<div className="space-y-2">
							<Label htmlFor="ai-provider-key">API Key</Label>
							<Input
								id="ai-provider-key"
								type="password"
								value={providerKey}
								onChange={(event) =>
									setProviderKey(event.target.value)
								}
								placeholder="Paste a provider key"
							/>
						</div>
						<Button
							type="button"
							className="w-full gap-2"
							disabled={
								!providerReady ||
								createProviderMutation.isPending
							}
							onClick={() =>
								createProviderMutation.mutate({
									name: providerName.trim(),
									provider: providerKind,
									api_key: providerKey,
									endpoint: providerEndpoint.trim() || null,
								})
							}
						>
							<Plus className="h-4 w-4" />
							Add Provider
						</Button>
					</CardContent>
				</Card>

				<div className="grid content-start gap-3">
					{providersQuery.isLoading && (
						<div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
							Loading provider connections...
						</div>
					)}
					{!providersQuery.isLoading && providers.length === 0 && (
						<div className="rounded-xl border border-dashed p-6">
							<div className="flex items-start gap-3">
								<CircleAlert className="mt-0.5 h-4 w-4 text-muted-foreground" />
								<div>
									<p className="text-sm font-medium">
										No providers connected
									</p>
									<p className="mt-1 text-sm text-muted-foreground">
										Add one provider connection before
										creating model profiles.
									</p>
								</div>
							</div>
						</div>
					)}
					{providers.map((provider) => (
						<Card
							key={provider.id}
							className="rounded-xl"
							size="sm"
						>
							<CardHeader>
								<CardTitle className="flex flex-wrap items-center gap-2">
									{provider.name}
									<Badge variant="secondary">
										{providerLabel(provider.provider)}
									</Badge>
								</CardTitle>
								<CardDescription>
									{provider.endpoint ||
										"Default provider endpoint"}
								</CardDescription>
								<CardAction className="flex items-center gap-1">
									<Button
										type="button"
										variant="ghost"
										size="icon"
										aria-label={`Edit ${provider.name}`}
										onClick={() =>
											setProviderEdit({
												id: provider.id,
												name: provider.name,
												provider: provider.provider,
												endpoint:
													provider.endpoint ?? "",
												apiKey: "",
											})
										}
									>
										<Pencil className="h-4 w-4" />
									</Button>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										aria-label={`Test ${provider.name}`}
										onClick={() =>
											testProviderMutation.mutate(
												provider.id,
											)
										}
									>
										<RefreshCw className="h-4 w-4" />
									</Button>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										aria-label={`Delete ${provider.name}`}
										onClick={() =>
											deleteProviderMutation.mutate(
												provider.id,
											)
										}
									>
										<Trash2 className="h-4 w-4" />
									</Button>
								</CardAction>
							</CardHeader>
							<CardContent>
								<div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
									<span>
										{provider.profile_count} profiles
									</span>
									<span>
										Key{" "}
										{provider.api_key_set
											? "saved"
											: "missing"}
									</span>
								</div>
							</CardContent>
						</Card>
					))}
				</div>
			</section>

			<section className="grid gap-4 xl:grid-cols-[minmax(320px,420px)_1fr]">
				<Card className="rounded-xl">
					<CardHeader>
						<CardTitle className="flex items-center gap-2">
							<Bot className="h-4 w-4 text-primary" />
							Model Profiles
						</CardTitle>
						<CardDescription>
							Name reusable provider and model combinations for
							later assignment.
						</CardDescription>
					</CardHeader>
					<CardContent className="space-y-4">
						<div className="space-y-2">
							<Label htmlFor="ai-profile-name">
								Profile Name
							</Label>
							<Input
								id="ai-profile-name"
								value={profileName}
								onChange={(event) =>
									setProfileName(event.target.value)
								}
								placeholder="Fast Support Chat"
							/>
						</div>
						<div className="space-y-2">
							<Label htmlFor="ai-profile-provider">
								Provider Connection
							</Label>
							<Select
								value={profileConnectionId}
								onValueChange={setProfileConnectionId}
							>
								<SelectTrigger
									id="ai-profile-provider"
									className="w-full rounded-lg"
								>
									<SelectValue placeholder="Select provider" />
								</SelectTrigger>
								<SelectContent>
									{providers.map((provider) => (
										<SelectItem
											key={provider.id}
											value={provider.id}
										>
											{provider.name} ·{" "}
											{providerLabel(provider.provider)}
										</SelectItem>
									))}
								</SelectContent>
							</Select>
						</div>
						<div className="space-y-2">
							<Label htmlFor="ai-profile-model">Model</Label>
							<Input
								id="ai-profile-model"
								value={profileModel}
								onChange={(event) =>
									setProfileModel(event.target.value)
								}
								placeholder="gpt-5-mini"
							/>
						</div>
						<div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
							<div className="space-y-2">
								<Label htmlFor="ai-profile-max-tokens">
									Max Tokens
								</Label>
								<Input
									id="ai-profile-max-tokens"
									type="number"
									min="1"
									value={profileMaxTokens}
									onChange={(event) =>
										setProfileMaxTokens(event.target.value)
									}
								/>
							</div>
							<label className="flex min-h-9 items-center gap-2 rounded-lg border px-3 py-2 text-sm">
								<Switch
									size="sm"
									checked={profileChatEnabled}
									onCheckedChange={setProfileChatEnabled}
								/>
								<MessageSquareText className="h-4 w-4 text-primary" />
								Chat
							</label>
						</div>
						<Button
							type="button"
							className="w-full gap-2"
							disabled={
								!profileReady || createProfileMutation.isPending
							}
							onClick={() =>
								createProfileMutation.mutate({
									name: profileName.trim(),
									connection_id: profileConnectionId,
									model: profileModel.trim(),
									max_tokens: Number(profileMaxTokens),
									capabilities: null,
									enabled_for_chat: profileChatEnabled,
								})
							}
						>
							<Plus className="h-4 w-4" />
							Create Profile
						</Button>
					</CardContent>
				</Card>

				<div className="grid content-start gap-3">
					{profilesQuery.isLoading && (
						<div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
							Loading model profiles...
						</div>
					)}
					{!profilesQuery.isLoading && profiles.length === 0 && (
						<div className="rounded-xl border border-dashed p-6">
							<p className="text-sm font-medium">
								No model profiles yet
							</p>
							<p className="mt-1 text-sm text-muted-foreground">
								Create profiles for fast, balanced, pro, or any
								other reusable model role you need.
							</p>
						</div>
					)}
					{profiles.map((profile) => (
						<Card key={profile.id} className="rounded-xl" size="sm">
							<CardHeader>
								<CardTitle className="flex flex-wrap items-center gap-2">
									{profile.name}
									{profile.enabled_for_chat && (
										<Badge variant="secondary">
											<MessageSquareText className="h-3 w-3" />
											Chat
										</Badge>
									)}
									{chatDefaultId === profile.id && (
										<Badge>
											<CheckCircle2 className="h-3 w-3" />
											Default
										</Badge>
									)}
								</CardTitle>
								<CardDescription>
									{profileLine(profile)}
								</CardDescription>
								<CardAction className="flex items-center gap-1">
									<Button
										type="button"
										variant="ghost"
										size="icon"
										aria-label={`Edit ${profile.name}`}
										onClick={() =>
											setProfileEdit({
												id: profile.id,
												name: profile.name,
												connectionId:
													profile.connection_id,
												model: profile.model,
												maxTokens: String(
													profile.max_tokens,
												),
											})
										}
									>
										<Pencil className="h-4 w-4" />
									</Button>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										aria-label={`Delete ${profile.name}`}
										onClick={() =>
											deleteProfileMutation.mutate(
												profile.id,
											)
										}
									>
										<Trash2 className="h-4 w-4" />
									</Button>
								</CardAction>
							</CardHeader>
							<CardContent className="flex flex-wrap items-center justify-between gap-3">
								<div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
									<span>
										{profile.max_tokens.toLocaleString()}{" "}
										max tokens
									</span>
									{(profile.assignment_keys ?? []).map(
										(key) => (
											<Badge key={key} variant="outline">
												{key.replace("_", " ")}
											</Badge>
										),
									)}
								</div>
								<div className="flex items-center gap-2 text-sm">
									<MessageSquareText className="h-4 w-4 text-primary" />
									<span>Enabled for Chat</span>
									<Switch
										size="sm"
										checked={profile.enabled_for_chat}
										onCheckedChange={(enabledForChat) =>
											updateProfileMutation.mutate({
												profileId: profile.id,
												enabledForChat,
											})
										}
									/>
									<Button
										type="button"
										variant="outline"
										size="sm"
										disabled={!profile.enabled_for_chat}
										onClick={() =>
											assignMutation.mutate({
												assignmentKey: "chat_default",
												profileId: profile.id,
											})
										}
									>
										Set Default
									</Button>
								</div>
							</CardContent>
						</Card>
					))}
				</div>
			</section>

			<section className="space-y-3">
				<div>
					<h3 className="text-base font-semibold">Assignments</h3>
					<p className="mt-1 text-sm text-muted-foreground">
						Features point at reusable profiles, so model swaps
						happen in one place.
					</p>
				</div>
				<div className="grid gap-3 lg:grid-cols-2">
					{ASSIGNMENTS.map((assignment) => {
						const Icon = assignment.icon;
						const current = assignmentsByKey.get(assignment.key);
						return (
							<Card
								key={assignment.key}
								className="rounded-xl"
								size="sm"
							>
								<CardHeader>
									<CardTitle className="flex items-center gap-2">
										<Icon className="h-4 w-4 text-primary" />
										{assignment.label}
									</CardTitle>
									<CardDescription>
										{assignment.description}
									</CardDescription>
								</CardHeader>
								<CardContent>
									<ModelProfileSelector
										id={`assignment-${assignment.key}`}
										label={`${assignment.label} Profile`}
										value={current?.profile_id ?? null}
										onValueChange={(profileId) =>
											assignMutation.mutate({
												assignmentKey: assignment.key,
												profileId,
											})
										}
										chatOnly={
											assignment.key === "chat_default"
										}
									/>
								</CardContent>
							</Card>
						);
					})}
				</div>
			</section>

			<Dialog
				open={providerEdit !== null}
				onOpenChange={(open) => !open && setProviderEdit(null)}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Edit Provider Connection</DialogTitle>
						<DialogDescription>
							Update the connection once for every profile that
							uses it. Leave the key blank to keep the saved key.
						</DialogDescription>
					</DialogHeader>
					{providerEdit && (
						<div className="grid gap-4 py-2">
							<div className="space-y-2">
								<Label htmlFor="edit-provider-name">Name</Label>
								<Input
									id="edit-provider-name"
									value={providerEdit.name}
									onChange={(event) =>
										setProviderEdit({
											...providerEdit,
											name: event.target.value,
										})
									}
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="edit-provider-kind">
									Provider
								</Label>
								<Select
									value={providerEdit.provider}
									onValueChange={(provider) =>
										setProviderEdit({
											...providerEdit,
											provider:
												provider as AIProviderKind,
										})
									}
								>
									<SelectTrigger id="edit-provider-kind">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{PROVIDERS.map((provider) => (
											<SelectItem
												key={provider.value}
												value={provider.value}
											>
												{provider.label}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							</div>
							<div className="space-y-2">
								<Label htmlFor="edit-provider-endpoint">
									Endpoint
								</Label>
								<Input
									id="edit-provider-endpoint"
									value={providerEdit.endpoint}
									onChange={(event) =>
										setProviderEdit({
											...providerEdit,
											endpoint: event.target.value,
										})
									}
									placeholder="Optional base URL"
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="edit-provider-key">
									New API Key
								</Label>
								<Input
									id="edit-provider-key"
									type="password"
									value={providerEdit.apiKey}
									onChange={(event) =>
										setProviderEdit({
											...providerEdit,
											apiKey: event.target.value,
										})
									}
									placeholder="Leave blank to keep the saved key"
								/>
							</div>
						</div>
					)}
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => setProviderEdit(null)}
						>
							Cancel
						</Button>
						<Button
							disabled={
								!providerEdit?.name.trim() ||
								updateProviderMutation.isPending
							}
							onClick={() =>
								providerEdit &&
								updateProviderMutation.mutate(providerEdit)
							}
						>
							Save Provider
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog
				open={profileEdit !== null}
				onOpenChange={(open) => !open && setProfileEdit(null)}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Edit Model Profile</DialogTitle>
						<DialogDescription>
							Changes apply everywhere this reusable profile is
							assigned.
						</DialogDescription>
					</DialogHeader>
					{profileEdit && (
						<div className="grid gap-4 py-2">
							<div className="space-y-2">
								<Label htmlFor="edit-profile-name">Name</Label>
								<Input
									id="edit-profile-name"
									value={profileEdit.name}
									onChange={(event) =>
										setProfileEdit({
											...profileEdit,
											name: event.target.value,
										})
									}
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="edit-profile-provider">
									Provider Connection
								</Label>
								<Select
									value={profileEdit.connectionId}
									onValueChange={(connectionId) =>
										setProfileEdit({
											...profileEdit,
											connectionId,
										})
									}
								>
									<SelectTrigger id="edit-profile-provider">
										<SelectValue />
									</SelectTrigger>
									<SelectContent>
										{providers.map((provider) => (
											<SelectItem
												key={provider.id}
												value={provider.id}
											>
												{provider.name} ·{" "}
												{providerLabel(
													provider.provider,
												)}
											</SelectItem>
										))}
									</SelectContent>
								</Select>
							</div>
							<div className="space-y-2">
								<Label htmlFor="edit-profile-model">
									Model
								</Label>
								<Input
									id="edit-profile-model"
									value={profileEdit.model}
									onChange={(event) =>
										setProfileEdit({
											...profileEdit,
											model: event.target.value,
										})
									}
								/>
							</div>
							<div className="space-y-2">
								<Label htmlFor="edit-profile-tokens">
									Max Tokens
								</Label>
								<Input
									id="edit-profile-tokens"
									type="number"
									min="1"
									value={profileEdit.maxTokens}
									onChange={(event) =>
										setProfileEdit({
											...profileEdit,
											maxTokens: event.target.value,
										})
									}
								/>
							</div>
						</div>
					)}
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => setProfileEdit(null)}
						>
							Cancel
						</Button>
						<Button
							disabled={
								!profileEdit?.name.trim() ||
								!profileEdit?.model.trim() ||
								!profileEdit?.connectionId ||
								Number(profileEdit?.maxTokens) < 1 ||
								editProfileMutation.isPending
							}
							onClick={() =>
								profileEdit &&
								editProfileMutation.mutate(profileEdit)
							}
						>
							Save Profile
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
