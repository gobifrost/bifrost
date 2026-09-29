import { beforeEach, describe, expect, it, vi } from "vitest";

const apiClient = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("@/lib/api-client", () => ({ apiClient }));

import { displayModelName, getModelDisplayNames } from "./modelNames";

describe("modelNames", () => {
	beforeEach(() => apiClient.GET.mockReset());

	it("loads the id-to-name map", async () => {
		apiClient.GET.mockResolvedValue({ data: { names: { "gpt-4o": "GPT-4o" } } });
		await expect(getModelDisplayNames()).resolves.toEqual({ "gpt-4o": "GPT-4o" });
		expect(apiClient.GET).toHaveBeenCalledWith("/api/model-catalog/names");
	});

	it("names known ids, dated snapshots, and falls back to the id", () => {
		const names = { "claude-haiku-4-5": "Claude Haiku 4.5" };
		expect(displayModelName(names, "claude-haiku-4-5")).toBe("Claude Haiku 4.5");
		expect(displayModelName(names, "claude-haiku-4-5-20251001")).toBe("Claude Haiku 4.5");
		expect(displayModelName(names, "custom-finetune")).toBe("custom-finetune");
		expect(displayModelName(undefined, "gpt-4o")).toBe("gpt-4o");
	});
});
