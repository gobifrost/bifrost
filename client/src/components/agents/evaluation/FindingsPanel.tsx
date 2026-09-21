import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { SearchBox } from "@/components/search/SearchBox";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { agentPlatform } from "@/services/agentPlatform";
import {
	EvidenceJson,
	PlatformError,
} from "@/components/agents/evaluation/PlatformEvidence";
import type { components } from "@/lib/v1";

type Finding = components["schemas"]["FindingPublic"];

/**
 * FindingsPanel — reviewed, dismissable problem records for one Agent.
 *
 * Findings are evidence-first: manual, run-sourced (run + journal
 * sequence), or external references (never fetched). Dismissal needs no
 * test; a passing test never resolves a finding. "New test" hands the
 * finding to the Tests tab, which saves it as a reviewed test.
 */
export function FindingsPanel({
	agentId,
	onNewTest,
}: {
	agentId: string;
	onNewTest: (finding: Finding) => void;
}) {
	const client = useQueryClient();
	const [open, setOpen] = useState(false);
	const [statusFilter, setStatusFilter] = useState<"open" | "dismissed">(
		"open",
	);
	const [search, setSearch] = useState("");
	const [description, setDescription] = useState("");
	const [expected, setExpected] = useState("");
	const [sourceKind, setSourceKind] = useState<"manual" | "run" | "external">(
		"manual",
	);
	const [sourceRunId, setSourceRunId] = useState("");
	const [sourceSequence, setSourceSequence] = useState("");
	const [externalRef, setExternalRef] = useState("");
	const findings = useQuery({
		queryKey: ["agent-platform", "findings", agentId],
		queryFn: () => agentPlatform.findings(agentId),
	});
	const refresh = () =>
		void client.invalidateQueries({
			queryKey: ["agent-platform", "findings", agentId],
		});
	const create = useMutation({
		mutationFn: () =>
			agentPlatform.createFinding({
				agent_id: agentId,
				description: description.trim(),
				expected_behavior: expected.trim() || null,
				source_kind: sourceKind,
				finding_kind: "problem",
				source_run_id:
					sourceKind === "run" && sourceRunId.trim()
						? sourceRunId.trim()
						: null,
				source_sequence:
					sourceKind === "run" && sourceSequence.trim()
						? Number(sourceSequence)
						: null,
				external_ref:
					sourceKind === "external" && externalRef.trim()
						? externalRef.trim()
						: null,
			}),
		onSuccess: () => {
			refresh();
			setOpen(false);
			setDescription("");
			setExpected("");
			setSourceRunId("");
			setSourceSequence("");
			setExternalRef("");
		},
	});
	const dismiss = useMutation({
		mutationFn: (findingId: string) =>
			agentPlatform.updateFinding(findingId, { status: "dismissed" }),
		onSuccess: refresh,
	});
	const visible = (findings.data ?? []).filter(
		(item) => item.status === statusFilter,
	);
	const query = search.trim().toLowerCase();
	const filtered = query
		? visible.filter((item) =>
				[
					item.description,
					item.expected_behavior ?? "",
					item.source_kind,
					item.source_run_id ?? "",
					item.external_ref ?? "",
				]
					.join(" ")
					.toLowerCase()
					.includes(query),
			)
		: visible;
	return (
		<section aria-label="Findings" className="space-y-4">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<h3 className="text-lg font-semibold">Findings</h3>
				<Button
					variant="outline"
					className="min-h-11 text-xs"
					onClick={() => setOpen((value) => !value)}
				>
					{open ? "Close" : "Record finding"}
				</Button>
			</div>
			<ListToolbar>
				<SearchBox
					aria-label="Search findings"
					placeholder="Search findings"
					value={search}
					onChange={setSearch}
					className="min-w-0 flex-1 sm:max-w-xs"
				/>
				<div className="flex flex-wrap items-center gap-2">
					<Button
						variant={
							statusFilter === "open" ? "secondary" : "ghost"
						}
						className="min-h-11 text-xs"
						onClick={() => setStatusFilter("open")}
					>
						Open
					</Button>
					<Button
						variant={
							statusFilter === "dismissed"
								? "secondary"
								: "ghost"
						}
						className="min-h-11 text-xs"
						onClick={() => setStatusFilter("dismissed")}
					>
						Dismissed
					</Button>
				</div>
			</ListToolbar>
			{open && (
				<form
					aria-label="Record finding"
					className="space-y-3 rounded-lg border p-4"
					onSubmit={(event) => {
						event.preventDefault();
						if (description.trim()) create.mutate();
					}}
				>
					<div>
						<Label htmlFor="finding-description">
							Observed problem
						</Label>
						<Textarea
							id="finding-description"
							rows={3}
							required
							maxLength={4000}
							value={description}
							onChange={(event) =>
								setDescription(event.target.value)
							}
							placeholder="What did the Agent do wrong?"
						/>
					</div>
					<div>
						<Label htmlFor="finding-expected">
							Expected behavior
						</Label>
						<Textarea
							id="finding-expected"
							rows={2}
							maxLength={4000}
							value={expected}
							onChange={(event) =>
								setExpected(event.target.value)
							}
							placeholder="What should it have done instead?"
						/>
					</div>
					<div className="grid gap-3 sm:grid-cols-3">
						<div>
							<Label htmlFor="finding-source">Source</Label>
							<select
								id="finding-source"
								className="h-10 w-full rounded-md border bg-background px-3 text-sm"
								value={sourceKind}
								onChange={(event) =>
									setSourceKind(
										event.target.value as typeof sourceKind,
									)
								}
							>
								<option value="manual">Manual note</option>
								<option value="run">Agent run</option>
								<option value="external">
									External reference
								</option>
							</select>
						</div>
						{sourceKind === "run" && (
							<>
								<div>
									<Label htmlFor="finding-run">
										Run ID
									</Label>
									<Input
										id="finding-run"
										required
										value={sourceRunId}
										onChange={(event) =>
											setSourceRunId(event.target.value)
										}
										placeholder="Run UUID"
									/>
								</div>
								<div>
									<Label htmlFor="finding-sequence">
										Journal sequence (optional)
									</Label>
									<Input
										id="finding-sequence"
										type="number"
										min={0}
										value={sourceSequence}
										onChange={(event) =>
											setSourceSequence(
												event.target.value,
											)
										}
									/>
								</div>
							</>
						)}
						{sourceKind === "external" && (
							<div className="sm:col-span-2">
								<Label htmlFor="finding-external">
									Reference (URI or ticket; never fetched)
								</Label>
								<Input
									id="finding-external"
									maxLength={500}
									value={externalRef}
									onChange={(event) =>
										setExternalRef(event.target.value)
									}
								/>
							</div>
						)}
					</div>
					<PlatformError error={create.error} />
					<Button disabled={create.isPending || !description.trim()}>
						{create.isPending ? "Recording…" : "Record finding"}
					</Button>
				</form>
			)}
			<PlatformError
				error={findings.error}
				retry={() => void findings.refetch()}
			/>
			{findings.isPending && <p role="status">Loading findings…</p>}
			{!findings.isPending && !filtered.length && (
				<p className="py-4 text-sm text-muted-foreground">
					{query
						? "No findings match your search."
						: statusFilter === "open"
							? "No open findings. Record one from a flagged run or a manual note."
							: "No dismissed findings."}
				</p>
			)}
			<ul className="divide-y rounded-lg border">
				{filtered.map((item) => (
					<li key={item.id} className="space-y-2 p-4">
						<p className="text-sm">{item.description}</p>
						{item.expected_behavior && (
							<p className="text-sm text-muted-foreground">
								Expected: {item.expected_behavior}
							</p>
						)}
						<div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
							<span>{item.source_kind}</span>
							{item.source_run_id && (
								<Link
									className="underline"
									to={`/agents/${agentId}/runs/${item.source_run_id}${
										item.source_sequence != null
											? `?tab=activity&sequence=${item.source_sequence}`
											: ""
									}`}
								>
									Run {item.source_run_id.slice(0, 8)}
									{item.source_sequence != null
										? ` · sequence ${item.source_sequence}`
										: ""}
								</Link>
							)}
							{item.external_ref && (
								<span className="break-all">
									{item.external_ref}
								</span>
							)}
							{item.linked_case_ids?.length ? (
								<span>
									{item.linked_case_ids.length} linked
									case(s)
								</span>
							) : null}
						</div>
						{(item.description || item.expected_behavior) && (
							<details>
								<summary className="cursor-pointer text-xs text-muted-foreground">
									Raw record
								</summary>
								<EvidenceJson
									label={`Finding ${item.id}`}
									value={item}
								/>
							</details>
						)}
						<div className="flex flex-wrap gap-2">
							{item.status === "open" && (
								<>
									<Button
										size="sm"
										variant="outline"
										onClick={() => onNewTest(item)}
									>
										New test from finding
									</Button>
									<Button
										size="sm"
										variant="ghost"
										disabled={dismiss.isPending}
										onClick={() =>
											dismiss.mutate(item.id)
										}
									>
										Dismiss without test
									</Button>
								</>
							)}
						</div>
						<PlatformError error={dismiss.error} />
					</li>
				))}
			</ul>
		</section>
	);
}
