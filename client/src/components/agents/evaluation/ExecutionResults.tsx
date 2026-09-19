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
		<div className="space-y-6">
			{results.map((result) => {
				const comparison = result.comparison ?? {};
				const usage = record(comparison.usage);
				const baseline = record(usage.baseline);
				const candidate = record(usage.candidate);
				return (
					<article
						key={result.id}
						className="min-w-0 rounded-lg border p-4 sm:p-5"
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
						<div className="my-4 flex flex-wrap gap-x-6 gap-y-2 text-sm">
							{[
								"regressions",
								"improvements",
								"unchanged_failures",
							].map((key) => (
								<div key={key}>
									<span className="font-medium">
										{Array.isArray(comparison[key])
											? comparison[key].length
											: "—"}
									</span>{" "}
									{key.replaceAll("_", " ")}
								</div>
							))}
						</div>
						<div className="max-w-full overflow-auto">
							<table className="w-full text-sm">
								<caption className="mb-2 text-left text-xs text-muted-foreground">
									Measured runtime usage. Automatic summaries
									excluded. Cache-hit fraction is cached input
									÷ total input.
								</caption>
								<thead>
									<tr className="border-b text-left">
										<th className="py-2 font-medium">
											Measure
										</th>
										<th className="p-2 font-medium">
											Baseline
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
						<section className="mt-5">
							<h4 className="mb-2 text-sm font-semibold">
								Assertions
							</h4>
							<ul className="divide-y">
								{result.assertion_results?.map(
									(assertion, index) => (
										<li key={index} className="py-3">
											<div className="flex flex-wrap items-center gap-2 text-sm">
												<PlatformStatus
													status={
														assertion.passed ===
														true
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
													)}
												</span>
											</div>
											<details className="mt-2 text-sm">
												<summary className="cursor-pointer">
													Expected, actual and
													evidence
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
																		to={`/agents/${agentId}/runs/${ref.run_id}/debug?sequence=${ref.sequence}`}
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
									),
								)}
							</ul>
						</section>
						<details className="mt-4">
							<summary className="cursor-pointer text-sm font-medium">
								Tool-call changes
							</summary>
							<EvidenceJson
								label="Tool-call changes"
								value={
									comparison.tool_trajectory_differences ?? []
								}
							/>
						</details>
						<details className="mt-3">
							<summary className="cursor-pointer text-sm font-medium">
								Output differences and assertion deltas
							</summary>
							<EvidenceJson
								label="Comparison evidence"
								value={{
									output_differences:
										comparison.output_differences,
									regressions: comparison.regressions,
									improvements: comparison.improvements,
									unchanged_failures:
										comparison.unchanged_failures,
								}}
							/>
						</details>
						<footer className="mt-4 flex flex-wrap gap-4 border-t pt-4 text-sm">
							{result.baseline_run_id && (
								<Link
									className="underline"
									to={`/agents/${agentId}/runs/${result.baseline_run_id}/debug`}
								>
									Debug baseline run
								</Link>
							)}
							{result.candidate_run_id && (
								<Link
									className="underline"
									to={`/agents/${agentId}/runs/${result.candidate_run_id}/debug`}
								>
									Debug candidate run
								</Link>
							)}
							<span className="break-all text-xs text-muted-foreground">
								Fixture state:{" "}
								{result.simulator_state_hash ?? "Not recorded"}
							</span>
						</footer>
					</article>
				);
			})}
		</div>
	);
}
