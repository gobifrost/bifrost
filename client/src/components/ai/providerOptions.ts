import type { AIProviderKind } from "@/services/aiModels";

export const PROVIDERS: {
	value: AIProviderKind;
	label: string;
	endpoint: string;
}[] = [
	{
		value: "openai",
		label: "OpenAI",
		endpoint: "https://api.openai.com/v1",
	},
	{
		value: "openrouter",
		label: "OpenRouter",
		endpoint: "https://openrouter.ai/api/v1",
	},
	{
		value: "opencode_go",
		label: "OpenCode Go",
		endpoint: "https://opencode.ai/zen/go/v1",
	},
	{
		value: "google",
		label: "Google",
		endpoint: "https://generativelanguage.googleapis.com",
	},
	{
		value: "anthropic",
		label: "Anthropic",
		endpoint: "https://api.anthropic.com",
	},
	{
		value: "openai_compatible",
		label: "OpenAI-Compatible",
		endpoint: "",
	},
];

export function providerOption(provider: AIProviderKind) {
	return (
		PROVIDERS.find((option) => option.value === provider) ?? PROVIDERS[0]
	);
}

export function providerLabel(provider: AIProviderKind): string {
	return (
		PROVIDERS.find((option) => option.value === provider)?.label ?? provider
	);
}
