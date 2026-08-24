import { useQuery } from "@tanstack/react-query";

import { Combobox } from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { listProviderModels } from "@/services/aiModels";

interface ProviderModelFieldProps {
	id: string;
	connectionId: string;
	value: string;
	onValueChange: (value: string) => void;
}

export function ProviderModelField({
	id,
	connectionId,
	value,
	onValueChange,
}: ProviderModelFieldProps) {
	const modelsQuery = useQuery({
		queryKey: ["ai", "provider-models", connectionId],
		queryFn: () => listProviderModels(connectionId),
		enabled: Boolean(connectionId),
	});
	const options = (modelsQuery.data?.models ?? []).map((model) => ({
		value: model.id,
		label: model.display_name,
		description: model.id !== model.display_name ? model.id : undefined,
	}));
	if (value && !options.some((option) => option.value === value)) {
		options.unshift({ value, label: value, description: undefined });
	}

	return (
		<div className="space-y-2">
			<Label htmlFor={id}>Model</Label>
			{!modelsQuery.isError &&
			(!connectionId || modelsQuery.isLoading || options.length > 0) ? (
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
					emptyText="No models reported by this provider."
					disabled={!connectionId}
					isLoading={modelsQuery.isLoading}
				/>
			) : (
				<>
					<Input
						id={id}
						value={value}
						onChange={(event) => onValueChange(event.target.value)}
						placeholder="Enter a model ID"
					/>
					<p className="text-xs text-muted-foreground">
						This provider did not return a model catalog. Enter the
						model ID manually.
					</p>
				</>
			)}
		</div>
	);
}
