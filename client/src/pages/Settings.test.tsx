import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { Settings } from "./Settings";

vi.mock("@/pages/settings/WorkflowKeys", () => ({ WorkflowKeys: () => null }));
vi.mock("@/pages/settings/Branding", () => ({ Branding: () => null }));
vi.mock("@/pages/settings/OAuth", () => ({ OAuth: () => null }));
vi.mock("@/pages/settings/GitHub", () => ({ GitHub: () => null }));
vi.mock("@/pages/settings/LLMConfig", () => ({ LLMConfig: () => null }));
vi.mock("@/pages/settings/MCP", () => ({ MCP: () => null }));
vi.mock("@/pages/settings/Maintenance", () => ({ Maintenance: () => null }));

describe("Settings", () => {
	it("labels the SSO configuration surface as Authentication", () => {
		renderWithProviders(<Settings />, {
			initialEntries: ["/settings/sso"],
		});

		expect(
			screen.getByRole("tab", { name: /authentication/i }),
		).toBeVisible();
	});
});
