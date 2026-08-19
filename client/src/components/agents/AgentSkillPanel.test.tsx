import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockGetAgentSkill = vi.fn();
const mockDownloadAgentSkill = vi.fn();

vi.mock("@/services/agentSkills", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/agentSkills")>(
			"@/services/agentSkills",
		);
	return {
		...actual,
		getAgentSkill: (...args: unknown[]) => mockGetAgentSkill(...args),
		downloadAgentSkill: (...args: unknown[]) =>
			mockDownloadAgentSkill(...args),
	};
});

import { AgentSkillPanel } from "./AgentSkillPanel";

beforeEach(() => {
	mockGetAgentSkill.mockReset();
	mockDownloadAgentSkill.mockReset();
	mockGetAgentSkill.mockResolvedValue({
		name: "ticket-triage",
		description: "Routes tickets",
		revision: "abcdef0123456789".repeat(4),
		bundle_path: "skills/ticket-triage",
		skill_markdown: "---\nname: ticket-triage\n---",
		files: ["SKILL.md", "references/routing.md"],
		companion_files: ["references/routing.md"],
		automatic_capabilities: ["bifrost_read_agent_skill_file"],
		source: "upload",
		is_managed: false,
	});
});

describe("AgentSkillPanel", () => {
	it("shows portable identity, bundle use, and export action", async () => {
		renderWithProviders(<AgentSkillPanel agentId="agent-1" />);

		expect(await screen.findByText("ticket-triage")).toBeInTheDocument();
		expect(screen.getByText("skills/ticket-triage")).toBeInTheDocument();
		expect(screen.getByText("references/routing.md")).toBeInTheDocument();
		expect(screen.getByText("bifrost_read_agent_skill_file")).toBeInTheDocument();
		// The revision is what a consumer caches against, so it must be visible.
		expect(screen.getByText("abcdef012345")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /download skill/i }),
		).toBeInTheDocument();
	});

	it("keeps skill visibility and offers retry after a load error", async () => {
		mockGetAgentSkill.mockRejectedValue(new Error("Skill API unavailable"));
		const { user } = renderWithProviders(
			<AgentSkillPanel agentId="agent-1" />,
		);

		expect(
			await screen.findByText("Agent Skill unavailable"),
		).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Try again" }));
		await waitFor(() => {
			expect(mockGetAgentSkill).toHaveBeenCalledTimes(2);
		});
	});
});
