import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Combobox } from "@/components/ui/combobox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { modelFactsLine } from "@/lib/model-info";
import { listProviderModels } from "@/services/aiModels";

interface ProviderModelFieldProps {
	id: string;
	disabled?: boolean;
	connectionId: string;
	value: string;
	onValueChange: (value: string) => void;
}

export function useProviderModels(connectionId: string) {
	return useQuery({
		queryKey: ["ai", "provider-models", connectionId],
		queryFn: () => listProviderModels(connectionId),
		enabled: Boolean(connectionId),
	});
}

export function ProviderModelField({
	id,
	disabled = false,
	connectionId,
	value,
	onValueChange,
}: ProviderModelFieldProps) {
	const modelsQuery = useProviderModels(connectionId);
	const models = modelsQuery.data?.models ?? [];
	const [manualEntry, setManualEntry] = useState(false);
	const options = models.map((model) => ({
		value: model.id,
		label: model.display_name,
		description: modelFactsLine(model),
	}));
	if (value && !options.some((option) => option.value === value)) {
		options.unshift({ value, label: value, description: undefined });
	}
	const listed = !connectionId || modelsQuery.isLoading || models.length > 0;
	const fromCatalog = modelsQuery.data?.source === "catalog";

	return (
		<div className="min-w-0 space-y-2">
			<Label htmlFor={id}>Model</Label>
			{modelsQuery.isError && <div role="alert" className="space-y-2 rounded-[var(--bf-radius-control)] bg-[var(--bf-warning-soft)] p-3 text-sm">
				<p>Could not load the model list. Your selected model is preserved.</p>
				<Button type="button" variant="outline" className="min-h-11" disabled={disabled || modelsQuery.isFetching} onClick={() => void modelsQuery.refetch()}>{modelsQuery.isFetching ? "Retrying…" : "Retry model list"}</Button>
			</div>}
			{listed && !manualEntry ? (
				<>
					<Combobox
						id={id}
						value={value}
						onValueChange={onValueChange}
						options={options}
						placeholder={
							connectionId
								? "Select a model"
								: "Select a provider first"
						}
						searchPlaceholder="Search models..."
						emptyText="No matching models."
						disabled={disabled || !connectionId}
						isLoading={modelsQuery.isLoading}
					/>
					{models.length > 0 && (
						<p className="text-xs text-muted-foreground">
							{fromCatalog
								? "From the models.dev catalog, which may list models your account cannot use."
								: "Reported by this endpoint, which may list models your account cannot use."}{" "}
							<Button
								type="button"
								variant="link"
								className="h-auto p-0 text-xs"
								disabled={disabled}
								onClick={() => setManualEntry(true)}
							>
								Enter a model ID instead
							</Button>
						</p>
					)}
				</>
			) : (
				<>
					<Input
						id={id}
						className="min-h-11"
						disabled={disabled || !connectionId}
						value={value}
						onChange={(event) => onValueChange(event.target.value)}
						placeholder="Enter a model ID"
					/>
					<p className="text-xs text-muted-foreground">
						{manualEntry ? (
							<Button
								type="button"
								variant="link"
								className="h-auto p-0 text-xs"
								disabled={disabled}
								onClick={() => setManualEntry(false)}
							>
								Choose from the model list
							</Button>
						) : modelsQuery.isError ? (
							"You can enter the model ID manually while the list is unavailable."
						) : (
							"This endpoint did not return a model list. Enter the model ID manually."
						)}
					</p>
				</>
			)}
		</div>
	);
}
