import type { AIProviderKind } from "@/services/aiModels";

/** Bifrost adapters, offered as the API format of a custom endpoint. */
export const PROVIDERS: {
	value: AIProviderKind;
	label: string;
}[] = [
	{ value: "openai_compatible", label: "OpenAI-Compatible" },
	{ value: "openai", label: "OpenAI" },
	{ value: "anthropic", label: "Anthropic" },
	{ value: "google", label: "Google" },
	{ value: "openrouter", label: "OpenRouter" },
	{ value: "opencode_go", label: "OpenCode Go" },
];

export function providerLabel(provider: AIProviderKind): string {
	return (
		PROVIDERS.find((option) => option.value === provider)?.label ?? provider
	);
}
