import { ProfileCreateDialog } from "@/components/ai/ProfileCreateDialog";
import { ProviderCreateDialog } from "@/components/ai/ProviderCreateDialog";
import { providerOption, providerLabel } from "@/components/ai/providerOptions";
import { ProviderEditDialog, type ProviderEditDraft } from "@/components/ai/ProviderEditDialog";
import { ModelProfileEditDialog, type ModelProfileEditDraft } from "@/components/ai/ModelProfileEditDialog";
import { ModelProfileMergeDialog } from "@/components/ai/ModelProfileMergeDialog";
import { ModelAssignmentCard } from "@/components/ai/ModelAssignmentCard";
import { ModelProfileDeleteDialog } from "@/components/ai/ModelProfileDeleteDialog";
import { ModelProfileCard } from "@/components/ai/ModelProfileCard";
import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
	Bot,
	GitMerge,
	KeyRound,
	MessageSquareText,
	Plus,
	Settings2,
	ShieldCheck,
	Sparkles,
	Star,
	type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";

import { ProviderDeleteDialog } from "@/components/ai/ProviderDeleteDialog";
import { ModelSettingsReadError } from "@/components/ai/ModelSettingsReadError";
import { ProviderConnectionCard } from "@/components/ai/ProviderConnectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
	type AIProviderConnection,
} from "@/services/aiModels";

const PROVIDER_QUERY_KEY = ["ai", "provider-connections"] as const;
const PROFILE_QUERY_KEY = ["ai", "model-profiles"] as const;
const ASSIGNMENT_QUERY_KEY = ["ai", "model-assignments"] as const;

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
	const [profileFailoverId, setProfileFailoverId] = useState<string | null>(null);
	const [profileSelectionMode, setProfileSelectionMode] = useState(false);
	const [selectedProfileIds, setSelectedProfileIds] = useState<Set<string>>(
		() => new Set(),
	);
	const [profileMergeOpen, setProfileMergeOpen] = useState(false);
	const [mergeTargetProfileId, setMergeTargetProfileId] = useState("");
	const [providerEdit, setProviderEdit] = useState<ProviderEditDraft | null>(null);
	const [profileEdit, setProfileEdit] = useState<ModelProfileEditDraft | null>(null);

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
	const profiles = useMemo(() => profilesQuery.data ?? [], [profilesQuery.data]);
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
		setProfileFailoverId(null);
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
		createProfileMutation.reset();
		createProviderMutation.reset();
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
		mergeProfilesMutation.reset();
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
				default_max_tokens: edit.defaultMaxTokens,
				failover_profile_id: edit.failoverProfileId,
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

	const providerHeadingRef = useRef<HTMLHeadingElement>(null);
	const profileHeadingRef = useRef<HTMLHeadingElement>(null);
	const [deletingProvider, setDeletingProvider] = useState<AIProviderConnection | null>(null);
	const deleteProviderMutation = useMutation({
		mutationFn: deleteProviderConnection,
		onSuccess: () => { setDeletingProvider(null); void invalidateAI(); },
	});

	const [deletingProfile, setDeletingProfile] = useState<AIModelProfile | null>(null);
	const deleteProfileMutation = useMutation({
		mutationFn: deleteModelProfile,
		onSuccess: () => { setDeletingProfile(null); void invalidateAI(); },
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
	});

	const providerReady =
		Boolean(providerName.trim() && providerKey.trim() && providerEndpoint.trim());
	const profileReady =
		Boolean(profileName.trim() && profileConnectionId && profileModel.trim());

	return (
		<div className="space-y-8">
			<ModelProfileDeleteDialog returnFocusRef={profileHeadingRef} completed={deleteProfileMutation.isSuccess} profile={deletingProfile} pending={deleteProfileMutation.isPending} failed={deleteProfileMutation.isError} onClose={() => setDeletingProfile(null)} onConfirm={() => { if (deletingProfile && !deleteProfileMutation.isPending) deleteProfileMutation.mutate(deletingProfile.id); }} />
			<ProviderDeleteDialog returnFocusRef={providerHeadingRef} completed={deleteProviderMutation.isSuccess} provider={deletingProvider} pending={deleteProviderMutation.isPending} failed={deleteProviderMutation.isError} onClose={() => setDeletingProvider(null)} onConfirm={() => { if (deletingProvider && !deleteProviderMutation.isPending) deleteProviderMutation.mutate(deletingProvider.id); }} />
			<section className="space-y-3">
				<div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
					<div>
						<h2 className="font-display text-2xl font-semibold">Models</h2>
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
				<div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
					<div>
						<h3 ref={providerHeadingRef} tabIndex={-1} className="flex items-center gap-2 text-base font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring">
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
						className="min-h-11 w-full sm:w-auto"
						onClick={() => { createProviderMutation.reset(); setProviderCreateOpen(true); }}
					>
						<Plus className="h-4 w-4" />
						Add Provider
					</Button>
				</div>

				<div className="grid gap-3 md:grid-cols-2">
					{providersQuery.isError && <ModelSettingsReadError resource="provider connections" cached={Boolean(providersQuery.data)} pending={providersQuery.isFetching} onRetry={() => void providersQuery.refetch()} />}
					{providersQuery.isLoading && (
						<div role="status" className="col-span-full rounded-[var(--bf-radius-surface)] border border-dashed p-8 text-center text-sm text-muted-foreground">
							Loading provider connections...
						</div>
					)}
					{!providersQuery.isLoading && !providersQuery.isError && providers.length === 0 && (
						<button
							type="button"
							className="col-span-full flex min-h-44 flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center transition-colors hover:border-primary/40 hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
							onClick={() => { createProviderMutation.reset(); setProviderCreateOpen(true); }}
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
					{providers.map((provider) => <ProviderConnectionCard key={provider.id} provider={provider} providerLabel={providerLabel(provider.provider)} testing={testProviderMutation.isPending && testProviderMutation.variables === provider.id} onEdit={() => { updateProviderMutation.reset(); setProviderEdit({ id: provider.id, name: provider.name, provider: provider.provider, endpoint: provider.endpoint ?? "", apiKey: "" }); }} onTest={() => testProviderMutation.mutate(provider.id)} onDelete={() => { deleteProviderMutation.reset(); setDeletingProvider(provider); }} />)}
				</div>
			</section>

			<section className="space-y-3">
				<div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
					<div>
						<h3 ref={profileHeadingRef} tabIndex={-1} className="flex items-center gap-2 text-base font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring">
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
					{profilesQuery.isError && <ModelSettingsReadError resource="model profiles" cached={Boolean(profilesQuery.data)} pending={profilesQuery.isFetching} onRetry={() => void profilesQuery.refetch()} />}
					{profilesQuery.isLoading && (
						<div role="status" className="rounded-[var(--bf-radius-surface)] border border-dashed p-6 text-sm text-muted-foreground">
							Loading model profiles...
						</div>
					)}
					{!profilesQuery.isLoading && !profilesQuery.isError && profiles.length === 0 && (
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
					{profiles.map((profile) => <ModelProfileCard key={profile.id}
						profile={profile} description={profileLine(profile)} assignments={(profile.assignment_keys ?? []).filter((key) => key !== "primary").map(assignmentLabel)}
						selectionMode={profileSelectionMode} selected={selectedProfileIds.has(profile.id)} isDefault={defaultProfileId === profile.id}
						chatPending={updateProfileMutation.isPending && updateProfileMutation.variables?.profileId === profile.id}
						defaultPending={assignMutation.isPending && assignMutation.variables?.assignmentKey === "primary" && assignMutation.variables.profileId === profile.id}
						defaultDisabled={assignmentsQuery.isLoading || assignmentsQuery.isError || assignMutation.isPending}
						onSelect={(selected) => toggleProfileSelection(profile.id, selected)}
						onEdit={() => { editProfileMutation.reset(); setProfileEdit({ id: profile.id, name: profile.name, connectionId: profile.connection_id, model: profile.model, defaultMaxTokens: profile.default_max_tokens ?? null, failoverProfileId: profile.failover_profile_id ?? null }); }}
						onDelete={() => { deleteProfileMutation.reset(); setDeletingProfile(profile); }}
						onChatChange={(enabledForChat) => updateProfileMutation.mutate({ profileId: profile.id, enabledForChat })}
						onSetDefault={() => assignMutation.mutate({ assignmentKey: "primary", profileId: profile.id })}
					/>)}
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
				{assignmentsQuery.isError && <ModelSettingsReadError resource="model assignments" cached={Boolean(assignmentsQuery.data)} pending={assignmentsQuery.isFetching} onRetry={() => void assignmentsQuery.refetch()} />}
				{assignmentsQuery.isLoading && <p role="status" className="text-sm text-muted-foreground">Loading model assignments…</p>}
				<div className="grid gap-3 lg:grid-cols-2">
					{ASSIGNMENTS.map((assignment) => <ModelAssignmentCard key={assignment.key}
						assignmentKey={assignment.key} label={assignment.label} description={assignment.description} icon={assignment.icon}
						profileId={assignmentsByKey.get(assignment.key)?.profile_id ?? null}
						disabled={assignmentsQuery.isLoading || assignmentsQuery.isError || profilesQuery.isLoading || profilesQuery.isError}
						saving={assignMutation.isPending && assignMutation.variables?.assignmentKey === assignment.key}
						onChange={(profileId) => assignMutation.mutate({ assignmentKey: assignment.key, profileId })}
					/>)}
				</div>
			</section>

			<ProviderCreateDialog providerCreateOpen={providerCreateOpen} providerName={providerName} providerKind={providerKind} providerEndpoint={providerEndpoint} providerKey={providerKey} providerReady={providerReady} setProviderName={setProviderName} changeProviderKind={changeProviderKind} setProviderEndpoint={setProviderEndpoint} setProviderKey={setProviderKey} pending={createProviderMutation.isPending} error={createProviderMutation.error} onClose={() => { setProviderCreateOpen(false); resetProviderCreate(); }} onSubmit={() =>
								createProviderMutation.mutate({
									name: providerName.trim(),
									provider: providerKind,
									api_key: providerKey,
									endpoint: providerEndpoint.trim(),
								})
							} />

			<ProfileCreateDialog profileCreateOpen={profileCreateOpen} profileName={profileName} profileConnectionId={profileConnectionId} profileModel={profileModel} profileChatEnabled={profileChatEnabled} profileFailoverId={profileFailoverId} profileReady={profileReady} providers={providers} profiles={profiles} firstProfile={profiles.length === 0} setProfileName={setProfileName} setProfileConnectionId={setProfileConnectionId} setProfileModel={setProfileModel} setProfileChatEnabled={setProfileChatEnabled} setProfileFailoverId={setProfileFailoverId} pending={createProfileMutation.isPending} error={createProfileMutation.error} onClose={() => { setProfileCreateOpen(false); resetProfileCreate(); }} onSubmit={() =>
								createProfileMutation.mutate({
									name: profileName.trim(),
									connection_id: profileConnectionId,
									model: profileModel.trim(),
									capabilities: null,
									enabled_for_chat: profileChatEnabled,
									failover_profile_id: profileFailoverId,
								})
							} />

			<ModelProfileMergeDialog returnFocusRef={profileHeadingRef} completed={mergeProfilesMutation.isSuccess} profileMergeOpen={profileMergeOpen} selectedProfiles={selectedProfiles} mergeTargetProfileId={mergeTargetProfileId} setMergeTargetProfileId={setMergeTargetProfileId} pending={mergeProfilesMutation.isPending} failed={mergeProfilesMutation.isError}
				onClose={() => { setProfileMergeOpen(false); setMergeTargetProfileId(""); }}
				onConfirm={() => { if (!mergeProfilesMutation.isPending) mergeProfilesMutation.mutate({ profile_ids: selectedProfiles.map((profile) => profile.id), target_profile_id: mergeTargetProfileId }); }}
			/>

			<ProviderEditDialog providerEdit={providerEdit} pending={updateProviderMutation.isPending} failed={updateProviderMutation.isError} onChange={setProviderEdit} onClose={() => setProviderEdit(null)} onSave={(draft) => updateProviderMutation.mutate(draft)} />

			<ModelProfileEditDialog profileEdit={profileEdit} profiles={profiles} providers={providers} providerLabel={providerLabel} pending={editProfileMutation.isPending} failed={editProfileMutation.isError} onChange={setProfileEdit} onClose={() => setProfileEdit(null)} onSave={(draft) => editProfileMutation.mutate(draft)} />
		</div>
	);
}
