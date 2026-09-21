import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAgentPlatformUpdates } from "@/hooks/useAgentPlatformUpdates";
import { agentPlatform } from "@/services/agentPlatform";
import { listModelProfiles } from "@/services/aiModels";
import { ExecutionResults } from "@/components/agents/evaluation/ExecutionResults";
import {
	PlatformError,
	PlatformStatus,
} from "@/components/agents/evaluation/PlatformEvidence";
import type { components } from "@/lib/v1";

type MatrixCell = components["schemas"]["MatrixCellPublic"];

/**
 * MatrixPanel — saved multi-profile comparison for one suite + candidate.
 *
 * Admits one atomic execution per (candidate-or-baseline, profile) cell
 * through the shared PlatformJob contract (no polling: the shared
 * notification channel invalidates these queries). Every candidate cell
 * pairs with the baseline-only cell under the same profile. Applying a
 * candidate stays explicit (`CandidatePromotion`): test success never
 * publishes.
 */
export function MatrixPanel({
	agentId,
	suiteId,
	candidateId,
	profileIds,
	matrixId,
	onProfilesChange,
	onMatrix,
}: {
	agentId: string;
	suiteId: string;
	candidateId: string;
	profileIds: string[];
	matrixId: string;
	onProfilesChange: (profiles: string[]) => void;
	onMatrix: (matrixId: string) => void;
}) {
	const client = useQueryClient();
	const [expandedCell, setExpandedCell] = useState<string | null>(null);
	const profiles = useQuery({
		queryKey: ["ai", "model-profiles"],
		queryFn: listModelProfiles,
	});
	const candidate = useQuery({
		queryKey: ["agent-platform", "candidate", candidateId],
		queryFn: () => agentPlatform.candidate(candidateId),
		enabled: !!candidateId,
	});
	const matrix = useQuery({
		queryKey: ["agent-platform", "matrix", matrixId],
		queryFn: () => agentPlatform.matrix(matrixId),
		enabled: !!matrixId,
	});
	useAgentPlatformUpdates();
	const refresh = () =>
		void client.invalidateQueries({ queryKey: ["agent-platform"] });
	const batch = useMutation({
		mutationFn: () =>
			agentPlatform.executeBatch({
				suite_id: suiteId,
				candidate_ids: candidateId ? [candidateId] : [],
				profile_ids: profileIds,
			}),
		onSuccess: (data) => {
			refresh();
			onMatrix(data.matrix.id);
		},
	});
	const cancel = useMutation({
		mutationFn: () => agentPlatform.cancelMatrix(matrixId),
		onSuccess: refresh,
	});
	function toggleProfile(profileId: string) {
		onProfilesChange(
			profileIds.includes(profileId)
				? profileIds.filter((id) => id !== profileId)
				: [...profileIds, profileId],
		);
	}
	const cells: MatrixCell[] = matrix.data?.cells ?? [];
	const active = cells.some((cell) =>
		["queued", "running", "waiting"].includes(cell.status),
	);
	const profileById = new Map(
		(profiles.data ?? []).map((profile) => [profile.id, profile]),
	);
	function cellProfileLabel(profileId: string | null | undefined): string {
		if (!profileId) return "Default model";
		const profile = profileById.get(profileId);
		return profile
			? `${profile.name} · ${profile.model}`
			: "Profile unavailable";
	}
	// Next-run selection (what the Run button will start).
	const selectedProfileCount = profileIds.length;
	const selectedComparisons =
		selectedProfileCount * (candidateId ? 2 : 1);
	function comparisonNoun(count: number): string {
		return count === 1 ? "comparison" : "comparisons";
	}
	// Saved run (what already ran): derived from the stored definition and
	// live cell states, never from the current selection.
	const savedProfileIds = matrix.data?.matrix.profile_ids ?? [];
	const savedProfileCount = new Set(savedProfileIds).size;
	const liveCells = cells.filter((cell) => !cell.candidate_id);
	const candidateCells = cells.filter((cell) => cell.candidate_id);
	const failedCells = cells.filter(
		(cell) =>
			cell.status === "failed" ||
			cell.status === "error" ||
			cell.failed_cases > 0,
	);
	const cancelledCells = cells.filter(
		(cell) => cell.status === "cancelled",
	);
	const pendingCells = cells.filter(
		(cell) =>
			["queued", "running", "waiting"].includes(cell.status) ||
			cell.completed_cases < cell.total_cases,
	);
	const allSucceeded =
		cells.length > 0 &&
		cells.every((cell) => cell.status === "succeeded");
	const savedProfileNames = savedProfileIds.map(
		(id: string) => profileById.get(id)?.name ?? "Profile unavailable",
	);
	const candidateOverlayProfile = (
		candidate.data?.overlays as Record<string, unknown> | undefined
	)?.llm_profile_id;
	const candidateProfileDiffers =
		typeof candidateOverlayProfile === "string" &&
		candidateOverlayProfile.length > 0 &&
		!profileIds.includes(candidateOverlayProfile);
	return (
		<section aria-label="Test runs" className="space-y-5">
			<div className="rounded-lg border p-4">
				<h3 className="text-lg font-semibold">Run tests</h3>
				<p className="mt-1 text-sm text-muted-foreground">
					Run this suite with the selected profiles. Model calls
					are real; tool responses are simulated.
				</p>
				<div className="mt-4 space-y-2">
					<Label id="matrix-profiles-label">
						Provider profiles
					</Label>
					<PlatformError
						error={profiles.error}
						retry={() => void profiles.refetch()}
					/>
					{profiles.isPending && (
						<p role="status">Loading profiles…</p>
					)}
					<ul
						aria-labelledby="matrix-profiles-label"
						className="grid gap-2 sm:grid-cols-2"
					>
						{profiles.data?.map((profile) => (
							<li key={profile.id}>
								<label className="flex min-h-11 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm">
									<Input
										type="checkbox"
										className="h-4 w-4"
										checked={profileIds.includes(
											profile.id,
										)}
										onChange={() =>
											toggleProfile(profile.id)
										}
									/>
									<span className="min-w-0">
										<span className="block truncate font-medium">
											{profile.name}
										</span>
										<span className="block truncate text-xs text-muted-foreground">
											{profile.connection.name} ·{" "}
											{profile.model}
										</span>
									</span>
								</label>
							</li>
						))}
					</ul>
				</div>
				<PlatformError error={batch.error} />
				{selectedProfileCount > 0 && (
					<p className="mt-3 text-sm text-muted-foreground">
						{selectedProfileCount} profile
						{selectedProfileCount === 1 ? "" : "s"} ·{" "}
						{selectedComparisons}{" "}
						{comparisonNoun(selectedComparisons)}
						{candidateId
							? ` (${selectedProfileCount} Live agent + ${selectedProfileCount} Proposed changes)`
							: " (Live agent)"}
					</p>
				)}
				{candidateProfileDiffers && (
					<p className="mt-2 text-sm text-muted-foreground">
						The proposed changes include another model profile;
						comparisons run under the selected profiles.
					</p>
				)}
				<div className="mt-3 flex flex-wrap gap-2">
					<Button
						disabled={
							batch.isPending ||
							!suiteId ||
							!profileIds.length ||
							(!!candidateId && !candidate.data)
						}
						onClick={() => batch.mutate()}
					>
						{batch.isPending
							? "Starting tests…" : "Run tests"}
					</Button>
					{active && (
						<Button
							variant="outline"
							disabled={cancel.isPending}
							onClick={() => cancel.mutate()}
						>
							Cancel test run
						</Button>
					)}
				</div>
				<PlatformError error={cancel.error} />
			</div>
			{matrixId && (
				<div className="space-y-4">
					<h3 className="text-lg font-semibold">Test results</h3>
					{matrix.data && (
						<div className="space-y-1">
							<p className="text-sm text-muted-foreground">
								Saved run · {savedProfileCount} profile
								{savedProfileCount === 1 ? "" : "s"} (
								{savedProfileNames.join(", ") || "none"}) ·{" "}
								{cells.length}{" "}
								{comparisonNoun(cells.length)}:{" "}
								{liveCells.length} Live agent,{" "}
								{candidateCells.length} Proposed changes
							</p>
							{failedCells.length > 0 ? (
								<p
									role="status"
									className="text-sm text-destructive"
								>
									Needs attention: {failedCells.length} of{" "}
									{cells.length}{" "}
									{comparisonNoun(cells.length)} failed
								</p>
							) : cancelledCells.length > 0 ? (
								<p role="status" className="text-sm">
									Cancelled: {cancelledCells.length} of{" "}
									{cells.length}{" "}
									{comparisonNoun(cells.length)} cancelled ·{" "}
									{cells.reduce(
										(sum, cell) =>
											sum + cell.completed_cases,
										0,
									)}{" "}
									of{" "}
									{cells.reduce(
										(sum, cell) => sum + cell.total_cases,
										0,
									)}{" "}
									test runs complete
								</p>
							) : pendingCells.length > 0 ? (
								<p role="status" className="text-sm">
									Running:{" "}
									{cells.reduce(
										(sum, cell) =>
											sum + cell.completed_cases,
										0,
									)}{" "}
									of{" "}
									{cells.reduce(
										(sum, cell) => sum + cell.total_cases,
										0,
									)}{" "}
									test runs complete
								</p>
							) : (
								allSucceeded && (
									<p
										role="status"
										className="text-sm text-[var(--bf-success)]"
									>
										All {cells.length}{" "}
										{comparisonNoun(cells.length)} passed
									</p>
								)
							)}
							<details>
								<summary className="cursor-pointer text-xs text-muted-foreground">
									Advanced
								</summary>
								<p className="mt-1 text-xs text-muted-foreground">
									{matrix.data.planned_runs} planned test
									runs · {matrix.data.cost_note}
								</p>
							</details>
						</div>
					)}
					<PlatformError
						error={matrix.error}
						retry={() => void matrix.refetch()}
					/>
					{matrix.isPending && (
						<p role="status">Loading matrix…</p>
					)}
					<ul className="divide-y rounded-lg border">
						{cells.map((cell) => (
							<li key={cell.execution_id} className="space-y-3 p-4">
								<div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
									<div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
										<PlatformStatus status={cell.status} />
										<span className="text-sm font-medium">
											{cell.candidate_id
												? "Proposed changes"
												: "Live agent"}
										</span>
										<span className="min-w-0 break-words text-xs text-muted-foreground">
											{cellProfileLabel(cell.profile_id)}
										</span>
										{!cell.profile_id ||
										!profileById.get(cell.profile_id) ? (
											<details className="text-xs text-muted-foreground">
												<summary className="cursor-pointer">
													Advanced
												</summary>
												<span className="break-all font-mono">
													{cell.profile_id ??
														"no profile recorded"}
												</span>
											</details>
										) : null}
									</div>
									<div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
										<span>
											{cell.completed_cases}/
											{cell.total_cases} done ·{" "}
											{cell.passed_cases} passed ·{" "}
											{cell.failed_cases} failed
										</span>
										<Button
											size="sm"
											variant="outline"
											onClick={() =>
												setExpandedCell(
													expandedCell ===
														cell.execution_id
														? null
														: cell.execution_id,
												)
											}
										>
											{expandedCell === cell.execution_id
												? "Hide results"
												: "Inspect results"}
										</Button>
									</div>
								</div>
								{expandedCell === cell.execution_id && (
									<div className="mt-3">
										<CellResults
											executionId={cell.execution_id}
											suiteId={suiteId}
											agentId={agentId}
										/>
									</div>
								)}
							</li>
						))}
					</ul>
					{!matrix.isPending && !cells.length && (
						<p className="py-4 text-sm text-muted-foreground">
							This matrix has no cells yet.
						</p>
					)}
				</div>
			)}
		</section>
	);
}

function CellResults({
	executionId,
	suiteId,
	agentId,
}: {
	executionId: string;
	suiteId: string;
	agentId: string;
}) {
	const results = useQuery({
		queryKey: ["agent-platform", "results", executionId],
		queryFn: () => agentPlatform.results(executionId),
	});
	const cases = useQuery({
		queryKey: ["agent-platform", "cases", suiteId],
		queryFn: () => agentPlatform.cases(suiteId),
		enabled: !!suiteId,
	});
	useAgentPlatformUpdates();
	if (results.isPending)
		return <p role="status">Loading test results…</p>;
	if (results.error)
		return (
			<PlatformError
				error={results.error}
				retry={() => void results.refetch()}
			/>
		);
	return (
		<ExecutionResults
			results={results.data ?? []}
			cases={cases.data ?? []}
			agentId={agentId}
		/>
	);
}
