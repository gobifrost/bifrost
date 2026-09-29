import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";

const service = vi.hoisted(() => ({
	getModelCatalog: vi.fn(),
	refreshModelCatalog: vi.fn(),
}));
vi.mock("@/services/aiModels", () => service);

import { ModelCatalogStatus } from "./ModelCatalogStatus";

describe("ModelCatalogStatus", () => {
	beforeEach(() => {
		service.getModelCatalog.mockReset();
		service.refreshModelCatalog.mockReset();
	});

	it("says when the catalog is still the bundled copy", async () => {
		service.getModelCatalog.mockResolvedValue({
			source: "bundled",
			fetched_at: null,
			provider_count: 225,
			model_count: 8000,
			providers: [{ id: "openai" }, { id: "anthropic" }],
		});
		renderWithProviders(<ModelCatalogStatus />);

		expect(await screen.findByTestId("model-catalog-status")).toHaveTextContent(
			"2 providers from the models.dev catalog · bundled copy, not refreshed yet",
		);
	});

	it("queues a refresh on request", async () => {
		service.getModelCatalog.mockResolvedValue({
			source: "refreshed",
			fetched_at: new Date().toISOString(),
			provider_count: 1,
			model_count: 1,
			providers: [{ id: "openai" }],
		});
		service.refreshModelCatalog.mockResolvedValue({ job_id: "job-1" });
		const { user } = renderWithProviders(<ModelCatalogStatus />);

		await user.click(await screen.findByRole("button", { name: /refresh catalog/i }));
		await waitFor(() => expect(service.refreshModelCatalog).toHaveBeenCalledOnce());
	});
});
