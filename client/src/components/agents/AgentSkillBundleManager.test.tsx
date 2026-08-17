import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockGetAgentSkill = vi.fn();
const mockUploadAgentSkill = vi.fn();
const mockDetachAgentSkill = vi.fn();

vi.mock("@/services/agentSkills", async () => {
	const actual =
		await vi.importActual<typeof import("@/services/agentSkills")>(
			"@/services/agentSkills",
		);
	return {
		...actual,
		getAgentSkill: (...args: unknown[]) => mockGetAgentSkill(...args),
		uploadAgentSkill: (...args: unknown[]) => mockUploadAgentSkill(...args),
		detachAgentSkill: (...args: unknown[]) => mockDetachAgentSkill(...args),
	};
});

vi.mock("@/components/file-tree/FileTree", () => ({
	FileTree: () => <div data-testid="skill-file-tree">File tree</div>,
}));
vi.mock("@/components/ui/tiptap-editor", () => ({
	TiptapEditor: ({
		content,
		ariaLabel,
	}: {
		content: string;
		ariaLabel?: string;
		readOnly?: boolean;
	}) => (
		<div role="document" aria-label={ariaLabel}>
			{content}
		</div>
	),
}));

import { AgentSkillBundleManager } from "./AgentSkillBundleManager";

function skill(overrides: Record<string, unknown> = {}) {
	return {
		name: "expense-tracker",
		description: "Track expenses",
		bundle_path: null,
		skill_markdown:
			"---\nname: expense-tracker\ndescription: Track expenses\n---\n\nDo the work.",
		files: ["SKILL.md"],
		companion_files: [],
		automatic_capabilities: [],
		source: "inline",
		is_managed: false,
		...overrides,
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	mockGetAgentSkill.mockResolvedValue(skill());
	mockUploadAgentSkill.mockResolvedValue(
		skill({
			bundle_path: "skills/expense-tracker",
			files: ["SKILL.md", "references/categories.md"],
			companion_files: ["references/categories.md"],
			automatic_capabilities: ["read_skill_asset"],
			source: "upload",
		}),
	);
	mockDetachAgentSkill.mockResolvedValue(undefined);
});

describe("AgentSkillBundleManager", () => {
	it("accepts a portable archive without exposing a raw storage path", async () => {
		const { user } = renderWithProviders(
			<AgentSkillBundleManager agentId="agent-1" />,
		);

		const input = await screen.findByLabelText(/upload agent skill archive/i);
		const archive = new File(["zip"], "expense-tracker.skill", {
			type: "application/zip",
		});
		await user.upload(input, archive);

		await waitFor(() => {
			expect(mockUploadAgentSkill).toHaveBeenCalledWith("agent-1", archive);
		});
		expect(
			screen.queryByRole("textbox", { name: /bundle path/i }),
		).not.toBeInTheDocument();
	});

	it("renders a Solution bundle as a browsable, read-only file tree", async () => {
		mockGetAgentSkill.mockResolvedValue(
			skill({
				bundle_path: "skills/expense-tracker",
				files: ["SKILL.md", "references/categories.md"],
				companion_files: ["references/categories.md"],
				source: "solution",
				is_managed: true,
			}),
		);

		const { user } = renderWithProviders(
			<AgentSkillBundleManager
				agentId="agent-1"
				isSolutionManaged
			/>,
		);

		expect(await screen.findByTestId("skill-file-tree")).toBeInTheDocument();
		expect(screen.getByText("Managed by Solution")).toBeInTheDocument();
		expect(screen.getByText("Read-only")).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /replace/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /remove bundle/i }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("document", { name: /preview skill.md/i }),
		).toHaveTextContent("Do the work.");
		expect(
			screen.getByRole("document", { name: /preview skill.md/i }),
		).not.toHaveTextContent("name: expense-tracker");

		await user.click(
			screen.getByRole("radio", { name: /view markdown source/i }),
		);
		expect(
			screen.getByText(
				(_content, element) =>
					element?.tagName === "PRE" &&
					Boolean(element.textContent?.includes("name: expense-tracker")),
			),
		).toBeInTheDocument();
	});

	it("explains that a new agent must be saved before upload", () => {
		renderWithProviders(<AgentSkillBundleManager />);

		expect(
			screen.getByText(/save this agent before adding a bundle/i),
		).toBeInTheDocument();
	});
});
