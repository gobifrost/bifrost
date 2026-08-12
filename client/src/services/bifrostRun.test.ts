import { beforeEach, describe, expect, it, vi } from "vitest";

const authFetchMock = vi.fn();

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => authFetchMock(...args),
}));

import { downloadBifrostRunPlugin } from "./bifrostRun";

describe("downloadBifrostRunPlugin", () => {
	beforeEach(() => {
		authFetchMock.mockReset();
	});

	it("downloads the plugin and uses the server filename", async () => {
		const blob = new Blob(["plugin"], { type: "application/zip" });
		authFetchMock.mockResolvedValue(
			new Response(blob, {
				status: 200,
				headers: {
					"Content-Disposition":
						'attachment; filename="custom-bifrost-agent.zip"',
				},
			}),
		);

		const result = await downloadBifrostRunPlugin();

		expect(authFetchMock).toHaveBeenCalledWith("/api/mcp/run/plugin");
		expect(result.filename).toBe("custom-bifrost-agent.zip");
		expect(await result.blob.text()).toBe("plugin");
	});

	it("uses a stable fallback filename", async () => {
		authFetchMock.mockResolvedValue(
			new Response(new Blob(["plugin"]), { status: 200 }),
		);

		await expect(downloadBifrostRunPlugin()).resolves.toMatchObject({
			filename: "bifrost-agent.zip",
		});
	});

	it("rejects failed downloads", async () => {
		authFetchMock.mockResolvedValue(new Response(null, { status: 403 }));

		await expect(downloadBifrostRunPlugin()).rejects.toThrow(
			"Failed to download Bifrost Agent",
		);
	});
});
