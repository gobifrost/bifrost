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
	GitHub: ({ canWrite }: { canWrite?: boolean }) => (
		<div>
			<h2>GitHub Panel</h2>
			<span>{canWrite ? "write" : "read-only"}</span>
		</div>
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
vi.mock("@/pages/settings/Builder", () => ({
	BuilderSettings: () => <h2>Builder Panel</h2>,
}));

const { allowedCapabilities } = vi.hoisted(() => ({
	allowedCapabilities: new Set<string>(),
}));

vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => ({
		hasSelectedCapability: (capability: string) =>
			allowedCapabilities.has("*") ||
			allowedCapabilities.has(capability),
	}),
}));

describe("Settings", () => {
	beforeEach(() => {
		allowedCapabilities.clear();
		allowedCapabilities.add("*");
	});

	it("shows the active route inside its expanded section", () => {
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/sso"],
		});

		expect(
			screen.getByRole("button", { name: /^security$/i }),
		).toHaveAttribute("aria-expanded", "true");
		expect(
			screen.getByRole("button", { name: /authentication/i }),
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

	it("shows only settings admitted by the selected Role", () => {
		allowedCapabilities.clear();
		allowedCapabilities.add("repository.read");

		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/github"],
		});

		expect(screen.getByRole("button", { name: /^connections$/i })).toBeVisible();
		expect(screen.getByRole("button", { name: /^github$/i })).toBeVisible();
		expect(screen.getByText("read-only")).toBeVisible();
		expect(screen.queryByRole("button", { name: /^ai$/i })).not.toBeInTheDocument();
		expect(screen.queryByRole("button", { name: /builder/i })).not.toBeInTheDocument();
	});

	it("passes write access to admitted setting panes", () => {
		allowedCapabilities.clear();
		allowedCapabilities.add("repository.readwrite");

		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/github"],
		});

		expect(screen.getByText("write")).toBeVisible();
	});
});
