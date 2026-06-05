import { useState } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
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
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
	Loader2,
	CheckCircle2,
	AlertCircle,
	Plus,
} from "lucide-react";
import { Github } from "@/components/icons/GithubIcon";
import {
	useGitHubConfig,
	useGitHubRepositories,
	useConfigureGitHub,
	useCreateGitHubRepository,
	useDisconnectGitHub,
	validateGitHubToken,
	listGitHubBranches,
	type GitHubRepoInfo,
	type GitHubBranchInfo,
	type GitHubConfigResponse,
} from "@/hooks/useGitHub";

export function GitHub() {
	const [config, setConfig] = useState<GitHubConfigResponse | null>(null);
	const [saving, setSaving] = useState(false);
	const [testingToken, setTestingToken] = useState(false);
	const [loadingBranches, setLoadingBranches] = useState(false);

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
	const { data: configData, isLoading: configLoading } = useGitHubConfig();

	// Load repositories when token is saved but not configured
	const shouldLoadRepos = configData?.token_saved && !configData?.configured;
	const { data: reposData } = useGitHubRepositories(shouldLoadRepos ?? false);

	// Mutations
	const configureMutation = useConfigureGitHub();
	const createRepoMutation = useCreateGitHubRepository();
	const disconnectMutation = useDisconnectGitHub();
	// Track the saved token for use in configuration
	const [savedToken, setSavedToken] = useState<string | null>(null);

	// Mirror server-loaded configData into local state so handlers can patch
	// it without going back through the query cache. Adjust during render
	// with a previous-value sentinel to avoid setState-in-effect.
	const [prevConfigDataRef, setPrevConfigDataRef] =
		useState<GitHubConfigResponse | undefined>(undefined);
	if (configData && prevConfigDataRef !== configData) {
		setPrevConfigDataRef(configData);
		setConfig(configData);
		if (configData.token_saved && !configData.configured) {
			setTokenValid(true);
		}
	}

	// Load repositories when they become available for an unconfigured token.
	const [prevReposDataRef, setPrevReposDataRef] =
		useState<typeof reposData | undefined>(undefined);
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
				setSelectedRepo(response.detected_repo.full_name);
				setSelectedBranch(response.detected_repo.branch);

				// Load branches for detected repo
				try {
					const branchList = await listGitHubBranches(
						response.detected_repo.full_name,
					);
					setBranches(branchList);
				} catch (error) {
					console.error(
						"Failed to load branches for detected repo:",
						error,
					);
				}

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
	const handleRepoSelection = async (repoFullName: string) => {
		setSelectedRepo(repoFullName);
		setBranches([]);
		setSelectedBranch("main");

		if (!repoFullName) return;

		setLoadingBranches(true);
		try {
			const branchList = await listGitHubBranches(repoFullName);
			setBranches(branchList);

			// Auto-select main/master if available
			const defaultBranch =
				branchList.find((b) => b.name === "main") ||
				branchList.find((b) => b.name === "master");
			if (defaultBranch) {
				setSelectedBranch(defaultBranch.name);
			}
		} catch {
			toast.error("Failed to load branches");
		} finally {
			setLoadingBranches(false);
		}
	};

	// Create new repository
	const handleCreateRepository = async () => {
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
		} catch (error) {
			toast.error("Failed to create repository", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setCreatingRepo(false);
		}
	};

	// Configure GitHub integration
	const handleConfigure = async () => {
		// Token must be saved to configure
		if (!config?.token_saved && !savedToken) {
			toast.error("Please validate your token first");
			return;
		}

		if (!selectedRepo) {
			toast.error("Please select a repository");
			return;
		}

		// Proceed directly to configuration
		await handleSaveConfig();
	};

	// Save configuration (always replaces workspace with remote)
	const handleSaveConfig = async () => {
		setSaving(true);

		try {
			const setupResponse = await configureMutation.mutateAsync({
				body: {
					repo_url: selectedRepo,
					branch: selectedBranch,
				},
			});

			// Configuration is now async - show job queued message
			toast.success("GitHub configuration started", {
				description: `Job ${setupResponse.job_id} queued. Watch for notifications for progress.`,
			});
		} catch (error) {
			toast.error("Failed to save configuration", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	// Disconnect GitHub integration
	const handleDisconnect = async () => {
		setSaving(true);
		setShowDisconnectConfirm(false);

		try {
			await disconnectMutation.mutateAsync({});

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
			<div className="flex items-center justify-center py-12">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	return (
		<div className="space-y-6">
			<Card>
				<CardHeader>
					<div className="flex items-center gap-2">
						<Github className="h-5 w-5" />
						<CardTitle>GitHub Integration</CardTitle>
					</div>
					<CardDescription>
						Connect your workspace to a GitHub repository for
						version control and collaboration
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-6">
					{/* Current Status */}
					{config?.configured ? (
						<div className="space-y-4">
							<div className="rounded-lg border bg-muted/50 p-4">
								<div className="flex items-center justify-between mb-2">
									<div className="flex items-center gap-2">
										<CheckCircle2 className="h-4 w-4 text-green-500" />
										<span className="text-sm font-medium">
											Currently Connected
										</span>
									</div>
									<Button
										variant="destructive"
										size="sm"
										onClick={() =>
											setShowDisconnectConfirm(true)
										}
										disabled={saving}
									>
										{saving ? (
											<Loader2 className="h-4 w-4 animate-spin" />
										) : (
											"Disconnect"
										)}
									</Button>
								</div>
								<div className="space-y-1 text-sm text-muted-foreground">
									<div>
										<strong>Repository:</strong>{" "}
										{config.repo_url}
									</div>
									<div>
										<strong>Branch:</strong> {config.branch}
									</div>
								</div>
							</div>
							<p className="text-sm text-muted-foreground">
								Use the <strong>Source Control</strong> panel in
								the Code Editor to commit, push, and pull
								changes.
							</p>
						</div>
					) : (
						<>
							{/* GitHub Token */}
							<div className="space-y-2">
								<Label htmlFor="github-token">
									GitHub Personal Access Token
								</Label>
								<div className="flex flex-col gap-2 sm:flex-row">
									<Input
										id="github-token"
										type="password"
										autoComplete="off"
										placeholder={
											config?.token_saved
												? "Token saved - enter new token to change"
												: "ghp_xxxxxxxxxxxxxxxxxxxx"
										}
										value={token}
										onChange={(e) => {
											setToken(e.target.value);
											// Only invalidate if we actually had a validated token from manual validation
											// Don't clear if token was just saved (not manually validated)
											if (tokenValid === true) {
												setTokenValid(null);
											}
										}}
									/>
									<Button
										onClick={handleTokenValidation}
										disabled={testingToken || !token.trim()}
										variant={
											tokenValid === true
												? "default"
												: tokenValid === false
													? "destructive"
													: "secondary"
										}
										className="gap-2"
									>
										{testingToken ? (
											<>
												<Loader2 className="h-4 w-4 animate-spin" />
												Validating...
											</>
										) : tokenValid === true ? (
											<>
												<CheckCircle2 className="h-4 w-4" />
												Validated
											</>
										) : tokenValid === false ? (
											<>
												<AlertCircle className="h-4 w-4" />
												Invalid
											</>
										) : (
											"Validate"
										)}
									</Button>
								</div>
								<p className="text-xs text-muted-foreground">
									Create a token at{" "}
									<a
										href="https://github.com/settings/tokens/new"
										target="_blank"
										rel="noopener noreferrer"
										className="underline hover:text-foreground"
									>
										github.com/settings/tokens
									</a>{" "}
									with <code className="text-xs">repo</code>{" "}
									scope
								</p>
							</div>

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
											onClick={() =>
												setShowCreateRepo(true)
											}
										>
											<Plus className="h-4 w-4 mr-1" />
											Create New
										</Button>
									</div>
									<Select
										value={selectedRepo}
										onValueChange={handleRepoSelection}
									>
										<SelectTrigger id="repository">
											<SelectValue placeholder="Select a repository" />
										</SelectTrigger>
										<SelectContent>
											{repositories.map((repo) => (
												<SelectItem
													key={repo.full_name}
													value={repo.full_name}
												>
													<div className="flex items-center gap-2">
														<span>
															{repo.full_name}
														</span>
														{repo.private && (
															<span className="text-xs text-muted-foreground">
																(private)
															</span>
														)}
													</div>
												</SelectItem>
											))}
										</SelectContent>
									</Select>
								</div>
							)}

							{/* Branch Selection - always show if repo selected */}
							{(tokenValid || config?.token_saved) && (
								<div className="space-y-2">
									<Label htmlFor="branch">Branch</Label>
									<Select
										value={selectedBranch}
										onValueChange={setSelectedBranch}
										disabled={
											!selectedRepo || loadingBranches
										}
									>
										<SelectTrigger id="branch">
											{loadingBranches ? (
												<div className="flex items-center gap-2">
													<Loader2 className="h-4 w-4 animate-spin" />
													<span>
														Loading branches...
													</span>
												</div>
											) : (
												<SelectValue placeholder="Select a branch" />
											)}
										</SelectTrigger>
										<SelectContent>
											{branches.map((branch) => (
												<SelectItem
													key={branch.name}
													value={branch.name}
												>
													<div className="flex items-center gap-2">
														<span>
															{branch.name}
														</span>
														{branch.protected && (
															<span className="text-xs text-muted-foreground">
																(protected)
															</span>
														)}
													</div>
												</SelectItem>
											))}
										</SelectContent>
									</Select>
									{!selectedRepo && (
										<p className="text-xs text-muted-foreground">
											Select a repository first
										</p>
									)}
								</div>
							)}

							{/* Save Button */}
							<div className="flex justify-end">
								<Button
									onClick={handleConfigure}
									disabled={
										saving ||
										!selectedRepo ||
										(!config?.token_saved &&
											!token.trim()) ||
										(!config?.token_saved &&
											tokenValid !== true)
									}
								>
									{saving ? (
										<>
											<Loader2 className="h-4 w-4 mr-2 animate-spin" />
											Configuring...
										</>
									) : (
										<>
											<Github className="h-4 w-4 mr-2" />
											Configure GitHub
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

			{/* Create Repository Modal */}
			<Dialog open={showCreateRepo} onOpenChange={setShowCreateRepo}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Create New Repository</DialogTitle>
						<DialogDescription>
							Create a new GitHub repository in your account
						</DialogDescription>
					</DialogHeader>

					<div className="space-y-4">
						<div className="space-y-2">
							<Label htmlFor="new-repo-name">
								Repository Name
							</Label>
							<Input
								id="new-repo-name"
								placeholder="my-repository"
								value={newRepoName}
								onChange={(e) => setNewRepoName(e.target.value)}
							/>
						</div>

						<div className="space-y-2">
							<Label htmlFor="new-repo-desc">
								Description (Optional)
							</Label>
							<Input
								id="new-repo-desc"
								placeholder="A brief description"
								value={newRepoDescription}
								onChange={(e) =>
									setNewRepoDescription(e.target.value)
								}
							/>
						</div>

						<div className="flex items-center space-x-2">
							<input
								type="checkbox"
								id="new-repo-private"
								checked={newRepoPrivate}
								onChange={(e) =>
									setNewRepoPrivate(e.target.checked)
								}
								className="h-4 w-4"
							/>
							<Label
								htmlFor="new-repo-private"
								className="text-sm font-normal"
							>
								Private repository
							</Label>
						</div>
					</div>

					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => setShowCreateRepo(false)}
						>
							Cancel
						</Button>
						<Button
							onClick={handleCreateRepository}
							disabled={!newRepoName.trim() || creatingRepo}
						>
							{creatingRepo ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Creating...
								</>
							) : (
								"Create Repository"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			{/* Disconnect Confirmation Dialog */}
			<Dialog
				open={showDisconnectConfirm}
				onOpenChange={setShowDisconnectConfirm}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Disconnect GitHub Integration</DialogTitle>
						<DialogDescription>
							Are you sure you want to disconnect GitHub
							integration? This will remove all stored credentials
							and you'll need to reconfigure to reconnect.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => setShowDisconnectConfirm(false)}
							disabled={saving}
						>
							Cancel
						</Button>
						<Button
							variant="destructive"
							onClick={handleDisconnect}
							disabled={saving}
						>
							{saving ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Disconnecting...
								</>
							) : (
								"Disconnect"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
