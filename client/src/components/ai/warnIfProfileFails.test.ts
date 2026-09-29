import { beforeEach, describe, expect, it, vi } from "vitest";

const { verifyModelProfile, warning } = vi.hoisted(() => ({
	verifyModelProfile: vi.fn(),
	warning: vi.fn(),
}));

vi.mock("@/services/aiModels", () => ({ verifyModelProfile }));
vi.mock("sonner", () => ({ toast: { warning } }));

import { warnIfProfileFails } from "./warnIfProfileFails";

describe("warnIfProfileFails", () => {
	beforeEach(() => {
		verifyModelProfile.mockReset();
		warning.mockReset();
	});

	it("stays quiet when the profile answers", async () => {
		verifyModelProfile.mockResolvedValue({ success: true, message: "ok" });
		await warnIfProfileFails("p1", "Fast");
		expect(verifyModelProfile).toHaveBeenCalledWith("p1");
		expect(warning).not.toHaveBeenCalled();
	});

	it("warns with the provider's error when the profile fails", async () => {
		verifyModelProfile.mockResolvedValue({
			success: false,
			message: "401 invalid api key",
		});
		await warnIfProfileFails("p1", "Fast");
		expect(warning).toHaveBeenCalledWith(
			"Fast could not answer a test request",
			{ description: "401 invalid api key" },
		);
	});

	it("warns when the check itself cannot run", async () => {
		verifyModelProfile.mockRejectedValue(new Error("network down"));
		await warnIfProfileFails("p1", "Fast");
		expect(warning).toHaveBeenCalledWith("Fast could not be tested", {
			description: "network down",
		});
	});
});
