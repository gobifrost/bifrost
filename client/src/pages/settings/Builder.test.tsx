import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { BuilderSettings } from "./Builder";

const mockGetSetup = vi.fn();
const mockSaveSetup = vi.fn();
const mockProvision = vi.fn();
const mockGetPlatformJob = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ user: { id: "admin-1" } }),
}));
vi.mock("@/services/builderRunner", () => ({
	getBuilderRunnerSetup: (...args: unknown[]) => mockGetSetup(...args),
	saveBuilderRunnerSetup: (...args: unknown[]) => mockSaveSetup(...args),
	provisionBuilderRunner: (...args: unknown[]) => mockProvision(...args),
}));
vi.mock("@/services/platformJobs", () => ({
	getPlatformJob: (...args: unknown[]) => mockGetPlatformJob(...args),
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
	active_provisioning_job_id: null,
};

beforeEach(() => {
	vi.clearAllMocks();
	mockGetSetup.mockResolvedValue(setup);
	mockSaveSetup.mockResolvedValue(setup.config);
	mockProvision.mockResolvedValue({ job_id: "job-1", status: "queued" });
	mockGetPlatformJob.mockReset();
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

	it("restores durable provisioning progress after a reload", async () => {
		mockGetSetup.mockResolvedValue({
			...setup,
			active_provisioning_job_id: "job-active",
		});
		mockGetPlatformJob.mockResolvedValue({
			id: "job-active",
			status: "running",
			progress: { phase: "Starting a real runner container", percent: 45 },
		});

		renderWithProviders(<BuilderSettings />);

		expect(
			(await screen.findAllByText("Starting a real runner container")).length,
		).toBeGreaterThan(0);
		expect(screen.getAllByText("45%")).toHaveLength(2);
		expect(
			screen.getByRole("button", { name: /provision and test/i }),
		).toBeDisabled();
	});

	it("shows actionable blockers and reverts failed enablement", async () => {
		mockGetSetup.mockResolvedValue({
			...setup,
			config: {
				...setup.config,
				provisioned: true,
				connected: true,
			},
			readiness: {
				...setup.readiness,
				ai_configured: true,
				credentials_configured: true,
				provisioned: true,
				connected: true,
				blockers: [
					{
						code: "builder_disabled",
						message: "Builder is still disabled.",
						action: "Enable it after reviewing these checks.",
					},
				],
			},
		});
		mockSaveSetup.mockRejectedValue(new Error("Settings changed elsewhere"));
		const { user } = renderWithProviders(<BuilderSettings />);

		expect(await screen.findByText("Builder is still disabled.")).toBeInTheDocument();
		expect(
			screen.getByText("Enable it after reviewing these checks."),
		).toBeInTheDocument();
		const toggle = screen.getByRole("switch", {
			name: /enable builder for users/i,
		});
		await user.click(toggle);

		await waitFor(() =>
			expect(mockSaveSetup).toHaveBeenCalledWith(
				expect.objectContaining({ enabled: true }),
			),
		);
		await waitFor(() => expect(toggle).not.toBeChecked());
	});
});
