import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { Settings } from "./Settings";

vi.mock("@/pages/settings/WorkflowKeys", () => ({ WorkflowKeys: () => null }));
vi.mock("@/pages/settings/Branding", () => ({ Branding: () => null }));
vi.mock("@/pages/settings/OAuth", () => ({ OAuth: () => null }));
vi.mock("@/pages/settings/GitHub", () => ({
	GitHub: ({ canWrite }: { canWrite?: boolean }) => (
		<div data-testid="github-content">
			{canWrite ? "write" : "read-only"}
		</div>
	),
}));
vi.mock("@/pages/settings/LLMConfig", () => ({ LLMConfig: () => null }));
vi.mock("@/pages/settings/MCP", () => ({ MCP: () => null }));
vi.mock("@/pages/settings/Maintenance", () => ({ Maintenance: () => null }));
vi.mock("@/pages/settings/Builder", () => ({ BuilderSettings: () => null }));
const { allowedCapabilities } = vi.hoisted(() => ({
	allowedCapabilities: new Set<string>(),
}));
vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => ({
		hasSelectedCapability: (capability: string) =>
			allowedCapabilities.has("*") || allowedCapabilities.has(capability),
	}),
}));

describe("Settings", () => {
	beforeEach(() => {
		allowedCapabilities.clear();
		allowedCapabilities.add("*");
	});

	it("labels the SSO configuration surface as Authentication", () => {
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/sso"],
		});

		expect(
			screen.getByRole("tab", { name: /authentication/i }),
		).toBeVisible();
	});

	it("renders read-only settings admitted by the selected Role", async () => {
		allowedCapabilities.clear();
		allowedCapabilities.add("repository.read");
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/github"],
		});

		expect(screen.getByRole("tab", { name: /github/i })).toBeVisible();
		expect(screen.getByTestId("github-content")).toHaveTextContent("read-only");
		expect(screen.queryByRole("tab", { name: /^ai$/i })).not.toBeInTheDocument();
		expect(screen.queryByRole("tab", { name: /builder/i })).not.toBeInTheDocument();
	});

	it("passes write access to setting panes when the selected Role can mutate", async () => {
		allowedCapabilities.clear();
		allowedCapabilities.add("repository.readwrite");
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/github"],
		});

		expect(screen.getByTestId("github-content")).toHaveTextContent("write");
	});
});
