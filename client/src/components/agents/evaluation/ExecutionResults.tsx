import { Link } from "react-router-dom";
import { EvidenceJson, PlatformStatus } from "./PlatformEvidence";
import type { components } from "@/lib/v1";

function record(value: unknown): Record<string, unknown> {
	return value && typeof value === "object" && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: {};
}
function metric(value: unknown, fraction = false): string {
	if (value == null) return "Not recorded";
	return fraction && typeof value === "number"
		? `${(value * 100).toFixed(2)}%`
		: String(value);
}
/** Short human-readable rendering of an assertion value (never invented). */
function formatOutcomeValue(value: unknown): string {
	if (value == null) return "Not recorded";
	if (typeof value === "string") return value || "Empty";
	try {
		return JSON.stringify(value);
	} catch {
		return "Unreadable value";
	}
}
const metrics = [
	["cost_usd", "Cost (USD)"],
	["latency_ms", "Latency (ms)"],
	["input_tokens", "Input tokens"],
	["output_tokens", "Output tokens"],
	["cache_read_tokens", "Cache-read tokens"],
	["cache_write_tokens", "Cache-write tokens"],
	["cache_hit_fraction", "Cache-hit fraction"],
	["model_calls", "Model calls"],
	["iterations", "Iterations"],
];
export function ExecutionResults({
	results,
	cases,
	agentId,
}: {
	results: components["schemas"]["EvaluationResultPublic"][];
	cases: components["schemas"]["EvaluationCasePublic"][];
	agentId: string;
}) {
	if (!results.length)
		return (
			<p className="py-6 text-sm text-muted-foreground">
				Results will appear as queued cases complete.
			</p>
		);
	return (
		<div className="divide-y">
			{results.map((result) => {
				const comparison = result.comparison ?? {};
				const usage = record(comparison.usage);
				const baseline = record(usage.baseline);
				const candidate = record(usage.candidate);
				const comparisonLists: Array<{ key: string; count: number }> = [];
				for (const key of [
					"regressions",
					"improvements",
					"unchanged_failures",
				]) {
					const value = comparison[key];
					if (Array.isArray(value))
						comparisonLists.push({ key, count: value.length });
				}
				const toolDifferences = Array.isArray(
					comparison.tool_trajectory_differences,
				)
					? comparison.tool_trajectory_differences
					: null;
				const outputDifferences = comparison.output_differences ?? null;
				const hasDetails =
					comparisonLists.length > 0 ||
					Object.keys(usage).length > 0 ||
					toolDifferences !== null ||
					outputDifferences !== null ||
					result.simulator_state_hash != null;
				const orderedAssertions = [
					...(result.assertion_results ?? []),
				].sort((left, right) => {
					const rank = (passed: unknown) =>
						passed === false ? 0 : passed === true ? 2 : 1;
					return rank(left.passed) - rank(right.passed);
				});
				return (
					<article
						key={result.id}
						className="min-w-0 space-y-3 py-4 first:pt-0 last:pb-0"
					>
						<header className="flex flex-wrap items-center justify-between gap-2">
							<h3 className="font-semibold">
								{cases.find(
									(item) => item.id === result.case_id,
								)?.name ?? "Case"}{" "}
								<span className="font-normal text-muted-foreground">
									· v{result.case_version} · repetition{" "}
									{result.repetition_index + 1}
								</span>
							</h3>
							<PlatformStatus status={result.status} />
						</header>
						{result.error && (
							<p
								role="alert"
								className="mt-3 text-sm text-destructive"
							>
								{result.error}
							</p>
						)}
						{comparisonLists.length > 0 && (
							<div className="my-4 flex flex-wrap gap-x-6 gap-y-2 text-sm">
								{comparisonLists.map(({ key, count }) => (
									<div key={key}>
										<span className="font-medium">
											{count}
										</span>{" "}
										{key.replaceAll("_", " ")}
									</div>
								))}
							</div>
						)}

						<section className="mt-5">
							<h4 className="mb-2 text-sm font-semibold">
								Expected behavior
							</h4>
							<ul className="divide-y">
								{orderedAssertions.map((assertion, index) => (
									<li key={index} className="py-3">
										<div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
											<PlatformStatus
												status={
													assertion.passed === true
														? "passed"
														: assertion.passed ===
															  false
															? "failed"
															: "pending"
												}
											/>
											<span className="font-medium">
												{String(
													assertion.label ??
														assertion.code ??
														"Assertion",
												)}
											</span>
											<span className="text-muted-foreground">
												{String(
													assertion.side ??
														"baseline",
												) === "baseline"
													? "Live agent"
													: "Proposed changes"}
											</span>
										</div>
										{assertion.passed === false && (
											<dl className="mt-2 space-y-1 text-sm">
												<div className="flex min-w-0 flex-wrap gap-x-2">
													<dt className="text-muted-foreground">
														Expected:
													</dt>
													<dd className="min-w-0 break-words">
														{formatOutcomeValue(
															assertion.expected,
														)}
													</dd>
												</div>
												<div className="flex min-w-0 flex-wrap gap-x-2">
													<dt className="text-muted-foreground">
														Actual:
													</dt>
													<dd className="min-w-0 break-words">
														{formatOutcomeValue(
															assertion.actual ??
																assertion.detail ??
																assertion.rationale,
														)}
													</dd>
												</div>
											</dl>
										)}
										<details className="mt-2 text-sm">
											<summary className="cursor-pointer">
												Evidence links and raw values
											</summary>
												<EvidenceJson
													label="Assertion values"
													value={{
														expected:
															assertion.expected,
														actual: assertion.actual,
														rationale:
															assertion.rationale,
													}}
												/>
												<div className="mt-2 flex flex-wrap gap-3">
													{Array.isArray(
														assertion.evidence_references,
													) &&
														assertion.evidence_references.map(
															(item, index) => {
																const ref =
																	record(
																		item,
																	);
																return typeof ref.run_id ===
																	"string" &&
																	typeof ref.sequence ===
																		"number" ? (
																	<Link
																		className="underline"
																		key={
																			index
																		}
																		to={`/agents/${agentId}/runs/${ref.run_id}?tab=activity&sequence=${ref.sequence}`}
																	>
																		Sequence{" "}
																		{
																			ref.sequence
																		}{" "}
																		·{" "}
																		{ref.run_id.slice(
																			0,
																			8,
																		)}
																	</Link>
																) : null;
															},
														)}
												</div>
											</details>
										</li>
									))}
							</ul>
						</section>
						{hasDetails && (
							<details className="my-4">
								<summary className="cursor-pointer text-sm font-medium">
									Run details
								</summary>
								<div className="mt-3 space-y-5">
									{Object.keys(usage).length > 0 && (
										<section aria-label="Usage">
											<h5 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
												Usage
											</h5>
							<div className="max-w-full overflow-auto">
								<table className="w-full text-sm">
									<caption className="mb-2 text-left text-xs text-muted-foreground">
										Measured runtime usage. Automatic
										summaries excluded. Cache-hit fraction
										is cached input ÷ total input.
									</caption>
									<thead>
										<tr className="border-b text-left">
											<th className="py-2 font-medium">
												Measure
											</th>
											<th className="p-2 font-medium">
												Live agent
											</th>
											{result.candidate_run_id && (
												<>
													<th className="p-2 font-medium">
														Candidate
													</th>
													<th className="p-2 font-medium">
														Change
													</th>
												</>
											)}
										</tr>
									</thead>
								<tbody>
									{metrics.map(([key, label]) => (
										<tr
											key={key}
											className="border-b last:border-0"
										>
											<th className="py-2 text-left font-normal text-muted-foreground">
												{label}
											</th>
											<td className="p-2 tabular-nums">
												{metric(
													baseline[key],
													key ===
														"cache_hit_fraction",
												)}
											</td>
											{result.candidate_run_id && (
												<>
													<td className="p-2 tabular-nums">
														{metric(
															candidate[key],
															key ===
																"cache_hit_fraction",
														)}
													</td>
													<td className="p-2 tabular-nums">
														{baseline[key] !=
															null &&
														candidate[key] !=
															null &&
														Number.isFinite(
															Number(
																baseline[key],
															),
														) &&
														Number.isFinite(
															Number(
																candidate[key],
															),
														)
															? key ===
																"cache_hit_fraction"
																? `${((Number(candidate[key]) - Number(baseline[key])) * 100).toFixed(2)} pp`
																: (
																		Number(
																			candidate[
																				key
																			],
																		) -
																		Number(
																			baseline[
																				key
																			],
																		)
																	).toLocaleString(
																		undefined,
																		{
																			maximumFractionDigits: 6,
																		},
																	)
															: "Not recorded"}
													</td>
												</>
											)}
										</tr>
									))}
								</tbody>
							</table>
							</div>
										</section>
									)}
									{toolDifferences !== null && (
										<section aria-label="Tool-call changes">
											<h5 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
												Tool-call changes
											</h5>
											<EvidenceJson
												label="Tool-call changes"
												value={toolDifferences}
											/>
										</section>
									)}
									{(outputDifferences !== null ||
										comparisonLists.length > 0) && (
										<section aria-label="Output comparison">
											<h5 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
												Output comparison
											</h5>
											<EvidenceJson
												label="Comparison evidence"
												value={{
													output_differences:
														outputDifferences,
													regressions:
														comparison.regressions,
													improvements:
														comparison.improvements,
													unchanged_failures:
														comparison.unchanged_failures,
												}}
											/>
										</section>
									)}
									<section aria-label="Raw evidence">
										<h5 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
											Raw evidence
										</h5>
										<p className="break-all text-xs text-muted-foreground">
											Fixture state:{" "}
											{result.simulator_state_hash ??
												"Not recorded"}
										</p>
									</section>
								</div>
							</details>
						)}
						<footer className="mt-4 flex flex-wrap gap-4 border-t pt-4 text-sm">
							{result.baseline_run_id && (
								<Link
									className="underline"
									to={`/agents/${agentId}/runs/${result.baseline_run_id}`}
								>
									Inspect live-agent run
								</Link>
							)}
							{result.candidate_run_id && (
								<Link
									className="underline"
									to={`/agents/${agentId}/runs/${result.candidate_run_id}`}
								>
									Inspect candidate run
								</Link>
							)}
						</footer>
					</article>
				);
			})}
		</div>
	);
}
