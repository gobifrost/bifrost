import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/contexts/AuthContext";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { Button } from "@/components/ui/button";
import { GitHubAppSetup } from "./GitHubAppSetup";
import {
	disconnectSolutionGitSource,
	listSolutionGitSources,
	reuseSolutionGitApp,
	type GitAppSetup,
} from "@/services/githubGitApp";

export function SolutionGitSources({
	onPurposeDetected,
	callbackSetup,
}: {
	onPurposeDetected: (
		purpose: "workspace" | "solution",
		setup: GitAppSetup,
	) => void;
	callbackSetup?: GitAppSetup;
}) {
	const { user } = useAuth();
	const [scope, setScope] = useState<string | null>(
		callbackSetup
			? (callbackSetup.source_organization_id ?? null)
			: (user?.organizationId ?? null),
	);
	const [initialSetup, setInitialSetup] = useState<
		GitAppSetup | null | undefined
	>(callbackSetup);
	const [attempt, setAttempt] = useState(0);
	const [error, setError] = useState<string | null>(null);
	const [pending, setPending] = useState(false);
	const sources = useQuery({
		queryKey: ["solution-github-sources", scope],
		queryFn: () => listSolutionGitSources(scope),
	});

	async function perform(action: () => Promise<void>) {
		setError(null);
		setPending(true);
		try {
			await action();
		} catch (caught) {
			setError(
				caught instanceof Error
					? caught.message
					: "Source setup failed.",
			);
		} finally {
			setPending(false);
		}
	}

	async function reuse(sourceId?: string) {
		const setup = await reuseSolutionGitApp(scope, sourceId);
		setInitialSetup(setup);
		setAttempt((value) => value + 1);
	}

	return (
		<section
			aria-label="Solution GitHub Sources"
			className="space-y-4 rounded-md border p-4"
		>
			<h3 className="font-medium">Solution Repository Sources</h3>
			<p className="text-sm text-muted-foreground">
				Authorize private repositories for this scope. Each Solution
				chooses its own repository and ref.
			</p>
			<OrganizationSelect
				aria-label="Source Organization"
				value={scope}
				onChange={(value) => {
					setScope(value ?? null);
					setInitialSetup(null);
					setAttempt((current) => current + 1);
				}}
				disabled={pending}
				showGlobal
			/>
			{(error || sources.error) && (
				<p role="alert" className="text-sm text-destructive">
					{error ?? sources.error?.message}
				</p>
			)}
			<Button
				variant="outline"
				disabled={pending}
				onClick={() => void perform(() => reuse())}
			>
				Use Workspace App Registration
			</Button>
			<div className="max-h-64 space-y-3 overflow-auto">
				{sources.data?.sources.map((source) => (
					<div key={source.id} className="rounded border p-3">
						<p className="font-medium">
							{source.app_name} ·{" "}
							{source.enabled ? "Connected" : "Disconnected"}
						</p>
						<ul className="text-sm">
							{source.repositories.map((repo) => (
								<li key={repo.id}>{repo.full_name}</li>
							))}
						</ul>
						<div className="mt-2 flex gap-2">
							<Button
								variant="outline"
								disabled={pending}
								onClick={() =>
									void perform(() => reuse(source.id))
								}
							>
								{source.enabled
									? "Add Repository"
									: "Reconnect Source"}
							</Button>
							{source.enabled && (
								<Button
									variant="outline"
									disabled={pending}
									onClick={() =>
										void perform(async () => {
											await disconnectSolutionGitSource(
												source.id,
												scope,
											);
											await sources.refetch();
										})
									}
								>
									Disconnect Source
								</Button>
							)}
						</div>
					</div>
				))}
			</div>
			<GitHubAppSetup
				key={`${attempt}:${initialSetup?.setup_id ?? "new"}`}
				purpose="solution"
				sourceOrganizationId={scope}
				initialSetup={initialSetup}
				connectedWithApp={false}
				onPurposeDetected={onPurposeDetected}
				onSourceScopeDetected={setScope}
				onReady={() => undefined}
				onMigrated={() => {
					void sources.refetch();
				}}
			/>
		</section>
	);
}
