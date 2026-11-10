import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import {
	getGitAppSetup,
	createGitApp,
	configureExistingGitApp,
	authorizeGitApp,
	refreshGitAppInstallations,
	listGitAppRepositories,
	selectGitAppRepository,
	takeGitAppCallback,
	completeGitAppCallback,
	submitGitAppManifest,
	openGitAppAuthorization,
	type GitAppSetup,
	type GitAppRepository,
} from "@/services/githubGitApp";

const setupLabels: Record<GitAppSetup["stage"], string> = {
	registration: "Registering App",
	registration_needs_review: "Registration Needs Review",
	authorization: "Authorization Required",
	installation: "Installation Required",
	repository: "Select a Repository",
	ready: "Ready to Connect",
	connected: "Connected",
};

export function GitHubAppSetup({
	onReady,
	onMigrated,
	connectedWithApp,
	purpose = "workspace",
	sourceOrganizationId = null,
	initialSetup,
	onPurposeDetected,
	onSourceScopeDetected,
}: {
	onReady: (setupId: string, repository: GitAppRepository) => void;
	onMigrated: () => void;
	connectedWithApp: boolean;
	purpose?: "workspace" | "solution";
	sourceOrganizationId?: string | null;
	initialSetup?: GitAppSetup | null;
	onPurposeDetected?: (
		purpose: "workspace" | "solution",
		setup: GitAppSetup,
	) => void;
	onSourceScopeDetected?: (organizationId: string | null) => void;
}) {
	const initialized = useRef(false);
	const [setup, setSetup] = useState<GitAppSetup | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [pending, setPending] = useState(false);
	const [owner, setOwner] = useState("");
	const [ownerType, setOwnerType] = useState<"organization" | "user">(
		"organization",
	);
	const [name, setName] = useState(
		purpose === "solution" ? "Bifrost Solutions" : "Bifrost Git Sync",
	);
	const [isPublic, setPublic] = useState(false);
	const [workflows, setWorkflows] = useState(false);
	const [manual, setManual] = useState(false);
	const [appId, setAppId] = useState("");
	const [clientId, setClientId] = useState("");
	const [privateKey, setPrivateKey] = useState("");
	const [clientSecret, setClientSecret] = useState("");
	const [installation, setInstallation] = useState("");
	const [repository, setRepository] = useState("");

	async function perform(action: () => Promise<void>) {
		setPending(true);
		setError(null);
		try {
			await action();
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "GitHub setup failed.",
			);
		} finally {
			setPending(false);
		}
	}

	useEffect(() => {
		if (initialized.current) return;
		if (!connectedWithApp) initialized.current = true;
		void perform(async () => {
			const callback = takeGitAppCallback();
			if (connectedWithApp && !callback) return;
			const current = callback
				? await completeGitAppCallback(callback)
				: initialSetup !== undefined
					? initialSetup
					: await getGitAppSetup(purpose);
			if (current && (current.purpose ?? "workspace") !== purpose) {
				onPurposeDetected?.(current.purpose ?? "workspace", current);
				return;
			}
			if (current?.purpose === "solution")
				onSourceScopeDetected?.(current.source_organization_id ?? null);
			setSetup(current);
		});
		// This component owns one setup attempt; parent keys start another.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [connectedWithApp]);

	if (connectedWithApp)
		return (
			<p className="text-sm text-muted-foreground">
				Git sync uses your GitHub App installation.
			</p>
		);

	return (
		<section
			aria-label="GitHub App Setup"
			className="space-y-4 rounded-md border p-4"
		>
			<div>
				<h3 className="font-medium">
					{purpose === "solution"
						? "Authorize Solution Repositories"
						: "Connect With a GitHub App"}
				</h3>
				<p className="text-sm text-muted-foreground">
					{purpose === "solution"
						? "Authorize selected repositories for Solution installs and updates. Fetches use temporary read-only credentials."
						: "Create an App owned by your account or organization. Git sync uses temporary repository credentials."}
				</p>
			</div>
			{error && (
				<p role="alert" className="text-sm text-destructive">
					{error}
				</p>
			)}
			{setup && (
				<p role="status" className="text-sm">
					{setup.app_name ?? "App Registration"}:{" "}
					{setupLabels[setup.stage]}
				</p>
			)}
			{(!setup ||
				setup.stage === "registration" ||
				setup.stage === "registration_needs_review" ||
				setup.stage === "connected") && (
				<>
					{setup?.stage === "registration_needs_review" && (
						<p className="text-sm">
							Registration may have completed.{" "}
							<a
								className="underline"
								href="https://github.com/settings/apps"
								target="_blank"
								rel="noreferrer"
							>
								Review Your GitHub Apps
							</a>{" "}
							before starting another registration. Use Existing
							App to recover its credentials.
						</p>
					)}
					{setup?.stage === "registration_needs_review" && (
						<Button
							variant="outline"
							disabled={pending}
							onClick={() => setSetup(null)}
						>
							I Checked GitHub; Start a New Registration
						</Button>
					)}
					<Button
						variant="outline"
						onClick={() => setManual(!manual)}
						disabled={pending}
					>
						{manual ? "Create a New App" : "Use Existing App"}
					</Button>
					{manual ? (
						<div className="space-y-3">
							<Label htmlFor="git-app-id">App ID</Label>
							<Input
								id="git-app-id"
								inputMode="numeric"
								value={appId}
								onChange={(event) =>
									setAppId(event.target.value)
								}
							/>
							<Label htmlFor="git-app-client-id">Client ID</Label>
							<Input
								id="git-app-client-id"
								value={clientId}
								onChange={(event) =>
									setClientId(event.target.value)
								}
							/>
							<Label htmlFor="git-app-key">Private Key</Label>
							<Textarea
								id="git-app-key"
								autoComplete="off"
								value={privateKey}
								onChange={(event) =>
									setPrivateKey(event.target.value)
								}
							/>
							<Label htmlFor="git-app-secret">
								Client Secret
							</Label>
							<Input
								id="git-app-secret"
								type="password"
								autoComplete="off"
								value={clientSecret}
								onChange={(event) =>
									setClientSecret(event.target.value)
								}
							/>
							<Button
								disabled={
									pending ||
									!appId ||
									!clientId ||
									!privateKey ||
									!clientSecret
								}
								onClick={() =>
									void perform(async () => {
										const current =
											await configureExistingGitApp({
												app_id: Number(appId),
												client_id: clientId,
												private_key: privateKey,
												client_secret: clientSecret,
												purpose,
												...(purpose === "solution"
													? {
															organization_id:
																sourceOrganizationId,
														}
													: {}),
											});
										setPrivateKey("");
										setClientSecret("");
										setSetup(current);
									})
								}
							>
								Verify Existing App
							</Button>
						</div>
					) : (
						setup?.stage !== "registration_needs_review" && (
							<div className="space-y-3">
								<Label htmlFor="git-app-owner">
									GitHub Owner
								</Label>
								<Input
									id="git-app-owner"
									value={owner}
									onChange={(event) =>
										setOwner(event.target.value)
									}
									placeholder="Your organization or username"
								/>
								<Label htmlFor="git-app-owner-type">
									Owner Type
								</Label>
								<select
									className="w-full rounded-md border bg-background p-2"
									id="git-app-owner-type"
									value={ownerType}
									onChange={(event) =>
										setOwnerType(
											event.target
												.value as typeof ownerType,
										)
									}
								>
									<option value="organization">
										Organization
									</option>
									<option value="user">
										Personal Account
									</option>
								</select>
								<Label htmlFor="git-app-name">App Name</Label>
								<Input
									id="git-app-name"
									value={name}
									onChange={(event) =>
										setName(event.target.value)
									}
								/>
								<div className="flex items-center gap-2">
									<Checkbox
										id="git-app-public"
										checked={isPublic}
										onCheckedChange={(value) =>
											setPublic(value === true)
										}
									/>
									<Label htmlFor="git-app-public">
										Allow Installation on Other GitHub
										Accounts
									</Label>
								</div>
								{isPublic && (
									<p className="text-sm text-muted-foreground">
										This registers a public App. Other
										accounts can install it; Bifrost still
										requires verified access before
										connecting.
									</p>
								)}
								{purpose === "workspace" && (
									<div className="flex items-center gap-2">
										<Checkbox
											id="git-app-workflows"
											checked={workflows}
											onCheckedChange={(value) =>
												setWorkflows(value === true)
											}
										/>
										<Label htmlFor="git-app-workflows">
											Allow Updating GitHub Actions
											Workflow Files
										</Label>
									</div>
								)}
								<p className="text-sm text-muted-foreground">
									GitHub will ask you to approve Contents
									{purpose === "solution"
										? " read"
										: " read/write"}
									{workflows ? " and Workflows write" : ""}{" "}
									access.
								</p>
								<Button
									disabled={
										pending || !owner.trim() || !name.trim()
									}
									onClick={() =>
										void perform(async () => {
											submitGitAppManifest(
												await createGitApp({
													owner,
													owner_type: ownerType,
													name,
													public: isPublic,
													workflows,
													purpose,
													...(purpose === "solution"
														? {
																organization_id:
																	sourceOrganizationId,
															}
														: {}),
												}),
											);
										})
									}
								>
									Create GitHub App
								</Button>
							</div>
						)
					)}
				</>
			)}
			{setup?.app_name && setup.stage !== "connected" && (
				<div className="flex flex-wrap gap-2">
					<Button
						disabled={pending}
						onClick={() =>
							void perform(async () => {
								openGitAppAuthorization(
									(await authorizeGitApp(setup.setup_id))
										.authorization_url,
								);
							})
						}
					>
						Authorize With GitHub
					</Button>
					{setup.install_url && (
						<Button asChild variant="outline">
							<a href={setup.install_url} rel="noreferrer">
								Install App / Manage Access
							</a>
						</Button>
					)}
					<Button
						variant="outline"
						disabled={pending}
						onClick={() =>
							void perform(async () => {
								setSetup(
									await refreshGitAppInstallations(
										setup.setup_id,
									),
								);
							})
						}
					>
						Refresh Installations
					</Button>
				</div>
			)}
			{!!setup?.installations?.length && (
				<div className="space-y-3">
					<Label htmlFor="git-app-installation">Installation</Label>
					<select
						className="w-full rounded-md border bg-background p-2"
						id="git-app-installation"
						disabled={pending}
						value={installation}
						onChange={(event) => {
							const value = event.target.value;
							setInstallation(value);
							setRepository("");
							if (value)
								void perform(async () => {
									setSetup(
										await listGitAppRepositories(
											setup.setup_id,
											Number(value),
										),
									);
								});
						}}
					>
						<option value="">Select an Installation</option>
						{setup.installations.map((item) => (
							<option key={item.id} value={item.id}>
								{item.account}
							</option>
						))}
					</select>
					<Label htmlFor="git-app-repository">App Repository</Label>
					<select
						className="w-full rounded-md border bg-background p-2"
						id="git-app-repository"
						disabled={pending || !installation}
						value={repository}
						onChange={(event) => setRepository(event.target.value)}
					>
						<option value="">Select a Repository</option>
						{setup.repositories.map((item) => (
							<option key={item.id} value={item.id}>
								{item.full_name}
							</option>
						))}
					</select>
					<Button
						disabled={pending || !installation || !repository}
						onClick={() =>
							void perform(async () => {
								const current = await selectGitAppRepository(
									setup.setup_id,
									Number(installation),
									Number(repository),
								);
								setSetup(current);
								if (current.stage === "connected") onMigrated();
								else {
									const selected = current.repositories.find(
										(item) =>
											item.id === Number(repository),
									);
									if (selected)
										onReady(current.setup_id, selected);
								}
							})
						}
					>
						Use Selected Repository
					</Button>
				</div>
			)}
		</section>
	);
}
