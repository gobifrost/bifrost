import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "@/test-utils";
import { LLMConfig } from "./LLMConfig";

const saveConfig = vi.fn();
const refetchConfig = vi.fn();
const savedConfig = {
	provider: "openai",
	model: "gpt-4o",
	api_key_set: true,
	is_configured: true,
	endpoint: null,
	max_tokens: 4096,
	default_system_prompt: null,
	summarization_model: null,
	tuning_model: null,
	builder_model: "builder-old",
};

vi.mock("@/lib/api-client", () => ({
	authFetch: vi.fn(),
	$api: {
		useQuery: (_method: string, path: string) => {
			if (path === "/api/admin/llm/config") {
				return {
					data: savedConfig,
					isLoading: false,
					refetch: refetchConfig,
				};
			}
			return { data: undefined, isLoading: true, refetch: vi.fn() };
		},
		useMutation: (_method: string, path: string) => ({
			mutateAsync:
				path === "/api/admin/llm/config"
					? saveConfig
					: vi.fn().mockResolvedValue({
							success: false,
							models: [],
						}),
		}),
	},
}));

vi.mock("@/services/ai-pricing", () => ({
	listPricing: vi.fn().mockResolvedValue({
		pricing: [],
		models_without_pricing: [],
	}),
	createPricing: vi.fn(),
	updatePricing: vi.fn(),
	deletePricing: vi.fn(),
}));

vi.mock("@/stores/notificationStore", () => ({
	useNotificationStore: (
		selector: (state: { notifications: never[] }) => unknown,
	) => selector({ notifications: [] }),
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

describe("LLMConfig builder model", () => {
	beforeEach(() => {
		saveConfig.mockReset();
		saveConfig.mockResolvedValue({});
		refetchConfig.mockReset();
	});

	it("loads and persists the dedicated Solution builder model override", async () => {
		renderWithProviders(<LLMConfig />);

		const builderModel = await screen.findByLabelText(
			"Solution builder model (optional)",
		);
		expect(builderModel).toHaveValue("builder-old");
		fireEvent.change(builderModel, { target: { value: "builder-new" } });
		fireEvent.click(
			screen.getByRole("button", { name: "Save Configuration" }),
		);

		await waitFor(() => {
			expect(saveConfig).toHaveBeenCalledWith(
				expect.objectContaining({
					body: expect.objectContaining({
						builder_model: "builder-new",
					}),
				}),
			);
		});
	});
});
