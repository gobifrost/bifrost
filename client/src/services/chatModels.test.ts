import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: vi.fn(),
	},
}));

import { apiClient } from "@/lib/api-client";
import { getChatModelProfiles } from "./chatModels";

const mockGet = apiClient.GET as unknown as ReturnType<typeof vi.fn>;

describe("getChatModelProfiles", () => {
	beforeEach(() => {
		mockGet.mockReset();
	});

	it("fetches chat-enabled model profiles", async () => {
		const response = {
			profiles: [
				{
					id: "profile-balanced",
					name: "Balanced",
					label: "Balanced",
					capabilities: {
						image_input: true,
						pdf_input: false,
						tool_calling: true,
						source: "verified",
						fingerprint: "abc",
					},
				},
			],
			default_profile_id: "profile-balanced",
		};
		mockGet.mockResolvedValue({ data: response, error: undefined });

		await expect(getChatModelProfiles()).resolves.toEqual(response);
		expect(mockGet).toHaveBeenCalledWith("/api/chat/model-profiles");
	});

	it("throws when the API returns an error", async () => {
		mockGet.mockResolvedValue({ data: undefined, error: { detail: "Nope" } });

		await expect(getChatModelProfiles()).rejects.toThrow(
			"Failed to fetch chat model profiles",
		);
	});
});
