import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
	useQuery: vi.fn(),
}));

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: mocks.useQuery,
	},
}));

import { useAgents } from "./useAgents";

describe("useAgents", () => {
	beforeEach(() => {
		mocks.useQuery.mockReset();
	});

	it("requests ordinary visibility for chat discovery", () => {
		useAgents(undefined, { discoveryOnly: true });

		expect(mocks.useQuery).toHaveBeenCalledWith("get", "/api/agents", {
			params: { query: { discovery_only: true } },
		});
	});

	it("does not narrow administrative inventory by default", () => {
		useAgents();

		expect(mocks.useQuery).toHaveBeenCalledWith("get", "/api/agents", {
			params: { query: undefined },
		});
	});
});
