/**
 * SolutionSetupWizard
 *
 * A guided, stepped variant of {@link SolutionSetupChecklist}. It walks an admin
 * through unmet setup requirements one step at a time:
 *
 *   Step 1 — Configuration: set values for declared config keys.
 *   Step 2 — Connections:   wire up the declared integrations (with a warn-only
 *                           OAuth nudge — never a blocker).
 *
 * Like the checklist, this is a side-effect-free presentational component: the
 * parent owns data-fetching and the real config-set mutation. Steps are derived
 * from the items, so the wizard collapses to a single step when one category is
 * absent.
 */

import { useState } from "react";
import { CheckCircle2, Circle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { SolutionSetupItem } from "@/services/solutions";
import {
	ConfigItem,
	ConnectionItem,
	defaultIntegrationHref,
} from "./SolutionSetupChecklist";

export interface SolutionSetupWizardProps {
	items: SolutionSetupItem[];
	setupComplete: boolean;
	/** Called when the user submits a value for a config key. */
	onSetConfig: (key: string, value: string) => void | Promise<void>;
	/** Supplies the href for a connection's "Set up integration" link. */
	integrationHref?: (name: string) => string;
	/** Invoked when the user clicks Finish/Done on the last step. */
	onFinish?: () => void;
}

interface WizardStep {
	id: "config" | "connection";
	title: string;
	items: SolutionSetupItem[];
}

export function SolutionSetupWizard({
	items,
	setupComplete,
	onSetConfig,
	integrationHref = defaultIntegrationHref,
	onFinish,
}: SolutionSetupWizardProps) {
	const configItems = items.filter((i) => i.kind === "config");
	const connectionItems = items.filter((i) => i.kind === "connection");

	// Only include a step if it has items — a solution with no connections is a
	// single-step (config) wizard, and vice-versa.
	const steps: WizardStep[] = [];
	if (configItems.length > 0) {
		steps.push({ id: "config", title: "Configuration", items: configItems });
	}
	if (connectionItems.length > 0) {
		steps.push({
			id: "connection",
			title: "Connections",
			items: connectionItems,
		});
	}

	const [stepIndex, setStepIndex] = useState(0);

	if (steps.length === 0) {
		return (
			<div className="rounded-lg border py-12 text-center text-sm text-muted-foreground">
				This Solution declares no setup requirements.
			</div>
		);
	}

	const current = steps[Math.min(stepIndex, steps.length - 1)];
	const isFirst = stepIndex === 0;
	const isLast = stepIndex >= steps.length - 1;

	const configsSatisfied = configItems.every((i) => !i.required || i.is_set);

	return (
		<div className="space-y-4">
			{/* Progress header */}
			<div className="flex items-center justify-between gap-3">
				<div>
					<p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
						Step {stepIndex + 1} of {steps.length}
					</p>
					<h3 className="text-base font-semibold">{current.title}</h3>
				</div>
				{steps.length > 1 && (
					<ol className="flex items-center gap-2">
						{steps.map((step, i) => (
							<li
								key={step.id}
								className={
									"flex items-center gap-1.5 text-xs " +
									(i === stepIndex
										? "font-medium text-foreground"
										: "text-muted-foreground")
								}
							>
								{i < stepIndex ? (
									<CheckCircle2 className="h-3.5 w-3.5 text-green-600 dark:text-green-500" />
								) : (
									<Circle className="h-3.5 w-3.5" />
								)}
								{step.title}
							</li>
						))}
					</ol>
				)}
			</div>

			{/* Show the completion banner on the final step so a connections-only
			    solution surfaces completion too (not just config-first wizards). */}
			{isLast && setupComplete && (
				<div className="flex items-center gap-2 rounded-lg border border-green-500/40 bg-green-500/5 px-4 py-3 text-sm text-green-700 dark:text-green-400">
					<CheckCircle2 className="h-4 w-4 shrink-0" />
					All required setup is complete — this Solution is ready to run.
				</div>
			)}

			{current.id === "config" && !setupComplete && !configsSatisfied && (
				<p className="text-xs text-muted-foreground">
					Set the required values below before this Solution can run.
				</p>
			)}

			{/* Step body */}
			<div className="space-y-3">
				{current.id === "config"
					? current.items.map((item) => (
							<ConfigItem
								key={item.key}
								item={item}
								onSet={onSetConfig}
							/>
						))
					: current.items.map((item) => (
							<ConnectionItem
								key={item.key}
								item={item}
								integrationHref={integrationHref}
							/>
						))}
			</div>

			{/* Navigation */}
			<div className="flex items-center justify-between gap-2 pt-1">
				<Button
					variant="outline"
					disabled={isFirst}
					onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
				>
					Back
				</Button>
				{isLast ? (
					// Finish is never gated by the OAuth warn-only nudge.
					<Button onClick={() => onFinish?.()}>Finish</Button>
				) : (
					<Button onClick={() => setStepIndex((i) => i + 1)}>Next</Button>
				)}
			</div>
		</div>
	);
}
