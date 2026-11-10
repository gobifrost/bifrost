import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { getSolutionGitSources } from "@/services/solutions";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

export function SolutionGitSourcePicker({
	organizationId,
	sourceId,
	repoUrl,
	onChange,
	disabled = false,
}: {
	organizationId: string | null | undefined;
	sourceId: string | null;
	repoUrl: string;
	onChange: (
		sourceId: string | null,
		repoUrl?: string,
		defaultBranch?: string,
	) => void;
	disabled?: boolean;
}) {
	const id = useId();
	const sources = useQuery({
		queryKey: [
			"solution-git-sources",
			organizationId === undefined
				? "HOME"
				: (organizationId ?? "GLOBAL"),
		],
		queryFn: () => getSolutionGitSources(organizationId),
	});
	const options = (sources.data?.sources ?? [])
		.filter((source) => source.enabled)
		.flatMap((source) =>
			source.repositories.map((repo) => ({
				source,
				repo,
				value: `${source.id}:${repo.id}`,
			})),
		);
	const selected = options.find(
		(option) =>
			option.source.id === sourceId &&
			`https://github.com/${option.repo.full_name}`.toLowerCase() ===
				repoUrl
					.replace(/\.git\/?$/, "")
					.replace(/\/$/, "")
					.toLowerCase(),
	);
	const unavailable = sourceId !== null && !selected;
	return (
		<div className="space-y-1.5">
			<Label htmlFor={id}>Repository Access</Label>
			<select
				id={id}
				className="w-full rounded-md border bg-background p-2"
				disabled={disabled || sources.isLoading}
				value={
					selected?.value ?? (unavailable ? "unavailable" : "public")
				}
				onChange={(event) => {
					if (event.target.value === "public") onChange(null);
					else {
						const option = options.find(
							(item) => item.value === event.target.value,
						);
						if (option)
							onChange(
								option.source.id,
								`https://github.com/${option.repo.full_name}`,
								option.repo.default_branch,
							);
					}
				}}
			>
				<option value="public">Public Repository</option>
				{unavailable && (
					<option value="unavailable" disabled>
						Unavailable App Source
					</option>
				)}
				{options.map((option) => (
					<option key={option.value} value={option.value}>
						{option.source.app_name} · {option.repo.full_name}
					</option>
				))}
			</select>
			{unavailable && (
				<p role="alert" className="text-sm text-destructive">
					The selected App source is unavailable for this scope or
					repository. Select an authorized source before fetching.
				</p>
			)}
			{sources.error && (
				<p role="alert" className="text-sm text-destructive">
					{sources.error.message}
				</p>
			)}
			<p className="text-xs text-muted-foreground">
				Private sources are authorized in Settings → GitHub → Solution
				Repositories.
			</p>
		</div>
	);
}

export function PrivateSolutionRepositoryFields({
	repoUrl,
	subpath,
	gitRef,
	onSubpathChange,
	onRefChange,
	onDisconnect,
}: {
	repoUrl: string;
	subpath: string;
	gitRef: string;
	onSubpathChange: (value: string) => void;
	onRefChange: (value: string) => void;
	onDisconnect: () => void;
}) {
	const id = useId();
	return (
		<div className="space-y-2 rounded-md border p-3">
			<Label htmlFor={`${id}-url`}>Repository URL</Label>
			<Input id={`${id}-url`} readOnly value={repoUrl} />
			<Label htmlFor={`${id}-path`}>Subfolder</Label>
			<Input
				id={`${id}-path`}
				value={subpath}
				onChange={(event) => onSubpathChange(event.target.value)}
			/>
			<Label htmlFor={`${id}-ref`}>Ref</Label>
			<Input
				id={`${id}-ref`}
				value={gitRef}
				onChange={(event) => onRefChange(event.target.value)}
			/>
			<Button type="button" variant="outline" onClick={onDisconnect}>
				Disconnect Git
			</Button>
		</div>
	);
}
