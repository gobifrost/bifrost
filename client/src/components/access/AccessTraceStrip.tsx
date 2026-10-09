import { motion, useReducedMotion, type Variants } from "framer-motion";
import {
	CircleCheck,
	CircleDashed,
	CircleMinus,
	ShieldAlert,
	ShieldCheck,
	TriangleAlert,
	type LucideIcon,
} from "lucide-react";

import { stepSentence, stepTitle, type TraceNames } from "@/lib/access-trace";
import { motionSeconds } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { AccessStep, AccessTrace } from "@/services/access";

const STATUS: Record<
	AccessStep["status"],
	{ label: string; icon: LucideIcon; tone: string; badge: string }
> = {
	passed: {
		label: "Passed",
		icon: CircleCheck,
		tone: "bg-[var(--bf-success-soft)] text-[var(--bf-success)]",
		badge: "text-[var(--bf-success)]",
	},
	stopped: {
		label: "Would Stop Here",
		icon: TriangleAlert,
		tone: "bg-[var(--bf-warning-soft)] text-[var(--bf-warning)] ring-1 ring-[var(--bf-warning)]/40",
		badge: "text-[var(--bf-warning)]",
	},
	not_applicable: {
		label: "Not Applicable",
		icon: CircleMinus,
		tone: "bg-muted text-muted-foreground",
		badge: "text-muted-foreground",
	},
	not_reached: {
		label: "Not Reached",
		icon: CircleDashed,
		tone: "bg-muted/60 text-muted-foreground",
		badge: "text-muted-foreground",
	},
};

function runUserId(trace: AccessTrace): string | undefined {
	const id = trace.steps.find((step) => step.key === "run_user")?.facts
		.user_id;
	return typeof id === "string" ? id : undefined;
}

/**
 * An access trace, step by step: what each check decided and why, the step
 * that would stop it in amber, and the report-only outcome. Steps in
 * `changed` (keys) are ringed and marked Changed. Steps reveal in sequence
 * on the feedback token's stagger; reduced motion shows them at once.
 */
export function AccessTraceStrip({
	trace,
	names,
	label = "Access Trace",
	changed,
}: {
	trace: AccessTrace;
	names: TraceNames;
	label?: string;
	changed?: ReadonlySet<string>;
}) {
	const reduceMotion = useReducedMotion();
	const stagger = motionSeconds("--bf-motion-feedback");
	const list: Variants = {
		shown: { transition: { staggerChildren: stagger } },
	};
	const item: Variants = {
		hidden: { opacity: 0, x: -6 },
		shown: { opacity: 1, x: 0, transition: { duration: stagger } },
	};
	const user = runUserId(trace);
	const allowed = trace.outcome === "success";
	const Outcome = allowed ? ShieldCheck : ShieldAlert;

	return (
		<div className="space-y-4">
			<motion.ol
				aria-label={label}
				className="space-y-1"
				initial={reduceMotion ? false : "hidden"}
				animate="shown"
				variants={list}
			>
				{trace.steps.map((step, index) => {
					const status = STATUS[step.status];
					const Icon = status.icon;
					const sentence = stepSentence(step, names, user);
					const last = index === trace.steps.length - 1;
					const isChanged = changed?.has(step.key) ?? false;
					return (
						<motion.li
							key={step.key}
							variants={item}
							className={cn(
								"relative grid grid-cols-[2rem_minmax(0,1fr)] gap-x-3 rounded-[var(--bf-radius-surface)] p-2",
								step.status === "stopped" &&
									"bg-[var(--bf-warning-soft)]/60",
								step.status === "not_reached" &&
									"text-muted-foreground",
								isChanged &&
									"ring-2 ring-inset ring-[var(--bf-info)]",
							)}
						>
							{!last && (
								<span
									aria-hidden="true"
									className="absolute bottom-[-0.25rem] left-[1.5rem] top-10 w-px bg-border"
								/>
							)}
							<span
								aria-hidden="true"
								className={cn(
									"flex size-8 items-center justify-center rounded-full",
									status.tone,
								)}
							>
								<Icon className="size-4" />
							</span>
							<div className="min-w-0 space-y-0.5 pt-1">
								<div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
									<h3 className="text-sm font-medium">
										{stepTitle(step.label)}
									</h3>
									<span className="flex items-baseline gap-2 text-xs font-medium">
										{isChanged && (
											<span className="text-[var(--bf-info)]">
												Changed
											</span>
										)}
										<span
											data-testid="step-status"
											className={status.badge}
										>
											{status.label}
										</span>
									</span>
								</div>
								{sentence && (
									<p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
										{sentence}
									</p>
								)}
							</div>
						</motion.li>
					);
				})}
			</motion.ol>
			<motion.p
				role="status"
				className={cn(
					"flex items-center gap-2 text-sm font-medium",
					allowed
						? "text-[var(--bf-success)]"
						: "text-[var(--bf-warning)]",
				)}
				initial={reduceMotion ? false : { opacity: 0 }}
				animate={{ opacity: 1 }}
				transition={{
					delay: stagger * trace.steps.length,
					duration: stagger,
				}}
			>
				<Outcome aria-hidden="true" className="size-4 shrink-0" />
				{allowed
					? "Would Be Allowed"
					: "Would Be Blocked — Not Enforced Yet"}
			</motion.p>
		</div>
	);
}
