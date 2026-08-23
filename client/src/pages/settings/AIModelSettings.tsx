import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Bot,
	CheckCircle2,
	GitMerge,
	KeyRound,
	Loader2,
	MessageSquareText,
	Pencil,
	Plus,
	RefreshCw,
	Settings2,
	ShieldCheck,
	Sparkles,
	Star,
	Trash2,
	type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";

import { ModelProfileSelector } from "@/components/ai/ModelProfileSelector";
import { ProviderModelField } from "@/components/ai/ProviderModelField";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
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
	mergeModelProfiles,
	setModelAssignment,
	testProviderConnection,
	updateProviderConnection,
	updateModelProfile,
	verifyProviderConnection,
	type AIModelAssignmentKey,
	type AIModelAssignment,
	type AIModelProfile,
	type AIProviderKind,
} from "@/services/aiModels";

const PROVIDER_QUERY_KEY = ["ai", "provider-connections"] as const;
const PROFILE_QUERY_KEY = ["ai", "model-profiles"] as const;
const ASSIGNMENT_QUERY_KEY = ["ai", "model-assignments"] as const;

const PROVIDERS: {
	value: AIProviderKind;
	label: string;
	endpoint: string;
}[] = [
	{
		value: "openai",
		label: "OpenAI",
		endpoint: "https://api.openai.com/v1",
	},
	{
		value: "openrouter",
		label: "OpenRouter",
		endpoint: "https://openrouter.ai/api/v1",
	},
	{
		value: "google",
		label: "Google",
		endpoint: "https://generativelanguage.googleapis.com",
	},
	{
		value: "anthropic",
		label: "Anthropic",
		endpoint: "https://api.anthropic.com",
	},
	{
		value: "openai_compatible",
		label: "OpenAI-Compatible",
		endpoint: "",
	},
];

function providerOption(provider: AIProviderKind) {
	return (
		PROVIDERS.find((option) => option.value === provider) ?? PROVIDERS[0]
	);
}

const ASSIGNMENTS: {
	key: AIModelAssignmentKey;
	label: string;
	description: string;
	icon: LucideIcon;
}[] = [
	{
		key: "primary",
		label: "Default",
		description: "Used for general AI work and SDK completions.",
		icon: Star,
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
		label: "Chat",
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

function assignmentLabel(key: AIModelAssignmentKey): string {
	return (
		ASSIGNMENTS.find((assignment) => assignment.key === key)?.label ?? key
	);
}

function replaceAssignment(
	assignments: AIModelAssignment[],
	nextAssignment: AIModelAssignment,
): AIModelAssignment[] {
	const remaining = assignments.filter(
		(assignment) =>
			assignment.assignment_key !== nextAssignment.assignment_key,
	);
	return [...remaining, nextAssignment].sort((left, right) =>
		left.assignment_key.localeCompare(right.assignment_key),
	);
}

function reflectAssignmentOnProfiles(
	profiles: AIModelProfile[],
	assignmentKey: AIModelAssignmentKey,
	profileId: string,
): AIModelProfile[] {
	return profiles.map((profile) => ({
		...profile,
		assignment_keys: [
			...(profile.assignment_keys ?? []).filter(
				(key) => key !== assignmentKey,
			),
			...(profile.id === profileId ? [assignmentKey] : []),
		],
	}));
}

class ProviderVerificationError extends Error {}

export function AIModelSettings() {
	const queryClient = useQueryClient();
	const [providerCreateOpen, setProviderCreateOpen] = useState(false);
	const [profileCreateOpen, setProfileCreateOpen] = useState(false);
	const [providerName, setProviderName] = useState("OpenAI");
	const [providerKind, setProviderKind] = useState<AIProviderKind>("openai");
	const [providerEndpoint, setProviderEndpoint] = useState(
		providerOption("openai").endpoint,
	);
	const [providerKey, setProviderKey] = useState("");
	const [profileName, setProfileName] = useState("");
	const [profileConnectionId, setProfileConnectionId] = useState("");
	const [profileModel, setProfileModel] = useState("");
	const [profileChatEnabled, setProfileChatEnabled] = useState(false);
	const [profileSelectionMode, setProfileSelectionMode] = useState(false);
	const [selectedProfileIds, setSelectedProfileIds] = useState<Set<string>>(
		() => new Set(),
	);
	const [profileMergeOpen, setProfileMergeOpen] = useState(false);
	const [mergeTargetProfileId, setMergeTargetProfileId] = useState("");
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
	const defaultProfileId = assignmentsByKey.get("primary")?.profile_id;
	const selectedProfiles = useMemo(
		() => profiles.filter((profile) => selectedProfileIds.has(profile.id)),
		[profiles, selectedProfileIds],
	);
	const mergeTargetProfile = selectedProfiles.find(
		(profile) => profile.id === mergeTargetProfileId,
	);
	const mergeSourceProfiles = selectedProfiles.filter(
		(profile) => profile.id !== mergeTargetProfileId,
	);
	const mergeAgentCount = mergeSourceProfiles.reduce(
		(total, profile) => total + profile.referenced_agent_count,
		0,
	);
	const mergeAssignmentCount = new Set(
		mergeSourceProfiles.flatMap((profile) => profile.assignment_keys ?? []),
	).size;
	const mergeEnablesChat =
		mergeTargetProfile !== undefined &&
		!mergeTargetProfile.enabled_for_chat &&
		selectedProfiles.some((profile) => profile.enabled_for_chat);

	const resetProviderCreate = () => {
		setProviderName("OpenAI");
		setProviderKind("openai");
		setProviderEndpoint(providerOption("openai").endpoint);
		setProviderKey("");
	};

	const resetProfileCreate = () => {
		setProfileName("");
		setProfileConnectionId("");
		setProfileModel("");
		setProfileChatEnabled(false);
	};

	const changeProviderKind = (nextKind: AIProviderKind) => {
		const previous = providerOption(providerKind);
		const next = providerOption(nextKind);
		setProviderKind(nextKind);
		if (!providerName.trim() || providerName === previous.label) {
			setProviderName(next.label);
		}
		if (
			!providerEndpoint.trim() ||
			providerEndpoint === previous.endpoint
		) {
			setProviderEndpoint(next.endpoint);
		}
	};

	const openProfileCreate = () => {
		if (providers.length === 0) {
			setProviderCreateOpen(true);
			return;
		}
		setProfileConnectionId((current) => current || providers[0].id);
		if (profiles.length === 0) setProfileChatEnabled(true);
		setProfileCreateOpen(true);
	};

	const cancelProfileSelection = () => {
		setProfileSelectionMode(false);
		setSelectedProfileIds(new Set());
		setMergeTargetProfileId("");
	};

	const toggleProfileSelection = (profileId: string, selected: boolean) => {
		setSelectedProfileIds((current) => {
			const next = new Set(current);
			if (selected) next.add(profileId);
			else next.delete(profileId);
			return next;
		});
	};

	const openProfileMerge = () => {
		if (selectedProfiles.length < 2) return;
		const selectedDefault = selectedProfiles.find(
			(profile) => profile.id === defaultProfileId,
		);
		setMergeTargetProfileId(selectedDefault?.id ?? selectedProfiles[0].id);
		setProfileMergeOpen(true);
	};

	const invalidateAI = () => {
		void queryClient.invalidateQueries({ queryKey: PROVIDER_QUERY_KEY });
		void queryClient.invalidateQueries({ queryKey: PROFILE_QUERY_KEY });
		void queryClient.invalidateQueries({ queryKey: ASSIGNMENT_QUERY_KEY });
	};

	const createProviderMutation = useMutation({
		mutationFn: async (
			connection: Parameters<typeof createProviderConnection>[0],
		) => {
			const result = await verifyProviderConnection(connection);
			if (!result.success)
				throw new ProviderVerificationError(result.message);
			return createProviderConnection(connection);
		},
		onSuccess: (connection) => {
			setProviderCreateOpen(false);
			resetProviderCreate();
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
			toast.error(
				error instanceof ProviderVerificationError
					? "Provider verification failed"
					: "Could not save provider",
				{
					description:
						error instanceof Error
							? error.message
							: "Check the fields and try again.",
				},
			),
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
			const isFirstProfile = profiles.length === 0;
			setProfileCreateOpen(false);
			resetProfileCreate();
			queryClient.setQueryData(
				PROFILE_QUERY_KEY,
				(existing: typeof profiles | undefined) => [
					...(existing ?? []),
					profile,
				],
			);
			invalidateAI();
			toast.success(
				isFirstProfile
					? "First profile created and assigned everywhere"
					: "Model profile created",
			);
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
		onSuccess: () => {
			invalidateAI();
			toast.success("Chat availability updated");
		},
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
		onMutate: async ({ assignmentKey, profileId }) => {
			await Promise.all([
				queryClient.cancelQueries({ queryKey: ASSIGNMENT_QUERY_KEY }),
				queryClient.cancelQueries({ queryKey: PROFILE_QUERY_KEY }),
			]);
			const previousAssignments =
				queryClient.getQueryData<AIModelAssignment[]>(
					ASSIGNMENT_QUERY_KEY,
				) ?? [];
			const previousProfiles =
				queryClient.getQueryData<AIModelProfile[]>(PROFILE_QUERY_KEY) ??
				[];
			const profile = previousProfiles.find(
				(candidate) => candidate.id === profileId,
			);
			if (profile) {
				const previous = previousAssignments.find(
					(assignment) => assignment.assignment_key === assignmentKey,
				);
				const now = new Date().toISOString();
				queryClient.setQueryData<AIModelAssignment[]>(
					ASSIGNMENT_QUERY_KEY,
					replaceAssignment(previousAssignments, {
						assignment_key: assignmentKey,
						profile_id: profileId,
						profile,
						created_at: previous?.created_at ?? now,
						updated_at: now,
					}),
				);
				queryClient.setQueryData<AIModelProfile[]>(
					PROFILE_QUERY_KEY,
					reflectAssignmentOnProfiles(
						previousProfiles,
						assignmentKey,
						profileId,
					),
				);
			}
			return { previousAssignments, previousProfiles };
		},
		onSuccess: (assignment) => {
			queryClient.setQueryData<AIModelAssignment[]>(
				ASSIGNMENT_QUERY_KEY,
				(existing = []) => replaceAssignment(existing, assignment),
			);
			queryClient.setQueryData<AIModelProfile[]>(
				PROFILE_QUERY_KEY,
				(existing = []) =>
					reflectAssignmentOnProfiles(
						existing,
						assignment.assignment_key,
						assignment.profile_id,
					),
			);
			toast.success(
				assignment.assignment_key === "primary"
					? "Default model profile updated"
					: `${assignmentLabel(assignment.assignment_key)} assignment updated`,
			);
		},
		onError: (error, _variables, context) => {
			if (context) {
				queryClient.setQueryData(
					ASSIGNMENT_QUERY_KEY,
					context.previousAssignments,
				);
				queryClient.setQueryData(
					PROFILE_QUERY_KEY,
					context.previousProfiles,
				);
			}
			toast.error("Could not update assignment", {
				description:
					error instanceof Error
						? error.message
						: "Try another profile.",
			});
		},
		onSettled: invalidateAI,
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

	const mergeProfilesMutation = useMutation({
		mutationFn: (request: Parameters<typeof mergeModelProfiles>[0]) =>
			mergeModelProfiles(request),
		onSuccess: (result) => {
			setProfileMergeOpen(false);
			cancelProfileSelection();
			invalidateAI();
			const movedItems =
				result.reassigned_agent_count +
				result.reassigned_assignment_keys.length;
			toast.success(
				`${result.merged_profile_ids.length} ${
					result.merged_profile_ids.length === 1
						? "profile"
						: "profiles"
				} merged into ${result.profile.name}`,
				{
					description:
						movedItems > 0
							? `${movedItems} ${movedItems === 1 ? "reference was" : "references were"} reassigned.`
							: "No assignments or agents needed reassignment.",
				},
			);
		},
		onError: (error) =>
			toast.error("Could not merge profiles", {
				description:
					error instanceof Error
						? error.message
						: "The profiles were not changed. Try again.",
			}),
	});

	const providerReady =
		providerName.trim() && providerKey.trim() && providerEndpoint.trim();
	const profileReady =
		profileName.trim() && profileConnectionId && profileModel.trim();

	return (
		<div className="space-y-8">
			<section className="space-y-3">
				<div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
					<div>
						<h2 className="text-xl font-semibold">Models</h2>
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

			<section className="space-y-3">
				<div className="flex items-start justify-between gap-4">
					<div>
						<h3 className="flex items-center gap-2 text-base font-semibold">
							<KeyRound className="h-4 w-4 text-primary" />
							Provider Connections
						</h3>
						<p className="mt-1 text-sm text-muted-foreground">
							Save credentials once, then reuse the connection
							across profiles.
						</p>
					</div>
					<Button
						type="button"
						size="sm"
						onClick={() => setProviderCreateOpen(true)}
					>
						<Plus className="h-4 w-4" />
						Add Provider
					</Button>
				</div>

				<div className="grid gap-3 md:grid-cols-2">
					{providersQuery.isLoading && (
						<div className="col-span-full rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">
							Loading provider connections...
						</div>
					)}
					{!providersQuery.isLoading && providers.length === 0 && (
						<button
							type="button"
							className="col-span-full flex min-h-44 flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center transition-colors hover:border-primary/40 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
							onClick={() => setProviderCreateOpen(true)}
						>
							<KeyRound className="h-9 w-9 text-muted-foreground" />
							<span className="mt-3 text-sm font-semibold">
								No providers
							</span>
							<span className="mt-1 text-sm text-muted-foreground">
								Click here to configure your first provider
								connection.
							</span>
						</button>
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
										disabled={
											testProviderMutation.isPending &&
											testProviderMutation.variables ===
												provider.id
										}
										onClick={() =>
											testProviderMutation.mutate(
												provider.id,
											)
										}
									>
										<RefreshCw
											className={`h-4 w-4 ${testProviderMutation.isPending && testProviderMutation.variables === provider.id ? "animate-spin motion-reduce:animate-none" : ""}`}
										/>
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
										{provider.profile_count}{" "}
										{provider.profile_count === 1
											? "profile"
											: "profiles"}
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

			<section className="space-y-3">
				<div className="flex items-start justify-between gap-4">
					<div>
						<h3 className="flex items-center gap-2 text-base font-semibold">
							<Bot className="h-4 w-4 text-primary" />
							Model Profiles
						</h3>
						<p className="mt-1 text-sm text-muted-foreground">
							Name reusable provider and model combinations for
							later assignment.
						</p>
					</div>
					<div className="flex flex-wrap items-center justify-end gap-2">
						{profileSelectionMode ? (
							<>
								<span
									className="text-sm text-muted-foreground"
									aria-live="polite"
								>
									{selectedProfiles.length} selected
								</span>
								<Button
									type="button"
									variant="outline"
									size="sm"
									onClick={cancelProfileSelection}
								>
									Cancel
								</Button>
								<Button
									type="button"
									size="sm"
									disabled={selectedProfiles.length < 2}
									onClick={openProfileMerge}
								>
									<GitMerge className="h-4 w-4" />
									Merge Profiles
								</Button>
							</>
						) : (
							<>
								{profiles.length >= 2 && (
									<Button
										type="button"
										variant="outline"
										size="sm"
										onClick={() =>
											setProfileSelectionMode(true)
										}
									>
										<GitMerge className="h-4 w-4" />
										Merge Profiles
									</Button>
								)}
								<Button
									type="button"
									size="sm"
									onClick={openProfileCreate}
								>
									<Plus className="h-4 w-4" />
									Add Profile
								</Button>
							</>
						)}
					</div>
				</div>

				<div className="grid content-start gap-3">
					{profilesQuery.isLoading && (
						<div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
							Loading model profiles...
						</div>
					)}
					{!profilesQuery.isLoading && profiles.length === 0 && (
						<button
							type="button"
							className="flex min-h-44 flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center transition-colors hover:border-primary/40 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
							onClick={openProfileCreate}
						>
							<Bot className="h-9 w-9 text-muted-foreground" />
							<span className="mt-3 text-sm font-semibold">
								No model profiles
							</span>
							<span className="mt-1 text-sm text-muted-foreground">
								{providers.length > 0
									? "Click here to configure your first model profile."
									: "Configure a provider connection before creating your first model profile."}
							</span>
						</button>
					)}
					{profiles.map((profile) => (
						<Card
							key={profile.id}
							className={`rounded-xl transition-[background-color,box-shadow] duration-200 motion-reduce:transition-none ${
								selectedProfileIds.has(profile.id)
									? "bg-primary/[0.03] ring-primary/25"
									: ""
							}`}
							size="sm"
						>
							<CardHeader>
								<CardTitle className="flex flex-wrap items-center gap-2">
									{profileSelectionMode && (
										<Checkbox
											checked={selectedProfileIds.has(
												profile.id,
											)}
											onCheckedChange={(checked) =>
												toggleProfileSelection(
													profile.id,
													checked === true,
												)
											}
											aria-label={`Select ${profile.name}`}
										/>
									)}
									{profile.name}
									{profile.enabled_for_chat && (
										<Badge variant="secondary">
											<MessageSquareText className="h-3 w-3" />
											Chat
										</Badge>
									)}
									{defaultProfileId === profile.id && (
										<Badge className="animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none">
											<CheckCircle2 className="h-3 w-3" />
											Default
										</Badge>
									)}
								</CardTitle>
								<CardDescription>
									{profileLine(profile)}
								</CardDescription>
								{!profileSelectionMode && (
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
								)}
							</CardHeader>
							<CardContent className="flex flex-wrap items-center justify-between gap-3">
								<div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
									{(profile.assignment_keys ?? [])
										.filter((key) => key !== "primary")
										.map((key) => (
											<Badge key={key} variant="outline">
												{assignmentLabel(key)}
											</Badge>
										))}
								</div>
								<div className="flex items-center gap-2 text-sm">
									<MessageSquareText className="h-4 w-4 text-primary" />
									<span>Enabled for Chat</span>
									<Switch
										size="sm"
										checked={profile.enabled_for_chat}
										disabled={
											profileSelectionMode ||
											(updateProfileMutation.isPending &&
												updateProfileMutation.variables
													?.profileId === profile.id)
										}
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
										disabled={
											profileSelectionMode ||
											defaultProfileId === profile.id ||
											assignMutation.isPending
										}
										onClick={() =>
											assignMutation.mutate({
												assignmentKey: "primary",
												profileId: profile.id,
											})
										}
									>
										{assignMutation.isPending &&
										assignMutation.variables
											?.assignmentKey === "primary" &&
										assignMutation.variables.profileId ===
											profile.id ? (
											<>
												<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
												Saving…
											</>
										) : defaultProfileId === profile.id ? (
											<>
												<CheckCircle2 className="h-4 w-4" />
												Default
											</>
										) : (
											"Set Default"
										)}
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
						const isSaving =
							assignMutation.isPending &&
							assignMutation.variables?.assignmentKey ===
								assignment.key;
						return (
							<Card
								key={assignment.key}
								className={`rounded-xl transition-[background-color,box-shadow] duration-200 motion-reduce:transition-none ${
									assignment.key === "primary"
										? "bg-amber-500/[0.04] ring-amber-500/25 dark:bg-amber-400/[0.05] dark:ring-amber-400/25"
										: ""
								} ${isSaving ? "bg-muted/40 shadow-sm" : ""}`}
								size="sm"
							>
								<CardHeader>
									<CardTitle className="flex items-center gap-2">
										<Icon
											className={`h-4 w-4 ${
												assignment.key === "primary"
													? "fill-current text-amber-600 dark:text-amber-400"
													: "text-primary"
											}`}
										/>
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
										isSaving={isSaving}
									/>
								</CardContent>
							</Card>
						);
					})}
				</div>
			</section>

			<Dialog
				open={providerCreateOpen}
				onOpenChange={(open) => {
					if (createProviderMutation.isPending) return;
					setProviderCreateOpen(open);
					if (!open) resetProviderCreate();
				}}
			>
				<DialogContent className="sm:max-w-[560px]">
					<DialogHeader>
						<DialogTitle>Add Provider Connection</DialogTitle>
						<DialogDescription>
							Save a provider once, then reuse it across model
							profiles.
						</DialogDescription>
					</DialogHeader>
					<div className="grid gap-4 py-2">
						<div className="grid gap-4 sm:grid-cols-2">
							<div className="space-y-2">
								<Label htmlFor="ai-provider-kind">
									Provider
								</Label>
								<Select
									value={providerKind}
									onValueChange={(value) =>
										changeProviderKind(
											value as AIProviderKind,
										)
									}
								>
									<SelectTrigger
										id="ai-provider-kind"
										className="w-full"
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
								<Label htmlFor="ai-provider-name">
									Connection Name
								</Label>
								<Input
									id="ai-provider-name"
									value={providerName}
									onChange={(event) =>
										setProviderName(event.target.value)
									}
									placeholder="OpenAI Production"
								/>
							</div>
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
								placeholder="https://api.example.com/v1"
							/>
							<p className="text-xs text-muted-foreground">
								{providerKind === "openai_compatible"
									? "Required for OpenAI-Compatible providers."
									: `Prefilled with the standard ${providerLabel(providerKind)} endpoint.`}
							</p>
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
					</div>
					<DialogFooter>
						<Button
							variant="outline"
							disabled={createProviderMutation.isPending}
							onClick={() => {
								setProviderCreateOpen(false);
								resetProviderCreate();
							}}
						>
							Cancel
						</Button>
						<Button
							disabled={
								!providerReady ||
								createProviderMutation.isPending
							}
							onClick={() =>
								createProviderMutation.mutate({
									name: providerName.trim(),
									provider: providerKind,
									api_key: providerKey,
									endpoint: providerEndpoint.trim(),
								})
							}
						>
							{createProviderMutation.isPending ? (
								<>
									<Loader2 className="h-4 w-4 animate-spin" />
									Verifying...
								</>
							) : (
								"Add Provider"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog
				open={profileCreateOpen}
				onOpenChange={(open) => {
					setProfileCreateOpen(open);
					if (!open) resetProfileCreate();
				}}
			>
				<DialogContent className="sm:max-w-[560px]">
					<DialogHeader>
						<DialogTitle>Add Model Profile</DialogTitle>
						<DialogDescription>
							Create a reusable provider and model combination.
						</DialogDescription>
					</DialogHeader>
					<div className="grid gap-4 py-2">
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
								onValueChange={(connectionId) => {
									setProfileConnectionId(connectionId);
									setProfileModel("");
								}}
							>
								<SelectTrigger
									id="ai-profile-provider"
									className="w-full"
								>
									<SelectValue placeholder="Select a provider connection" />
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
						<div>
							<ProviderModelField
								id="ai-profile-model"
								connectionId={profileConnectionId}
								value={profileModel}
								onValueChange={setProfileModel}
							/>
						</div>
						<label className="flex items-center justify-between gap-4 rounded-lg border px-3 py-2.5 text-sm">
							<span>
								<span className="flex items-center gap-2">
									<MessageSquareText className="h-4 w-4 text-primary" />
									Enable for Chat
								</span>
								{profiles.length === 0 && (
									<span className="mt-1 block text-xs text-muted-foreground">
										Your first profile starts as the default
										for every assignment.
									</span>
								)}
							</span>
							<Switch
								size="sm"
								checked={profileChatEnabled}
								disabled={profiles.length === 0}
								onCheckedChange={setProfileChatEnabled}
							/>
						</label>
					</div>
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => {
								setProfileCreateOpen(false);
								resetProfileCreate();
							}}
						>
							Cancel
						</Button>
						<Button
							disabled={
								!profileReady || createProfileMutation.isPending
							}
							onClick={() =>
								createProfileMutation.mutate({
									name: profileName.trim(),
									connection_id: profileConnectionId,
									model: profileModel.trim(),
									capabilities: null,
									enabled_for_chat: profileChatEnabled,
								})
							}
						>
							{createProfileMutation.isPending ? (
								<>
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									Creating…
								</>
							) : (
								"Add Profile"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog
				open={profileMergeOpen}
				onOpenChange={(open) => {
					if (mergeProfilesMutation.isPending) return;
					setProfileMergeOpen(open);
					if (!open) setMergeTargetProfileId("");
				}}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Merge Model Profiles</DialogTitle>
						<DialogDescription>
							Choose the profile to keep. Every assignment and
							agent using the others will switch to it.
						</DialogDescription>
					</DialogHeader>
					<div className="grid gap-4 py-2">
						<RadioGroup
							value={mergeTargetProfileId}
							onValueChange={setMergeTargetProfileId}
							aria-label="Profile to keep"
							className="gap-2"
						>
							{selectedProfiles.map((profile) => {
								const selected =
									profile.id === mergeTargetProfileId;
								return (
									<Label
										key={profile.id}
										htmlFor={`merge-target-${profile.id}`}
										className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 transition-[background-color,border-color,box-shadow] duration-150 motion-reduce:transition-none ${
											selected
												? "border-primary/50 bg-primary/[0.04] ring-2 ring-primary/15"
												: "hover:bg-muted/50"
										}`}
									>
										<RadioGroupItem
											id={`merge-target-${profile.id}`}
											value={profile.id}
										/>
										<span className="min-w-0 flex-1">
											<span className="block font-medium">
												{profile.name}
											</span>
											<span className="block truncate text-xs font-normal text-muted-foreground">
												{profileLine(profile)}
											</span>
										</span>
										{selected && (
											<Badge variant="secondary">
												Keep
											</Badge>
										)}
									</Label>
								);
							})}
						</RadioGroup>

						{mergeTargetProfile && (
							<div
								className="rounded-xl bg-muted/50 p-4 text-sm"
								aria-live="polite"
							>
								<p className="font-medium">
									Keep {mergeTargetProfile.name} and remove{" "}
									{mergeSourceProfiles.length} other{" "}
									{mergeSourceProfiles.length === 1
										? "profile"
										: "profiles"}
									.
								</p>
								<p className="mt-1 text-muted-foreground">
									{mergeAgentCount}{" "}
									{mergeAgentCount === 1 ? "agent" : "agents"}{" "}
									and {mergeAssignmentCount}{" "}
									{mergeAssignmentCount === 1
										? "assignment"
										: "assignments"}{" "}
									will move to it.
								</p>
								{mergeEnablesChat && (
									<p className="mt-1 text-muted-foreground">
										{mergeTargetProfile.name} will also be
										enabled for Chat.
									</p>
								)}
								<p className="mt-3 text-xs font-medium text-destructive">
									This can’t be undone.
								</p>
							</div>
						)}
					</div>
					<DialogFooter>
						<Button
							variant="outline"
							disabled={mergeProfilesMutation.isPending}
							onClick={() => setProfileMergeOpen(false)}
						>
							Cancel
						</Button>
						<Button
							variant="destructive"
							disabled={
								!mergeTargetProfileId ||
								selectedProfiles.length < 2 ||
								mergeProfilesMutation.isPending
							}
							onClick={() =>
								mergeProfilesMutation.mutate({
									profile_ids: selectedProfiles.map(
										(profile) => profile.id,
									),
									target_profile_id: mergeTargetProfileId,
								})
							}
						>
							{mergeProfilesMutation.isPending ? (
								<>
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									Merging…
								</>
							) : (
								<>
									<GitMerge className="h-4 w-4" />
									Merge Profiles
								</>
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

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
									onValueChange={(provider) => {
										const nextKind =
											provider as AIProviderKind;
										const previousDefault = providerOption(
											providerEdit.provider,
										).endpoint;
										setProviderEdit({
											...providerEdit,
											provider: nextKind,
											endpoint:
												!providerEdit.endpoint ||
												providerEdit.endpoint ===
													previousDefault
													? providerOption(nextKind)
															.endpoint
													: providerEdit.endpoint,
										});
									}}
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
									placeholder="https://api.example.com/v1"
								/>
								<p className="text-xs text-muted-foreground">
									{providerEdit.provider ===
									"openai_compatible"
										? "Required for OpenAI-Compatible providers."
										: `Standard ${providerLabel(providerEdit.provider)} endpoint.`}
								</p>
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
							{updateProviderMutation.isPending ? (
								<>
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									Saving…
								</>
							) : (
								"Save Provider"
							)}
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
											model: "",
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
							<ProviderModelField
								id="edit-profile-model"
								connectionId={profileEdit.connectionId}
								value={profileEdit.model}
								onValueChange={(model) =>
									setProfileEdit({
										...profileEdit,
										model,
									})
								}
							/>
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
								editProfileMutation.isPending
							}
							onClick={() =>
								profileEdit &&
								editProfileMutation.mutate(profileEdit)
							}
						>
							{editProfileMutation.isPending ? (
								<>
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
									Saving…
								</>
							) : (
								"Save Profile"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
