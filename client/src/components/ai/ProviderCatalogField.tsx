import { useQuery } from "@tanstack/react-query";

import { Combobox } from "@/components/ui/combobox";
import { Label } from "@/components/ui/label";
import {
	getModelCatalog,
	type ModelCatalogProvider,
} from "@/services/aiModels";

/** Combobox value for a connection that is not in the catalog. */
export const CUSTOM_PROVIDER = "custom";

export function useModelCatalog() {
	return useQuery({
		queryKey: ["ai", "model-catalog"],
		queryFn: getModelCatalog,
		staleTime: 5 * 60 * 1000,
	});
}

export function ProviderCatalogField({
	id,
	value,
	onSelect,
	disabled = false,
}: {
	id: string;
	/** A catalog provider id, or CUSTOM_PROVIDER. */
	value: string;
	/** The chosen catalog provider, or null for a custom endpoint. */
	onSelect: (provider: ModelCatalogProvider | null) => void;
	disabled?: boolean;
}) {
	const catalogQuery = useModelCatalog();
	const providers = catalogQuery.data?.providers ?? [];
	const options = [
		...providers.map((provider) => ({
			value: provider.id,
			label: provider.name,
			description: [
				provider.native ? null : "Community",
				provider.endpoint,
			]
				.filter(Boolean)
				.join(" · "),
		})),
		{
			value: CUSTOM_PROVIDER,
			label: "Custom endpoint",
			description: "Any endpoint not in the catalog",
		},
	];

	return (
		<div className="min-w-0 space-y-2">
			<Label htmlFor={id}>Provider</Label>
			<Combobox
				id={id}
				value={value}
				onValueChange={(next) =>
					onSelect(providers.find((provider) => provider.id === next) ?? null)
				}
				options={options}
				placeholder="Select a provider"
				searchPlaceholder="Search providers..."
				emptyText="No matching provider. Use a custom endpoint."
				disabled={disabled}
				isLoading={catalogQuery.isLoading}
			/>
			{catalogQuery.isError && (
				<p role="alert" className="text-xs text-destructive">
					Could not load the provider catalog. You can still add a
					custom endpoint.
				</p>
			)}
		</div>
	);
}

/** Help text for a catalog provider: community status and key hint. */
export function catalogProviderHint(
	provider: ModelCatalogProvider | undefined,
): string | null {
	if (!provider) return null;
	const community = provider.native
		? null
		: "Community provider: it uses the same adapter as a native one and is not tested by Bifrost directly.";
	const keyHint = provider.env?.length
		? `Its key is usually named ${provider.env[0]}.`
		: null;
	return [community, keyHint].filter(Boolean).join(" ") || null;
}
