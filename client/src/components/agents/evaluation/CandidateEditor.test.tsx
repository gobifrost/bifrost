import { describe, expect, it, vi } from "vitest";
import { fireEvent, waitFor } from "@testing-library/react";
import { renderWithProviders, screen } from "@/test-utils";
import { CandidateEditor, CandidatePromotion } from "./CandidateEditor";
import { agentPlatform } from "@/services/agentPlatform";
import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";
vi.mock("@/components/ai/ModelProfileSelector", () => ({
	ModelProfileSelector: () => <div>Profile selector</div>,
}));
vi.mock("@/services/agentPlatform", () => ({
	agentPlatform: { createCandidate: vi.fn() },
}));
vi.mock("@/lib/api-client", () => ({
	apiClient: { GET: vi.fn(), PUT: vi.fn() },
}));
const agent = {
	id: "agent",
	name: "Support",
	system_prompt: "Live prompt",
	organization_id: null,
} as components["schemas"]["AgentPublic"];
describe("candidate isolation", () => {
	it("creates a snapshot override without updating production", async () => {
		vi.mocked(agentPlatform.createCandidate).mockResolvedValue({
			id: "candidate",
		} as never);
		const created = vi.fn();
		renderWithProviders(
			<CandidateEditor agent={agent} onCreated={created} />,
		);
		fireEvent.change(
			screen.getByLabelText("Candidate prompt · evaluation only"),
			{ target: { value: "Proposed prompt" } },
		);
		fireEvent.click(
			screen.getByRole("button", { name: "Create immutable candidate" }),
		);
		await waitFor(() => expect(created).toHaveBeenCalled());
		expect(agentPlatform.createCandidate).toHaveBeenCalledWith(
			expect.objectContaining({
				overlays: { system_prompt: "Proposed prompt" },
			}),
		);
		expect(apiClient.PUT).not.toHaveBeenCalled();
	});
	it("requires current-production diff review before applying", async () => {
		vi.mocked(apiClient.GET).mockResolvedValue({ data: agent } as never);
		vi.mocked(apiClient.PUT).mockResolvedValue({ data: agent } as never);
		renderWithProviders(
			<CandidatePromotion
				agentId="agent"
				candidate={{
					id: "candidate",
					evaluation_only: true,
					overlays: {
						system_prompt: "Proposed prompt",
						tool_ids: null,
						max_iterations: null,
					},
				}}
				onApplied={vi.fn()}
			/>,
		);
		expect(apiClient.PUT).not.toHaveBeenCalled();
		fireEvent.click(
			screen.getByRole("button", { name: "Review production diff" }),
		);
		expect(
			await screen.findByLabelText("Live system_prompt"),
		).toHaveTextContent("Live prompt");
		expect(
			screen.getByLabelText("Candidate system_prompt"),
		).toHaveTextContent("Proposed prompt");
		fireEvent.click(
			screen.getByRole("button", {
				name: "Apply these changes to live Agent",
			}),
		);
		await waitFor(() =>
			expect(apiClient.PUT).toHaveBeenCalledWith(
				"/api/agents/{agent_id}",
				{
					params: { path: { agent_id: "agent" } },
					body: {
						system_prompt: "Proposed prompt",
						clear_roles: false,
					},
				},
			),
		);
	});
});
