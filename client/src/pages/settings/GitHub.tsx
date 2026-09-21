import { GitHubTokenField } from "./GitHubTokenField";
import { GitHubResourceSelect } from "./GitHubResourceSelect";
import { GitHubCreateRepositoryDialog } from "./GitHubCreateRepositoryDialog";
import { GitHubDisconnectDialog } from "./GitHubDisconnectDialog";
import { SettingsReadError } from "./SettingsReadError";
import { GitHubConnectionSummary } from "./GitHubConnectionSummary";
import { useRef, useState } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { toast } from "sonner";
import { Loader2, Plus } from "lucide-react";
import { Github } from "@/components/icons/GithubIcon";
import {
	useGitHubConfig,
	useGitHubRepositories,
	useCreateGitHubRepository,
	useDisconnectGitHub,
	enqueueGitHubConnect,
	previewGitHubConnect,
	validateGitHubToken,
	listGitHubBranches,
	type GitConnectPreview,
	type GitConnectRequest,
	type GitHubRepoInfo,
	type GitHubBranchInfo,
	type GitHubConfigResponse,
} from "@/hooks/useGitHub";

export function GitHub() {
	const headingRef = useRef<HTMLDivElement>(null);
	const branchRequest = useRef(0);
	const [branchError, setBranchError] = useState(false);
	const [disconnected, setDisconnected] = useState(false);
	const [config, setConfig] = useState<GitHubConfigResponse | null>(null);
	const [saving, setSaving] = useState(false);
	const [testingToken, setTestingToken] = useState(false);
	const [loadingBranches, setLoadingBranches] = useState(false);
	const [connectionPreview, setConnectionPreview] =
		useState<GitConnectPreview | null>(null);
	const [connectionStrategy, setConnectionStrategy] = useState<
		GitConnectRequest["strategy"] | ""
	>("");
	const [conflictDecisions, setConflictDecisions] = useState<
		Record<string, "local" | "remote">
	>({});
	const [confirmDestructive, setConfirmDestructive] = useState(false);
	const [connectionJobId, setConnectionJobId] = useState<string | null>(null);

	// Form state
	const [token, setToken] = useState("");
	const [tokenValid, setTokenValid] = useState<boolean | null>(null);
	const [repositories, setRepositories] = useState<GitHubRepoInfo[]>([]);
	const [branches, setBranches] = useState<GitHubBranchInfo[]>([]);
	const [selectedRepo, setSelectedRepo] = useState<string>("");
	const [selectedBranch, setSelectedBranch] = useState<string>("main");

	// Create repo state
	const [showCreateRepo, setShowCreateRepo] = useState(false);
	const [newRepoName, setNewRepoName] = useState("");
	const [newRepoDescription, setNewRepoDescription] = useState("");
	const [newRepoPrivate, setNewRepoPrivate] = useState(true);
	const [creatingRepo, setCreatingRepo] = useState(false);

	// Disconnect confirmation state
	const [showDisconnectConfirm, setShowDisconnectConfirm] = useState(false);

	// Load current GitHub configuration
	const {
		data: configData,
		isLoading: configLoading,
		isError: configError,
		isFetching: configFetching,
		refetch: refetchConfig,
	} = useGitHubConfig();

	// Load repositories when token is saved but not configured
	const shouldLoadRepos = configData?.token_saved && !configData?.configured;
	const {
		data: reposData,
		isError: reposError,
		isFetching: reposFetching,
		refetch: refetchRepos,
	} = useGitHubRepositories(shouldLoadRepos ?? false);

	// Mutations
	const createRepoMutation = useCreateGitHubRepository();
	const disconnectMutation = useDisconnectGitHub();
	// Track the saved token for use in configuration
	const [savedToken, setSavedToken] = useState<string | null>(null);
	const previewItems = connectionPreview?.items ?? [];
	const conflicts = previewItems.filter(
		(item) => item.classification === "conflict",
	);
	const summaryCounts = {
		local: previewItems.filter(
			(item) => item.classification === "local_only",
		).length,
		remote: previewItems.filter(
			(item) => item.classification === "remote_only",
		).length,
		same: previewItems.filter((item) => item.classification === "identical")
			.length,
		conflict: conflicts.length,
	};

	// Mirror server-loaded configData into local state so handlers can patch
	// it without going back through the query cache. Adjust during render
	// with a previous-value sentinel to avoid setState-in-effect.
	const [prevConfigDataRef, setPrevConfigDataRef] = useState<
		GitHubConfigResponse | undefined
	>(undefined);
	if (configData && prevConfigDataRef !== configData) {
		setPrevConfigDataRef(configData);
		setConfig(configData);
		if (configData.token_saved && !configData.configured) {
			setTokenValid(true);
		}
	}

	// Load repositories when they become available for an unconfigured token.
	const [prevReposDataRef, setPrevReposDataRef] = useState<
		typeof reposData | undefined
	>(undefined);
	if (
		reposData?.repositories &&
		config?.token_saved &&
		!config?.configured &&
		prevReposDataRef !== reposData
	) {
		setPrevReposDataRef(reposData);
		setRepositories(reposData.repositories);
	}

	// Validate token and load repositories
	const handleTokenValidation = async () => {
		if (testingToken || saving) return;
		if (!token.trim()) {
			toast.error("Please enter a GitHub Personal Access Token");
			return;
		}

		setTestingToken(true);
		setTokenValid(null);
		setRepositories([]);
		setBranches([]);

		try {
			const response = await validateGitHubToken(token);
			setRepositories(response.repositories);
			setSavedToken(token); // Save token for later use in configure
			setTokenValid(true);

			// Auto-select detected repo if available
			if (response.detected_repo) {
				await handleRepoSelection(
					response.detected_repo.full_name,
					response.detected_repo.branch,
				);

				toast.success("Token validated successfully", {
					description: `Detected existing repository: ${response.detected_repo.full_name}`,
				});
			} else {
				toast.success("Token validated successfully", {
					description: `Found ${response.repositories.length} accessible repositories`,
				});
			}
		} catch {
			setTokenValid(false);
			toast.error("Invalid token", {
				description:
					"Please check your GitHub Personal Access Token and try again",
			});
		} finally {
			setTestingToken(false);
		}
	};

	// Load branches when repository is selected
	const handleRepoSelection = async (
		repoFullName: string,
		preferredBranch?: string,
	) => {
		const request = ++branchRequest.current;
		setBranchError(false);
		setSelectedRepo(repoFullName);
		setBranches([]);
		setSelectedBranch(preferredBranch || "main");
		setConnectionPreview(null);
		setConnectionJobId(null);

		if (!repoFullName) return;

		setLoadingBranches(true);
		try {
			const branchList = await listGitHubBranches(repoFullName);
			if (request !== branchRequest.current) return;
			setBranches(branchList);

			// Auto-select main/master if available
			const defaultBranch =
				branchList.find((b) => b.name === preferredBranch) ||
				branchList.find((b) => b.name === "main") ||
				branchList.find((b) => b.name === "master");
			if (defaultBranch) {
				setSelectedBranch(defaultBranch.name);
			}
		} catch {
			if (request !== branchRequest.current) return;
			setBranchError(true);
		} finally {
			if (request === branchRequest.current) setLoadingBranches(false);
		}
	};

	// Create new repository
	const handleCreateRepository = async () => {
		if (creatingRepo) return;
		if (!newRepoName.trim()) {
			toast.error("Please enter a repository name");
			return;
		}

		setCreatingRepo(true);
		try {
			const newRepo = await createRepoMutation.mutateAsync({
				body: {
					name: newRepoName,
					description: newRepoDescription || null,
					private: newRepoPrivate,
					organization: null,
				},
			});

			toast.success("Repository created", {
				description: `Created ${newRepo.full_name}`,
			});

			// Update repositories list - refetch will happen automatically from mutation
			// For now, we manually update the state for immediate feedback
			setRepositories([
				...repositories,
				newRepo as unknown as GitHubRepoInfo,
			]);
			setSelectedRepo(newRepo.full_name);

			// Load branches for new repo
			await handleRepoSelection(newRepo.full_name);

			// Close dialog and reset form
			setShowCreateRepo(false);
			setNewRepoName("");
			setNewRepoDescription("");
			setNewRepoPrivate(true);
		} catch {
			// The dialog retains the draft and displays the mutation failure inline.
		} finally {
			setCreatingRepo(false);
		}
	};

	// Compare before any first connection can modify the workspace.
	const handleConfigure = async () => {
		if (saving || testingToken || loadingBranches || branchError) return;
		// Token must be saved to configure
		if (!config?.token_saved && !savedToken) {
			toast.error("Please validate your token first");
			return;
		}

		if (!selectedRepo) {
			toast.error("Please select a repository");
			return;
		}

		setSaving(true);
		setConnectionJobId(null);
		try {
			const preview = await previewGitHubConnect({
				repository_url: selectedRepo,
				branch: selectedBranch,
			});
			setConnectionPreview(preview);
			setConnectionStrategy("");
			setConflictDecisions({});
			setConfirmDestructive(false);
			toast.success("Workspace connection reviewed", {
				description:
					"Choose how to reconcile the reviewed files before connecting.",
			});
		} catch (error) {
			toast.error("Failed to review workspace connection", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	const handleConnect = async () => {
		if (!connectionPreview || !connectionStrategy || saving) return;
		if (
			connectionStrategy === "reconcile" &&
			conflicts.some((item) => !conflictDecisions[item.path])
		) {
			toast.error("Choose local or remote for every conflict", {
				description:
					"Reconcile requires one decision for each conflicting path.",
			});
			return;
		}
		const discardsLocal = previewItems.some(
			(item) =>
				item.classification === "local_only" ||
				item.classification === "conflict",
		);
		if (
			connectionStrategy === "start_from_remote" &&
			discardsLocal &&
			!confirmDestructive
		) {
			toast.error("Confirm replacing local workspace content", {
				description:
					"Starting from the remote would discard reviewed local files.",
			});
			return;
		}
		setSaving(true);
		try {
			const operation = await enqueueGitHubConnect({
				preview_token: connectionPreview.token,
				strategy: connectionStrategy,
				decisions:
					connectionStrategy === "reconcile" ? conflictDecisions : {},
				confirm_destructive:
					connectionStrategy === "start_from_remote" &&
					confirmDestructive,
			});
			setConnectionJobId(operation.job_id);
			toast.success("GitHub connection queued", {
				description: operation.notification_id
					? "Progress will appear in notifications."
					: `Track durable job ${operation.job_id}.`,
			});
		} catch (error) {
			toast.error("Failed to queue GitHub connection", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	// Disconnect GitHub integration
	const handleDisconnect = async () => {
		if (saving) return;
		setSaving(true);

		try {
			await disconnectMutation.mutateAsync({});
			setDisconnected(true);
			setShowDisconnectConfirm(false);
			setSavedToken(null);

			// Reset all state
			setConfig({
				configured: false,
				token_saved: false,
				repo_url: null,
				branch: null,
				backup_path: null,
			});
			setToken("");
			setTokenValid(null);
			setRepositories([]);
			setBranches([]);
			setSelectedRepo("");
			setSelectedBranch("main");

			toast.success("GitHub integration disconnected", {
				description: "Your credentials have been removed",
			});
		} catch (error) {
			toast.error("Failed to disconnect GitHub", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	if (configLoading) {
		return (
			<div
				role="status"
				aria-label="Loading GitHub configuration"
				className="flex items-center justify-center py-12"
			>
				<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
			</div>
		);
	}

	const readError = configError ? (
		<SettingsReadError
			resource="GitHub configuration"
			cached={!!configData}
			pending={configFetching}
			onRetry={() => {
				void refetchConfig();
			}}
		/>
	) : null;
	if (!configData) return readError;

	return (
		<div className="space-y-6">
			{readError}
			<Card>
				<CardHeader>
					<div className="flex items-center gap-2">
						<Github className="h-5 w-5" />
						<CardTitle
							ref={headingRef}
							tabIndex={-1}
							className="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						>
							GitHub Integration
						</CardTitle>
					</div>
					<CardDescription>
						Connect your workspace to a GitHub repository for
						version control and collaboration
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-6">
					{/* Current Status */}
					{config?.configured ? (
						<GitHubConnectionSummary
							config={config}
							pending={saving}
							onDisconnect={() => {
								disconnectMutation.reset();
								setDisconnected(false);
								setShowDisconnectConfirm(true);
							}}
						/>
					) : (
						<>
							<GitHubTokenField
								value={token}
								saved={!!config?.token_saved}
								valid={tokenValid}
								pending={testingToken}
								disabled={saving}
								onChange={(value) => {
									setToken(value);
									setTokenValid(null);
								}}
								onValidate={() => {
									void handleTokenValidation();
								}}
							/>

							{/* Repository Selection - always show if token is valid or saved */}
							{(tokenValid || config?.token_saved) && (
								<div className="space-y-2">
									<div className="flex items-center justify-between gap-3">
										<Label htmlFor="repository">
											Repository
										</Label>
										<Button
											variant="ghost"
											size="sm"
											className="min-h-11"
											disabled={saving}
											onClick={() => {
												createRepoMutation.reset();
												setShowCreateRepo(true);
											}}
										>
											<Plus className="h-4 w-4 mr-1" />
											Create New
										</Button>
									</div>
									<GitHubResourceSelect
										id="repository"
										value={selectedRepo}
										onChange={(value) => {
											void handleRepoSelection(value);
										}}
										placeholder={
											reposFetching &&
											repositories.length === 0
												? "Loading repositories…"
												: "Select a repository"
										}
										disabled={
											saving ||
											(repositories.length === 0 &&
												(reposFetching || reposError))
										}
										options={repositories.map((repo) => ({
											value: repo.full_name,
											detail: repo.private
												? "Private"
												: undefined,
										}))}
									/>
									{reposError && (
										<SettingsReadError
											resource="GitHub repositories"
											cached={repositories.length > 0}
											pending={reposFetching}
											onRetry={() => {
												void refetchRepos();
											}}
										/>
									)}
									{!reposError &&
										!reposFetching &&
										repositories.length === 0 && (
											<p className="text-sm text-muted-foreground">
												No repositories available.
												Create one or check the saved
												token’s repository access.
											</p>
										)}
								</div>
							)}

							{/* Branch Selection - always show if repo selected */}
							{(tokenValid || config?.token_saved) && (
								<div className="space-y-2">
									<Label htmlFor="branch">Branch</Label>
									<GitHubResourceSelect
										id="branch"
										value={selectedBranch}
										onChange={(branch) => {
											setSelectedBranch(branch);
											setConnectionPreview(null);
											setConnectionJobId(null);
										}}
										placeholder="Select a branch"
										loading={loadingBranches}
										disabled={
											!selectedRepo ||
											branchError ||
											saving
										}
										options={branches.map((branch) => ({
											value: branch.name,
											detail: branch.protected
												? "Protected"
												: undefined,
										}))}
									/>
									{branchError && (
										<SettingsReadError
											resource="repository branches"
											cached={false}
											pending={loadingBranches}
											onRetry={() => {
												void handleRepoSelection(
													selectedRepo,
													selectedBranch,
												);
											}}
										/>
									)}
									{!selectedRepo && (
										<p className="text-xs text-muted-foreground">
											Select a repository first
										</p>
									)}
								</div>
							)}

							{connectionJobId && (
								<p
									role="status"
									className="text-sm text-muted-foreground"
								>
									Connection queued. Follow its progress in
									notifications.
								</p>
							)}

							{connectionPreview && (
								<section
									aria-label="Review workspace connection"
									className="space-y-4 rounded-[var(--bf-radius-control)] border border-border/70 bg-muted/20 p-4"
								>
									<div className="space-y-1">
										<h3 className="font-medium">
											Review workspace connection
										</h3>
										<p className="text-sm text-muted-foreground">
											The comparison is bound to this
											repository and branch. If either
											changes, review it again.
										</p>
									</div>
									<div
										aria-label="Connection summary"
										className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4"
									>
										<span>
											<strong>
												{summaryCounts.local}
											</strong>{" "}
											local-only
										</span>
										<span>
											<strong>
												{summaryCounts.remote}
											</strong>{" "}
											remote-only
										</span>
										<span>
											<strong>
												{summaryCounts.same}
											</strong>{" "}
											same
										</span>
										<span>
											<strong>
												{summaryCounts.conflict}
											</strong>{" "}
											conflict
											{summaryCounts.conflict === 1
												? ""
												: "s"}
										</span>
									</div>

									<RadioGroup
										value={connectionStrategy}
										onValueChange={(value) =>
											setConnectionStrategy(
												value as GitConnectRequest["strategy"],
											)
										}
										aria-label="Connection strategy"
										className="space-y-2"
									>
										<div className="flex gap-3 rounded-[var(--bf-radius-control)] border border-border/70 p-3">
											<RadioGroupItem
												value="publish_local"
												id="publish-local"
											/>
											<Label
												htmlFor="publish-local"
												className="space-y-1"
											>
												<span>
													Publish local workspace
												</span>
												<span className="block text-xs font-normal text-muted-foreground">
													Creates the remote branch
													from local files. This is
													unavailable when the remote
													branch already has content.
												</span>
											</Label>
										</div>
										<div className="flex gap-3 rounded-[var(--bf-radius-control)] border border-border/70 p-3">
											<RadioGroupItem
												value="start_from_remote"
												id="start-from-remote"
											/>
											<Label
												htmlFor="start-from-remote"
												className="space-y-1"
											>
												<span>Start from remote</span>
												<span className="block text-xs font-normal text-muted-foreground">
													Replaces reviewed local-only
													files with the remote
													branch.
												</span>
											</Label>
										</div>
										<div className="flex gap-3 rounded-[var(--bf-radius-control)] border border-border/70 p-3">
											<RadioGroupItem
												value="reconcile"
												id="reconcile"
											/>
											<Label
												htmlFor="reconcile"
												className="space-y-1"
											>
												<span>Reconcile both</span>
												<span className="block text-xs font-normal text-muted-foreground">
													Keeps both sides where
													possible and asks you to
													choose every conflict.
												</span>
											</Label>
										</div>
									</RadioGroup>

									{connectionStrategy ===
										"start_from_remote" &&
										previewItems.some(
											(item) =>
												item.classification ===
													"local_only" ||
												item.classification ===
													"conflict",
										) && (
											<div className="flex items-start gap-3 rounded-[var(--bf-radius-control)] border border-destructive/40 bg-destructive/5 p-3">
												<Checkbox
													id="confirm-destructive-connect"
													checked={confirmDestructive}
													onCheckedChange={(
														checked,
													) =>
														setConfirmDestructive(
															checked === true,
														)
													}
												/>
												<Label
													htmlFor="confirm-destructive-connect"
													className="font-normal"
												>
													I understand this replaces
													the reviewed local-only and
													conflicting files with the
													remote branch.
												</Label>
											</div>
										)}

									{connectionStrategy === "reconcile" &&
										conflicts.length > 0 && (
											<div className="space-y-2">
												<Label>Resolve conflicts</Label>
												<div className="max-h-56 space-y-2 overflow-y-auto pr-1">
													{conflicts.map((item) => (
														<div
															key={item.path}
															className="rounded-[var(--bf-radius-control)] border border-border/70 p-3"
														>
															<p className="mb-2 break-all font-mono text-xs">
																{item.path}
															</p>
															<RadioGroup
																value={
																	conflictDecisions[
																		item
																			.path
																	] ?? ""
																}
																onValueChange={(
																	value,
																) =>
																	setConflictDecisions(
																		(
																			current,
																		) => ({
																			...current,
																			[item.path]:
																				value as
																					| "local"
																					| "remote",
																		}),
																	)
																}
																aria-label={`Decision for ${item.path}`}
																className="grid grid-cols-1 gap-2 sm:grid-cols-2"
															>
																<div className="flex items-center gap-2">
																	<RadioGroupItem
																		value="local"
																		id={`local-${item.path}`}
																	/>
																	<Label
																		htmlFor={`local-${item.path}`}
																		className="font-normal"
																	>
																		Keep
																		local:{" "}
																		{
																			item.path
																		}
																	</Label>
																</div>
																<div className="flex items-center gap-2">
																	<RadioGroupItem
																		value="remote"
																		id={`remote-${item.path}`}
																	/>
																	<Label
																		htmlFor={`remote-${item.path}`}
																		className="font-normal"
																	>
																		Keep
																		remote:{" "}
																		{
																			item.path
																		}
																	</Label>
																</div>
															</RadioGroup>
														</div>
													))}
												</div>
											</div>
										)}

									<Button
										onClick={() => {
											void handleConnect();
										}}
										className="min-h-11 w-full sm:w-auto"
										disabled={
											saving ||
											!connectionStrategy ||
											(connectionStrategy ===
												"reconcile" &&
												conflicts.some(
													(item) =>
														!conflictDecisions[
															item.path
														],
												)) ||
											(connectionStrategy ===
												"start_from_remote" &&
												previewItems.some(
													(item) =>
														item.classification ===
															"local_only" ||
														item.classification ===
															"conflict",
												) &&
												!confirmDestructive)
										}
									>
										{saving ? (
											<>
												<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
												Connecting...
											</>
										) : (
											<>
												{" "}
												<Github className="mr-2 h-4 w-4" />
												Connect GitHub
											</>
										)}
									</Button>
								</section>
							)}

							{/* Review Button */}
							<div className="flex justify-end">
								<Button
									onClick={handleConfigure}
									className="min-h-11 w-full sm:w-auto"
									disabled={
										saving ||
										testingToken ||
										loadingBranches ||
										branchError ||
										!selectedRepo ||
										(!config?.token_saved &&
											!token.trim()) ||
										(!config?.token_saved &&
											tokenValid !== true)
									}
								>
									{saving ? (
										<>
											<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
											Reviewing...
										</>
									) : (
										<>
											<Github className="h-4 w-4 mr-2" />
											Review connection
										</>
									)}
								</Button>
							</div>
						</>
					)}
				</CardContent>
			</Card>

			{/* Additional Information */}
			<Card>
				<CardHeader>
					<CardTitle className="text-base">How it works</CardTitle>
				</CardHeader>
				<CardContent className="space-y-2 text-sm text-muted-foreground">
					<p>
						Once configured, your workspace will be synced with the
						selected GitHub repository:
					</p>
					<ul className="list-disc list-inside space-y-1 ml-2">
						<li>
							Use the <strong>Source Control</strong> panel in the
							Code Editor to view changes
						</li>
						<li>
							Commit and push changes directly from the editor
						</li>
						<li>
							Pull updates from GitHub to keep your workspace in
							sync
						</li>
						<li>Resolve merge conflicts with inline tools</li>
					</ul>
				</CardContent>
			</Card>

			<GitHubCreateRepositoryDialog
				open={showCreateRepo}
				name={newRepoName}
				description={newRepoDescription}
				isPrivate={newRepoPrivate}
				pending={creatingRepo}
				failed={createRepoMutation.isError}
				onClose={() => setShowCreateRepo(false)}
				onConfirm={() => {
					void handleCreateRepository();
				}}
				onNameChange={setNewRepoName}
				onDescriptionChange={setNewRepoDescription}
				onPrivateChange={setNewRepoPrivate}
			/>

			<GitHubDisconnectDialog
				open={showDisconnectConfirm}
				pending={saving}
				failed={disconnectMutation.isError}
				completed={disconnected}
				returnFocusRef={headingRef}
				onClose={() => setShowDisconnectConfirm(false)}
				onConfirm={() => {
					void handleDisconnect();
				}}
			/>
		</div>
	);
}
