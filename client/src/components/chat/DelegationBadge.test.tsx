/**
 * Component tests for DelegationBadge (M6 multi-agent delegation).
 *
 * Cover:
 *   - "consulting <agent>" while running, "consulted <agent>" once complete
 *   - status → icon class (spinner / check / error)
 *   - duration shown only when complete
 *   - expandable detail shows the delegated task + response, and the error
 *     (hiding the response) when the delegation failed
 */

import { describe, it, expect } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

import { DelegationBadge } from "./DelegationBadge";
import type { ChatDelegationInfo } from "@/services/websocket";

function makeDelegation(
	overrides: Partial<ChatDelegationInfo> = {},
): ChatDelegationInfo {
	return {
		tool_call_id: "call_1",
		agent_id: "agent-uuid",
		agent_name: "Researcher",
		task: "Find facts about X",
		response: "Here are the facts.",
		error: null,
		duration_ms: 1500,
		...overrides,
	};
}

describe("DelegationBadge — label + status", () => {
	it("shows 'consulting <agent>' with a spinner while running", () => {
		const { container } = renderWithProviders(
			<DelegationBadge
				delegation={makeDelegation({ response: null })}
				status="running"
			/>,
		);
		expect(screen.getByText("consulting Researcher")).toBeInTheDocument();
		const icon = container.querySelector("svg");
		expect(icon?.getAttribute("class") || "").toMatch(/animate-spin/);
	});

	it("shows '✓ consulted <agent>' with a settled (muted) check when completed", () => {
		const { container } = renderWithProviders(
			<DelegationBadge delegation={makeDelegation()} status="completed" />,
		);
		expect(screen.getByText("consulted Researcher")).toBeInTheDocument();
		// Completed badge is de-emphasized via the muted-foreground token
		// (design-system color, not a raw green-500).
		const icon = container.querySelector("svg");
		expect(icon?.getAttribute("class") || "").toMatch(/text-muted-foreground/);
	});

	it("shows an error icon when the delegation failed", () => {
		const { container } = renderWithProviders(
			<DelegationBadge
				delegation={makeDelegation({
					response: null,
					error: "sub-agent blew up",
				})}
				status="error"
			/>,
		);
		const icon = container.querySelector("svg");
		expect(icon?.getAttribute("class") || "").toMatch(/text-destructive/);
	});

	it("hides duration while running", () => {
		renderWithProviders(
			<DelegationBadge
				delegation={makeDelegation({ response: null })}
				status="running"
			/>,
		);
		expect(screen.queryByText("1.5s")).not.toBeInTheDocument();
	});

	it("shows duration once complete", () => {
		renderWithProviders(
			<DelegationBadge delegation={makeDelegation()} status="completed" />,
		);
		expect(screen.getByText("1.5s")).toBeInTheDocument();
	});
});

describe("DelegationBadge — expandable detail", () => {
	it("expands to show the delegated task and response", async () => {
		const { user } = renderWithProviders(
			<DelegationBadge delegation={makeDelegation()} status="completed" />,
		);

		await user.click(screen.getByText("consulted Researcher"));

		expect(
			await screen.findByText("Find facts about X"),
		).toBeInTheDocument();
		expect(screen.getByText("Here are the facts.")).toBeInTheDocument();
	});

	it("shows the error and hides the response when failed", async () => {
		const { user } = renderWithProviders(
			<DelegationBadge
				delegation={makeDelegation({
					response: null,
					error: "sub-agent blew up",
				})}
				status="error"
			/>,
		);

		await user.click(screen.getByText("consulted Researcher"));

		expect(
			await screen.findByText("sub-agent blew up"),
		).toBeInTheDocument();
		expect(
			screen.queryByText(/response/i),
		).not.toBeInTheDocument();
	});
});
