import { describe, expect, it } from "vitest";

import {
	createAgentRunNavigationState,
	getLocationHref,
	readAgentRunNavigationOrigin,
} from "./agent-run-navigation";

describe("agent run navigation", () => {
	it("builds a return href from the current router location", () => {
		expect(
			getLocationHref({
				pathname: "/agents/agent-1/runs/run-1",
				search: "?view=activity",
				hash: "#delegation",
			}),
		).toBe(
			"/agents/agent-1/runs/run-1?view=activity#delegation",
		);
	});

	it("round-trips a valid in-app origin", () => {
		const state = createAgentRunNavigationState({
			href: "/agents/agent-1/runs/run-1?view=activity",
			label: "Back to Service Desk Triage run",
		});

		expect(readAgentRunNavigationOrigin(state)).toEqual(
			state.agentRunOrigin,
		);
	});

	it.each([
		null,
		{},
		{ agentRunOrigin: null },
		{ agentRunOrigin: { href: "/history", label: "" } },
		{
			agentRunOrigin: {
				href: "https://example.com/history",
				label: "Back",
			},
		},
		{ agentRunOrigin: { href: "//example.com/history", label: "Back" } },
		{ agentRunOrigin: { href: "/\\example.com", label: "Back" } },
	])("rejects malformed or external navigation state", (state) => {
		expect(readAgentRunNavigationOrigin(state)).toBeNull();
	});
});
