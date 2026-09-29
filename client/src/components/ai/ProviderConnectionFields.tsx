import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import type { AIProviderKind } from "@/services/aiModels";

import {
	CUSTOM_PROVIDER,
	ProviderCatalogField,
	catalogProviderHint,
	useModelCatalog,
} from "./ProviderCatalogField";
import { PROVIDERS } from "./providerOptions";

export interface ProviderConnectionDraft {
	/** A catalog provider id, or CUSTOM_PROVIDER. */
	catalogProviderId: string;
	provider: AIProviderKind;
	endpoint: string;
}

function normalized(endpoint: string | null | undefined): string {
	return (endpoint ?? "").trim().replace(/\/+$/, "");
}

/** Request fields for a draft; custom endpoints carry no catalog link. */
export function connectionRequestFields(draft: ProviderConnectionDraft) {
	return {
		provider: draft.provider,
		endpoint: draft.endpoint.trim() || null,
		catalog_provider_id:
			draft.catalogProviderId === CUSTOM_PROVIDER
				? null
				: draft.catalogProviderId,
	};
}

export function ProviderConnectionFields({
	idPrefix,
	draft,
	onChange,
	disabled = false,
}: {
	idPrefix: string;
	draft: ProviderConnectionDraft;
	onChange: (draft: ProviderConnectionDraft) => void;
	disabled?: boolean;
}) {
	const catalogQuery = useModelCatalog();
	const selected = catalogQuery.data?.providers.find(
		(provider) => provider.id === draft.catalogProviderId,
	);
	const custom = draft.catalogProviderId === CUSTOM_PROVIDER;
	const endpointChanged =
		!custom &&
		selected !== undefined &&
		normalized(draft.endpoint) !== normalized(selected.endpoint);
	const hint = catalogProviderHint(selected);

	return (
		<>
			<ProviderCatalogField
				id={`${idPrefix}-catalog-provider`}
				value={draft.catalogProviderId}
				disabled={disabled}
				onSelect={(provider) =>
					onChange(
						provider
							? {
									catalogProviderId: provider.id,
									provider: provider.adapter,
									endpoint: provider.endpoint ?? "",
								}
							: {
									catalogProviderId: CUSTOM_PROVIDER,
									provider: "openai_compatible",
									endpoint: "",
								},
					)
				}
			/>
			{hint && <p className="-mt-2 text-xs text-muted-foreground">{hint}</p>}
			{custom && (
				<div className="space-y-2">
					<Label htmlFor={`${idPrefix}-api-format`}>API format</Label>
					<Select
						disabled={disabled}
						value={draft.provider}
						onValueChange={(provider) =>
							onChange({ ...draft, provider: provider as AIProviderKind })
						}
					>
						<SelectTrigger
							id={`${idPrefix}-api-format`}
							className="h-auto min-h-11 w-full data-[size=default]:h-auto"
						>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							{PROVIDERS.map((provider) => (
								<SelectItem key={provider.value} value={provider.value}>
									{provider.label}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
				</div>
			)}
			<div className="space-y-2">
				<Label htmlFor={`${idPrefix}-endpoint`}>Endpoint</Label>
				<Input
					id={`${idPrefix}-endpoint`}
					value={draft.endpoint}
					onChange={(event) =>
						onChange({ ...draft, endpoint: event.target.value })
					}
					placeholder="https://api.example.com/v1"
				/>
				<p className="text-xs text-muted-foreground">
					{custom
						? "Models are listed by this endpoint, or entered by ID."
						: endpointChanged
							? "A different endpoint saves this as a custom connection: models are listed by the endpoint instead of the catalog."
							: "The provider's standard endpoint from the catalog."}
				</p>
			</div>
		</>
	);
}
