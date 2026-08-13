import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
}));

import {
	getRequiredInstructionsSettings,
	updateRequiredInstructionsSettings,
} from "./required-instructions";

describe("required instructions service", () => {
	beforeEach(() => authFetchMock.mockReset());

	it("loads global and organization settings from distinct scopes", async () => {
		authFetchMock
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ instructions: "Global" }), {
					status: 200,
				}),
			)
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ instructions: "Organization" }), {
					status: 200,
				}),
			);

		await expect(getRequiredInstructionsSettings()).resolves.toEqual({
			instructions: "Global",
		});
		await expect(
			getRequiredInstructionsSettings("org-1"),
		).resolves.toEqual({ instructions: "Organization" });

		expect(authFetchMock).toHaveBeenNthCalledWith(
			1,
			"/api/admin/required-instructions",
			undefined,
		);
		expect(authFetchMock).toHaveBeenNthCalledWith(
			2,
			"/api/admin/required-instructions/organizations/org-1",
			undefined,
		);
	});

	it("updates Markdown in the selected organization scope", async () => {
		authFetchMock.mockResolvedValue(
			new Response(JSON.stringify({ instructions: "Use the runbook." }), {
				status: 200,
			}),
		);

		await updateRequiredInstructionsSettings("Use the runbook.", "org-1");

		expect(authFetchMock).toHaveBeenCalledWith(
			"/api/admin/required-instructions/organizations/org-1",
			expect.objectContaining({
				method: "PUT",
				body: '{"instructions":"Use the runbook."}',
			}),
		);
	});
});
