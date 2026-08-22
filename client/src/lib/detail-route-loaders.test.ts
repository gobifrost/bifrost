import { afterEach, describe, expect, it, vi } from "vitest";

const { prepareAppBundle } = vi.hoisted(() => ({
	prepareAppBundle: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/components/jsx-app/BundledAppShell", () => ({
	prepareAppBundle,
}));

import { queryClient } from "@/lib/queryClient";
import {
	agentDetailLoader,
	applicationDetailLoader,
} from "./detail-route-loaders";

class LoadedImage {
	complete = false;
	onload: (() => void) | null = null;
	onerror: (() => void) | null = null;

	set src(_value: string) {
		queueMicrotask(() => this.onload?.());
	}
}

describe("detail route loaders", () => {
	afterEach(() => {
		vi.restoreAllMocks();
		prepareAppBundle.mockClear();
		vi.unstubAllGlobals();
	});

	it("waits for agent metadata and its primary image", async () => {
		vi.stubGlobal("Image", LoadedImage);
		const ensure = vi
			.spyOn(queryClient, "ensureQueryData")
			.mockResolvedValue({ logo_url: "/agent-logo.png" });

		const request = new Request("http://localhost/agents/agent-1");
		await agentDetailLoader({
			params: { id: "agent-1" },
			request,
			url: new URL(request.url),
			pattern: "/agents/:id",
			context: {},
		});

		expect(ensure).toHaveBeenCalledOnce();
		expect(ensure.mock.calls[0]?.[0].queryKey).toEqual([
			"get",
			"/api/agents/{agent_id}",
			{ params: { path: { agent_id: "agent-1" } } },
		]);
	});

	it("waits for application metadata, image, and bundle readiness", async () => {
		vi.stubGlobal("Image", LoadedImage);
		vi.spyOn(queryClient, "ensureQueryData").mockResolvedValue({
			id: "app-1",
			slug: "portal",
			logo_url: "/app-logo.png",
		});
		const request = new Request("http://localhost/apps/portal");

		await applicationDetailLoader(false)({
			params: { applicationId: "portal", "*": "" },
			request,
			url: new URL(request.url),
			pattern: "/apps/:applicationId/*",
			context: {},
		});

		expect(prepareAppBundle).toHaveBeenCalledWith({
			appId: "app-1",
			isPreview: false,
			signal: request.signal,
		});
	});
});
