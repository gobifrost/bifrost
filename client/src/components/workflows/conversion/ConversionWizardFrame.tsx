import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export type ConversionStage =
	"scope" | "analyze" | "review" | "apply" | "cutover";

const STEPS: { id: ConversionStage; label: string }[] = [
	{ id: "scope", label: "Choose workflows" },
	{ id: "analyze", label: "Analyze" },
	{ id: "review", label: "Review" },
	{ id: "apply", label: "Apply" },
];

/** One task frame shared by every conversion entry point and saved job. */
export function ConversionWizardFrame({
	stage,
	platformIntent,
	children,
	footer,
	workspace = false,
}: {
	stage: ConversionStage;
	platformIntent: boolean;
	children: ReactNode;
	footer: ReactNode;
	workspace?: boolean;
}) {
	const steps = platformIntent
		? [...STEPS, { id: "cutover" as const, label: "Platform default" }]
		: STEPS;
	const current = steps.findIndex((step) => step.id === stage);
	return (
		<section
			aria-label="Workflow conversion wizard"
			className={cn(
				"flex max-h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-[var(--bf-radius-feature)] border border-border bg-card shadow-sm",
				workspace && "flex-1",
			)}
		>
			<nav
				aria-label="Conversion progress"
				className="shrink-0 border-b border-border bg-muted/25 px-5 py-4 sm:px-6"
			>
				<ol aria-label="Conversion steps" className="flex items-center">
					{steps.map((step, index) => (
						<li
							key={step.id}
							aria-current={
								index === current ? "step" : undefined
							}
							aria-label={`${step.label}, ${index < current ? "complete" : index === current ? "current" : "upcoming"}`}
							className={cn(
								"flex min-w-0 items-center",
								index > 0 && "flex-1",
							)}
						>
							{index > 0 && (
								<span
									aria-hidden="true"
									className={cn(
										"mx-2 h-px min-w-2 flex-1 sm:mx-4",
										index <= current
											? "bg-primary/50"
											: "bg-border",
									)}
								/>
							)}
							<span
								aria-hidden="true"
								className={cn(
									"flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-medium",
									index === current
										? "bg-primary text-primary-foreground ring-4 ring-primary/10"
										: index < current
											? "bg-primary/10 text-primary"
											: "border border-border bg-background text-muted-foreground",
								)}
							>
								{index < current ? (
									<Check className="size-4" />
								) : (
									index + 1
								)}
							</span>
							<span
								className={cn(
									"ml-2 hidden text-sm sm:inline",
									index === current
										? "font-medium text-foreground"
										: "text-muted-foreground",
								)}
							>
								{step.label}
							</span>
						</li>
					))}
				</ol>
				<p
					aria-hidden="true"
					className="mt-3 text-xs text-muted-foreground sm:hidden"
				>
					Step {current + 1} of {steps.length} ·{" "}
					{steps[current]?.label}
				</p>
			</nav>
			<div
				className={cn(
					"min-h-0 min-w-0 p-5 sm:p-6",
					workspace
						? "flex flex-1 flex-col overflow-hidden"
						: "overflow-auto",
				)}
			>
				{children}
			</div>
			{footer && (
				<footer className="z-10 flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border bg-card px-5 py-4 sm:px-6">
					{footer}
				</footer>
			)}
		</section>
	);
}
