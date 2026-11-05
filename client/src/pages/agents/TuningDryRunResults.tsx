import { useId } from "react";
import type { ConsolidatedDryRunResponse } from "@/services/agentTuning";
import { cn } from "@/lib/utils";

export function TuningDryRunResults({ results }: ConsolidatedDryRunResponse) {
	const headingId = useId();
	const changed = results.filter(
		(result) =>
			!result.evaluation_failed && !result.would_still_decide_same,
	).length;
	const failed = results.filter((result) => result.evaluation_failed).length;
	const evaluated = results.length - failed;
	return (
		<section
			aria-labelledby={headingId}
			data-testid="dryrun-results"
			className="min-w-0 rounded-[var(--bf-radius-surface)] border bg-card p-4"
		>
			<div className="flex flex-wrap items-baseline justify-between gap-2">
				<h2 id={headingId} className="text-base font-semibold">
					Dry-run results
				</h2>
				<p className="text-sm text-muted-foreground">
					{changed} of {evaluated} evaluated runs would change
					behavior
					{failed > 0 &&
						` · ${failed} evaluation${failed === 1 ? "" : "s"} failed`}
				</p>
			</div>
			{results.length === 0 ? (
				<p className="mt-3 text-sm text-muted-foreground">
					No runs were evaluated.
				</p>
			) : (
				<ul className="mt-4 grid min-w-0 gap-3 lg:grid-cols-2">
					{results.map((result) => (
						<li
							key={result.run_id}
							className="min-w-0 rounded-[var(--bf-radius-control)] border bg-background p-4 [overflow-wrap:anywhere]"
						>
							<div className="flex flex-wrap items-center justify-between gap-2">
								<span className="font-mono text-xs text-muted-foreground">
									Run {result.run_id}
								</span>
								<span
									className={cn(
										"rounded-full px-2 py-1 text-xs font-medium",
										result.evaluation_failed
											? "bg-muted text-muted-foreground"
											: result.would_still_decide_same
												? "bg-[var(--bf-warning-soft)] text-[var(--bf-warning)]"
												: "bg-[var(--bf-success-soft)] text-[var(--bf-success)]",
									)}
								>
									{result.evaluation_failed
										? "Evaluation Failed"
										: result.would_still_decide_same
											? "Same decision"
											: "Would change"}
								</span>
							</div>
							<p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed">
								{result.reasoning}
							</p>
							{!result.evaluation_failed && (
								<p className="mt-3 text-xs text-muted-foreground">
									Confidence:{" "}
									{Math.round(result.confidence * 100)}%
								</p>
							)}
						</li>
					))}
				</ul>
			)}
		</section>
	);
}
