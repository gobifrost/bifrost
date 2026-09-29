import { useDialogReturnFocus } from "@/hooks/useDialogReturnFocus";
import { ModelSettingsReadError } from "./ModelSettingsReadError";
import { useEffect, useMemo, useRef, useState } from "react";
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
import { warnIfProfileFails } from "./warnIfProfileFails";
import { useModelDisplayName } from "@/services/modelNames";

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
	/** Parent renders a shared profile-query error for a group of selectors. */
	profileErrorShownByParent?: boolean;
}

function profileDescription(
	profile: AIModelProfile,
	modelName: (model: string) => string,
): string {
	return `${profile.connection.name} · ${modelName(profile.model)}`;
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
	profileErrorShownByParent = false,
}: ModelProfileSelectorProps) {
	const modelName = useModelDisplayName();
	const [creating, setCreating] = useState(false);
	const [newName, setNewName] = useState("");
	const [newConnectionId, setNewConnectionId] = useState("");
	const [newModel, setNewModel] = useState("");
	const queryClient = useQueryClient();
	const createSubmissionLockRef = useRef(false);
	const createErrorRef = useRef<HTMLParagraphElement | null>(null);

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
	const savedProfile = profilesQuery.data?.find(
		(profile) => profile.id === value,
	);
	const profileOptions = useMemo(() => {
		const options = profiles.map((profile) => ({
			value: profile.id,
			label: profile.name,
			description: profileDescription(profile, modelName),
		}));

		if (
			savedProfile &&
			!options.some((option) => option.value === savedProfile.id)
		) {
			options.unshift({
				value: savedProfile.id,
				label: savedProfile.name,
				description: profileDescription(savedProfile, modelName),
			});
		}

		return options;
	}, [profiles, savedProfile, modelName]);
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
			void warnIfProfileFails(profile.id, profile.name);
		},
		onError: (error) => {
			toast.error("Could not create profile", {
				description:
					error instanceof Error
						? error.message
						: "Check the provider connection and model name.",
			});
		},
		onSettled: () => {
			createSubmissionLockRef.current = false;
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
	const comboboxPlaceholder =
		value && !savedProfile ? "Assigned profile unavailable" : placeholder;

	const submitCreate = () => {
		if (
			!formReady ||
			createMutation.isPending ||
			createSubmissionLockRef.current ||
			connectionsQuery.isError ||
			connectionsQuery.isLoading
		) {
			return;
		}
		createSubmissionLockRef.current = true;
		createMutation.mutate({
			name: inferredName,
			connection_id: newConnectionId,
			model: newModel.trim(),
			capabilities: null,
			enabled_for_chat: chatOnly,
		});
	};

	const dialogFocus = useDialogReturnFocus();

	useEffect(() => {
		if (createMutation.isError) {
			createErrorRef.current?.scrollIntoView({ block: "nearest" });
			createErrorRef.current?.focus({ preventScroll: true });
		}
	}, [createMutation.isError]);

	return (
		<div className="min-w-0 space-y-2">
			<div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
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
						className="min-h-11 gap-1.5 px-2 text-sm"
						onClick={() => {
							createMutation.reset();
							createSubmissionLockRef.current = false;
							setCreating(true);
						}}
						disabled={disabled || connectionsQuery.isLoading}
					>
						<Plus className="h-3.5 w-3.5" />
						Create profile
					</Button>
				)}
			</div>
			{profilesQuery.isError && !profileErrorShownByParent && (
				<ModelSettingsReadError
					resource="model profiles"
					cached={Boolean(profilesQuery.data)}
					pending={profilesQuery.isFetching}
					onRetry={() => void profilesQuery.refetch()}
				/>
			)}
			<Combobox
				id={id}
				value={savedProfile ? (value ?? "") : ""}
				onValueChange={(nextValue) => {
					if (nextValue) onValueChange(nextValue);
				}}
				options={profileOptions}
				placeholder={comboboxPlaceholder}
				searchPlaceholder="Search profiles..."
				emptyText={
					chatOnly
						? "No chat-enabled profiles found."
						: "No profiles found."
				}
				isLoading={profilesQuery.isLoading}
				disabled={
					disabled ||
					isSaving ||
					(profilesQuery.isError && !profilesQuery.data)
				}
			/>

			<Dialog
				open={creating}
				onOpenChange={(open) => {
					if (!createMutation.isPending) setCreating(open);
				}}
			>
				<DialogContent
					{...dialogFocus}
					className="sm:max-w-[560px]"
					onEscapeKeyDown={(event) => {
						if (createMutation.isPending) {
							event.preventDefault();
						}
					}}
					onPointerDownOutside={(event) => {
						if (createMutation.isPending) {
							event.preventDefault();
						}
					}}
				>
					<DialogHeader>
						<div className="mb-2 flex h-9 w-9 items-center justify-center rounded-[var(--bf-radius-control)] bg-primary/10 text-primary">
							<Sparkles className="h-4 w-4" />
						</div>
						<DialogTitle>Create Model Profile</DialogTitle>
						<DialogDescription>
							Profiles are reusable model choices. Assign this
							profile wherever Bifrost needs a model.
						</DialogDescription>
					</DialogHeader>

					<form
						className="grid min-w-0 gap-4"
						onSubmit={(event) => {
							event.preventDefault();
							event.stopPropagation();
							submitCreate();
						}}
					>
						{connectionsQuery.isError && (
							<ModelSettingsReadError
								resource="provider connections"
								cached={Boolean(connectionsQuery.data)}
								pending={connectionsQuery.isFetching}
								onRetry={() => void connectionsQuery.refetch()}
							/>
						)}
						{connectionsQuery.isLoading ? (
							<p
								role="status"
								className="text-sm text-muted-foreground"
							>
								Loading provider connections…
							</p>
						) : canCreate ? (
							<fieldset
								disabled={
									createMutation.isPending ||
									connectionsQuery.isError
								}
								className="grid min-w-0 gap-4 py-2"
							>
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
										disabled={
											createMutation.isPending ||
											connectionsQuery.isError
										}
										onValueChange={setNewConnectionId}
									>
										<SelectTrigger
											id="model-profile-connection"
											className="h-auto min-h-11 w-full data-[size=default]:h-auto [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
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
									<div className="flex items-start gap-2 rounded-[var(--bf-radius-surface)] bg-muted/60 p-3 text-sm">
										<MessageSquareText className="mt-0.5 h-4 w-4 text-primary" />
										<p className="text-muted-foreground">
											New profiles created here are
											enabled for chat.
										</p>
									</div>
								)}
							</fieldset>
						) : !connectionsQuery.isError ? (
							<div className="flex items-start gap-3 rounded-[var(--bf-radius-surface)] border bg-muted/40 p-4">
								<Bot className="mt-0.5 h-4 w-4 text-muted-foreground" />
								<div>
									<p className="text-sm font-medium">
										Create a provider connection first
									</p>
									<p className="mt-1 text-sm text-muted-foreground">
										Profiles need a saved provider
										connection before they can be reused.
									</p>
								</div>
							</div>
						) : null}

						{createMutation.isError && (
							<p
								ref={createErrorRef}
								role="alert"
								tabIndex={-1}
								className="text-sm text-destructive outline-none [overflow-wrap:anywhere]"
							>
								Could not create this profile. Your entries are
								preserved. Check the provider and model, then
								try again.
							</p>
						)}
						<DialogFooter>
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								disabled={createMutation.isPending}
								onClick={() => setCreating(false)}
							>
								Cancel
							</Button>
							<Button
								type="submit"
								className="min-h-11"
								disabled={
									!formReady ||
									createMutation.isPending ||
									connectionsQuery.isError ||
									connectionsQuery.isLoading
								}
							>
								{createMutation.isPending ? (
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
								) : (
									<Check className="h-4 w-4" />
								)}
								{createMutation.isPending
									? "Creating…"
									: "Create"}
							</Button>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>
		</div>
	);
}
