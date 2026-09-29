import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { reasoningLabel } from "@/lib/model-info";

import { useProviderModels } from "./ProviderModelField";

const MODEL_DEFAULT = "__model_default__";

/** The reasoning choices the catalog lists for a connection's model. */
export function useReasoningChoices(connectionId: string, model: string): string[] {
	const modelsQuery = useProviderModels(connectionId);
	return (
		modelsQuery.data?.models.find((entry) => entry.id === model.trim())
			?.reasoning_choices ?? []
	);
}

/**
 * Reasoning control for a profile. Rendered only when the model's catalog
 * entry lists choices, and offers exactly those, so a saved choice is one
 * the model is documented to accept.
 */
export function ReasoningEffortField({
	id,
	connectionId,
	model,
	value,
	onValueChange,
	disabled = false,
}: {
	id: string;
	connectionId: string;
	model: string;
	value: string | null;
	onValueChange: (value: string | null) => void;
	disabled?: boolean;
}) {
	const choices = useReasoningChoices(connectionId, model);
	if (choices.length === 0) return null;

	return (
		<div className="min-w-0 space-y-2">
			<Label htmlFor={id}>Reasoning</Label>
			<Select
				disabled={disabled}
				value={value ?? MODEL_DEFAULT}
				onValueChange={(next) => onValueChange(next === MODEL_DEFAULT ? null : next)}
			>
				<SelectTrigger id={id} className="min-h-11 w-full">
					<SelectValue />
				</SelectTrigger>
				<SelectContent>
					<SelectItem value={MODEL_DEFAULT}>Model default</SelectItem>
					{choices.map((choice) => (
						<SelectItem key={choice} value={choice}>
							{reasoningLabel(choice)}
						</SelectItem>
					))}
				</SelectContent>
			</Select>
			<p className="text-xs text-muted-foreground">
				Options come from this model&apos;s catalog entry. Higher effort
				spends more output tokens, which cost the most. Each run shows
				the reasoning tokens the model reported.
			</p>
		</div>
	);
}
