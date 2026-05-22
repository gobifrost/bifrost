import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/api-client", () => ({
	apiClient: { POST: vi.fn(), DELETE: vi.fn() },
}));

import { regenerateInvite, resendInvite, revokeInvite } from "./user-invites";
import { apiClient } from "@/lib/api-client";

describe("user-invites service", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("resendInvite POSTs to /resend with user_id path param", async () => {
		(apiClient.POST as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
			data: { user_id: "u1", registration_url: "x", event_emitted: true, expires_at: "" },
			error: null,
		});
		const r = await resendInvite("u1");
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/users/{user_id}/invite/resend",
			{ params: { path: { user_id: "u1" } } },
		);
		expect(r.registration_url).toBe("x");
	});

	it("regenerateInvite POSTs to /regenerate", async () => {
		(apiClient.POST as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
			data: { user_id: "u2", registration_url: "y", event_emitted: false, expires_at: "" },
			error: null,
		});
		const r = await regenerateInvite("u2");
		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/users/{user_id}/invite/regenerate",
			{ params: { path: { user_id: "u2" } } },
		);
		expect(r.registration_url).toBe("y");
	});

	it("revokeInvite DELETEs", async () => {
		(apiClient.DELETE as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
			data: undefined,
			error: null,
		});
		await revokeInvite("u3");
		expect(apiClient.DELETE).toHaveBeenCalledWith(
			"/api/users/{user_id}/invite",
			{ params: { path: { user_id: "u3" } } },
		);
	});

	it("propagates errors from the api client", async () => {
		(apiClient.POST as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
			data: null,
			error: { detail: "boom" },
		});
		await expect(regenerateInvite("u4")).rejects.toEqual({ detail: "boom" });
	});
});
