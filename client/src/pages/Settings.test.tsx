import { beforeEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, screen } from "@/test-utils";
import { Settings } from "./Settings";

vi.mock("@/pages/settings/WorkflowKeys", () => ({
	WorkflowKeys: () => <h2>Workflow Keys Panel</h2>,
}));
vi.mock("@/pages/settings/Branding", () => ({
	Branding: () => <h2>Branding Panel</h2>,
}));
vi.mock("@/pages/settings/OAuth", () => ({
	OAuth: () => <h2>Authentication Panel</h2>,
}));
vi.mock("@/pages/settings/GitHub", () => ({
	GitHub: () => (
		<>
			<h2>GitHub Panel</h2>
			<input aria-label="Repository draft" defaultValue="" />
		</>
	),
}));
vi.mock("@/pages/settings/AIModelSettings", () => ({
	AIModelSettings: () => <h2>Models Panel</h2>,
}));
vi.mock("@/pages/settings/AIEmbeddingSettings", () => ({
	AIEmbeddingSettings: () => <h2>Embeddings Panel</h2>,
}));
vi.mock("@/pages/settings/AIBehaviorSettings", () => ({
	AIBehaviorSettings: () => <h2>Chat Instructions Panel</h2>,
}));
vi.mock("@/pages/settings/AIUsageSettings", () => ({
	AIUsageSettings: () => <h2>Usage Panel</h2>,
}));
vi.mock("@/pages/settings/MemorySettings", () => ({
	MemorySettings: () => <h2>Memory Panel</h2>,
}));
vi.mock("@/pages/settings/RequiredInstructionsSettings", () => ({
	RequiredInstructionsSettings: () => <h2>Required Instructions Panel</h2>,
}));
vi.mock("@/pages/settings/MCP", () => ({
	MCP: () => <h2>MCP Panel</h2>,
}));
vi.mock("@/pages/settings/Maintenance", () => ({
	Maintenance: () => <h2>Maintenance Panel</h2>,
}));
vi.mock("@/pages/settings/KubernetesExecutions", () => ({
	KubernetesExecutions: () => <h2>Executions Panel</h2>,
}));

const kubernetesStatusMock = vi.fn();

vi.mock("@/services/kubernetes", () => ({
	getKubernetesStatus: (...args: unknown[]) =>
		kubernetesStatusMock(...args),
}));

describe("Settings", () => {
	beforeEach(() => {
		kubernetesStatusMock.mockReset();
		kubernetesStatusMock.mockResolvedValue({
			configured: false,
			backend: "local",
		});
	});
	it("retains a visited panel draft and hides inactive controls", async () => {
		const user = userEvent.setup();
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/github"],
		});
		await user.type(
			screen.getByRole("textbox", { name: "Repository draft" }),
			"unsaved-repository",
		);
		await user.click(
			screen.getByRole("button", { name: "Security" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Authentication" }),
		);
		expect(
			screen.queryByRole("textbox", { name: "Repository draft" }),
		).not.toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "GitHub" }),
		);
		expect(
			screen.getByRole("textbox", { name: "Repository draft" }),
		).toHaveValue("unsaved-repository");
	});
	it("selects the canonical Models panel for an unknown subsection", () => {
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/unknown"],
		});
		expect(
			screen.getByRole("button", { name: "Models" }),
		).toHaveAttribute("aria-current", "page");
		expect(
			screen.getByRole("heading", { name: "Models Panel" }),
		).toBeVisible();
	});
	it("closes mobile navigation and restores trigger focus after choosing a section", async () => {
		const user = userEvent.setup();
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/sso"],
		});
		const trigger = screen.getByRole("button", {
			name: /^Settings navigation:/,
		});
		await user.click(trigger);
		expect(trigger).toHaveAttribute("aria-expanded", "true");
		await user.click(screen.getByRole("button", { name: /^Connections$/ }));
		await user.click(screen.getByRole("button", { name: /^GitHub$/ }));
		expect(trigger).toHaveAttribute("aria-expanded", "false");
		expect(trigger).toHaveFocus();
		expect(
			screen.getByRole("heading", { name: "GitHub Panel" }),
		).toBeVisible();
	});
	it("shows the active route inside its expanded section", () => {
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/sso"],
		});

		expect(
			screen.getByRole("button", { name: /^security$/i }),
		).toHaveAttribute("aria-expanded", "true");
		expect(
			screen.getByRole("button", { name: /^authentication$/i }),
		).toHaveAttribute("aria-current", "page");
		expect(
			screen.getByRole("heading", { name: /authentication panel/i }),
		).toBeVisible();
	});

	it("navigates between setting subsections", async () => {
		const user = userEvent.setup();
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/sso"],
		});

		await user.click(
			screen.getByRole("button", { name: /^connections$/i }),
		);
		await user.click(screen.getByRole("button", { name: /^github$/i }));

		expect(
			screen.getByRole("button", { name: /^github$/i }),
		).toHaveAttribute("aria-current", "page");
		expect(
			screen.getByRole("heading", { name: /github panel/i }),
		).toBeVisible();
	});

	it("preserves expanded sections across subsection navigation", async () => {
		const user = userEvent.setup();
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/sso"],
		});

		await user.click(
			screen.getByRole("button", { name: /^connections$/i }),
		);
		await user.click(screen.getByRole("button", { name: /^github$/i }));

		expect(
			screen.getByRole("button", { name: /^security$/i }),
		).toHaveAttribute("aria-expanded", "true");
		expect(
			screen.getByRole("button", { name: /^connections$/i }),
		).toHaveAttribute("aria-expanded", "true");
	});

	it("collapses and expands primary sections", async () => {
		const user = userEvent.setup();
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/mcp"],
		});

		const connections = screen.getByRole("button", {
			name: /^connections$/i,
		});
		expect(connections).toHaveAttribute("aria-expanded", "true");
		expect(screen.getByRole("button", { name: /^github$/i })).toBeVisible();

		await user.click(connections);

		expect(connections).toHaveAttribute("aria-expanded", "false");
		expect(
			screen.queryByRole("button", { name: /^github$/i }),
		).not.toBeInTheDocument();

		await user.click(connections);

		expect(connections).toHaveAttribute("aria-expanded", "true");
		expect(screen.getByRole("button", { name: /^github$/i })).toBeVisible();
	});

	it("shows the Kubernetes section only when the deployment configures it", async () => {
		const user = userEvent.setup();
		kubernetesStatusMock.mockResolvedValue({
			configured: true,
			backend: "kubernetes",
		});
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/ai"],
		});

		await user.click(
			await screen.findByRole("button", { name: /^kubernetes$/i }),
		);
		await user.click(
			screen.getByRole("button", { name: /^executions$/i }),
		);
		expect(
			screen.getByRole("heading", { name: /executions panel/i }),
		).toBeVisible();
	});

	it("hides the Kubernetes section on deployments without it", async () => {
		kubernetesStatusMock.mockResolvedValue({
			configured: false,
			backend: "local",
		});
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/ai"],
		});

		await screen.findByRole("button", { name: /^models$/i });
		expect(
			screen.queryByRole("button", { name: /^kubernetes$/i }),
		).not.toBeInTheDocument();
	});
});
