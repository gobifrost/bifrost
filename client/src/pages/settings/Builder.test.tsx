import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { BuilderSettings } from "./Builder";

const mockGetSetup = vi.fn();
const mockSaveSetup = vi.fn();
const mockProvision = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ user: { id: "admin-1" } }),
}));
vi.mock("@/services/builderRunner", () => ({
	getBuilderRunnerSetup: (...args: unknown[]) => mockGetSetup(...args),
	saveBuilderRunnerSetup: (...args: unknown[]) => mockSaveSetup(...args),
	provisionBuilderRunner: (...args: unknown[]) => mockProvision(...args),
}));
vi.mock("@/services/websocket", () => ({
	webSocketService: {
		connect: vi.fn(),
		onPlatformJobUpdate: vi.fn(() => vi.fn()),
	},
}));

const setup = {
	config: {
		provider: "cloudflare" as const,
		enabled: false,
		callback_base_url: "https://bifrost.example.com",
		provisioned: false,
		connected: false,
		cloudflare: {
			account_id: null,
			api_token_set: false,
			script_name: "bifrost-builder-runner",
			workflow_name: "bifrost-builder-workflow",
		},
		local: null,
	},
	readiness: {
		configured: true,
		ready: false,
		ai_configured: false,
		provider: "cloudflare" as const,
		enabled: false,
		credentials_configured: false,
		callback_configured: true,
		provisioned: false,
		connected: false,
		blockers: [],
	},
	recommended_callback_base_url: "https://bifrost.example.com",
	runner_image: "ghcr.io/gobifrost/bifrost-builder-runner:dev",
	cloudflare_permissions: ["Workers Scripts Write", "Workers Containers Write"],
};

beforeEach(() => {
	vi.clearAllMocks();
	mockGetSetup.mockResolvedValue(setup);
	mockSaveSetup.mockResolvedValue(setup.config);
	mockProvision.mockResolvedValue({ job_id: "job-1", status: "queued" });
});

describe("BuilderSettings", () => {
	it("walks the administrator through AI, runner, callback, and enablement", async () => {
		renderWithProviders(<BuilderSettings />);

		expect(await screen.findByRole("heading", { name: /native app building/i })).toBeInTheDocument();
		expect(screen.getByRole("link", { name: /configure ai/i })).toHaveAttribute("href", "/settings/ai");
		expect(screen.getByText(/no additional hostname or forwarded port is needed/i)).toBeInTheDocument();
		expect(screen.getByText(/containers scale to zero between jobs/i)).toBeInTheDocument();
		expect(screen.getByText(/hard turn limits/i)).toBeInTheDocument();
		expect(screen.getByText(/cloudflare container charges remain/i)).toBeInTheDocument();
		expect(screen.getByRole("link", { name: /view ai usage/i })).toHaveAttribute("href", "/reports/usage");
		expect(screen.getByRole("switch", { name: /enable builder for users/i })).toBeDisabled();
	});

	it("saves a scoped Cloudflare configuration without exposing the token again", async () => {
		const { user } = renderWithProviders(<BuilderSettings />);
		await screen.findByRole("heading", { name: /native app building/i });
		await user.type(screen.getByLabelText(/account id/i), "account-123");
		await user.type(screen.getByLabelText(/api token/i), "secret-token");
		await user.click(screen.getByRole("button", { name: /save settings/i }));

		await waitFor(() =>
			expect(mockSaveSetup).toHaveBeenCalledWith(
				expect.objectContaining({
					provider: "cloudflare",
					enabled: false,
					callback_base_url: "https://bifrost.example.com",
					cloudflare: expect.objectContaining({
						account_id: "account-123",
						api_token: "secret-token",
					}),
				}),
			),
		);
	});
});
