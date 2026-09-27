import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";

import { renderWithProviders } from "@/test-utils";
import { GitHub } from "./GitHub";

const {
	mockUseGitHubConfig,
	mockUseGitHubRepositories,
	mockPreviewConnect,
	mockEnqueueConnect,
	mockCreateRepoMutateAsync,
	mockDisconnectMutateAsync,
	mockValidateGitHubToken,
	mockListGitHubBranches,
} = vi.hoisted(() => ({
	mockUseGitHubConfig: vi.fn(),
	mockUseGitHubRepositories: vi.fn(),
	mockPreviewConnect: vi.fn(),
	mockEnqueueConnect: vi.fn(),
	mockCreateRepoMutateAsync: vi.fn(),
	mockDisconnectMutateAsync: vi.fn(),
	mockValidateGitHubToken: vi.fn(),
	mockListGitHubBranches: vi.fn(),
}));

vi.mock("@/hooks/useGitHub", () => ({
	useGitHubConfig: () => mockUseGitHubConfig(),
	useGitHubRepositories: (enabled?: boolean) =>
		mockUseGitHubRepositories(enabled),
	previewGitHubConnect: (...args: unknown[]) => mockPreviewConnect(...args),
	enqueueGitHubConnect: (...args: unknown[]) => mockEnqueueConnect(...args),
	useCreateGitHubRepository: () => ({
		mutateAsync: mockCreateRepoMutateAsync,
		isError: false,
		reset: vi.fn(),
	}),
	useDisconnectGitHub: () => ({
		mutateAsync: mockDisconnectMutateAsync,
		isError: false,
		reset: vi.fn(),
	}),
	validateGitHubToken: (...args: unknown[]) =>
		mockValidateGitHubToken(...args),
	listGitHubBranches: (...args: unknown[]) => mockListGitHubBranches(...args),
}));

beforeEach(() => {
	vi.clearAllMocks();
	Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
		value: vi.fn(),
		configurable: true,
	});
	mockUseGitHubConfig.mockReturnValue({
		data: {
			configured: false,
			token_saved: false,
			repo_url: null,
			branch: null,
			backup_path: null,
		},
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	mockUseGitHubRepositories.mockReturnValue({
		data: undefined,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	mockPreviewConnect.mockResolvedValue({
		token: "review-token",
		repository_url: "https://github.com/fixture-owner/app",
		branch: "preview",
		state: "requires_reconciliation",
		items: [
			{ path: "apps/local.tsx", classification: "local_only" },
			{ path: "apps/remote.tsx", classification: "remote_only" },
			{ path: "apps/same.tsx", classification: "identical" },
			{ path: "apps/shared.tsx", classification: "conflict" },
		],
	});
	mockEnqueueConnect.mockResolvedValue({
		job_id: "connect-job",
		status: "queued",
		notification_id: "notification-1",
	});
	mockCreateRepoMutateAsync.mockResolvedValue({
		full_name: "fixture-owner/new-repo",
		private: true,
	});
	mockDisconnectMutateAsync.mockResolvedValue({ success: true });
	mockValidateGitHubToken.mockResolvedValue({
		repositories: [
			{ full_name: "fixture-owner/app", private: true },
			{ full_name: "fixture-owner/site", private: false },
		],
		detected_repo: null,
	});
	mockListGitHubBranches.mockResolvedValue([
		{ name: "main", protected: true },
		{ name: "preview", protected: false },
	]);
});

describe("GitHub settings", () => {
	it("previews a selected repository and queues an explicit reconcile decision", async () => {
		const { user } = renderWithProviders(<GitHub />);

		await user.type(
			screen.getByLabelText("GitHub Personal Access Token"),
			"ghp_fixture_token",
		);
		await user.click(screen.getByRole("button", { name: "Validate" }));

		await waitFor(() =>
			expect(mockValidateGitHubToken).toHaveBeenCalledWith(
				"ghp_fixture_token",
			),
		);
		expect(await screen.findByText("Token validated.")).toBeVisible();

		await user.click(screen.getByRole("combobox", { name: /repository/i }));
		await user.click(
			await screen.findByRole("option", { name: /fixture-owner\/app/i }),
		);
		await waitFor(() =>
			expect(mockListGitHubBranches).toHaveBeenCalledWith(
				"fixture-owner/app",
			),
		);

		await user.click(screen.getByRole("combobox", { name: /branch/i }));
		await user.click(
			await screen.findByRole("option", { name: /^preview$/i }),
		);
		await user.click(
			screen.getByRole("button", { name: "Review connection" }),
		);

		await waitFor(() =>
			expect(mockPreviewConnect).toHaveBeenCalledWith({
				repository_url: "fixture-owner/app",
				branch: "preview",
			}),
		);
		const review = await screen.findByRole("region", {
			name: "Review workspace connection",
		});
		expect(
			within(review).getByLabelText("Connection summary"),
		).toHaveTextContent("1 conflict");
		await user.click(
			within(review).getByRole("radio", { name: /reconcile both/i }),
		);
		await user.click(
			within(review).getByRole("radio", {
				name: /keep local.*apps\/shared.tsx/i,
			}),
		);
		await user.click(
			within(review).getByRole("button", { name: "Connect GitHub" }),
		);
		await waitFor(() =>
			expect(mockEnqueueConnect).toHaveBeenCalledWith({
				preview_token: "review-token",
				strategy: "reconcile",
				decisions: { "apps/shared.tsx": "local" },
				confirm_destructive: false,
			}),
		);
	});

	it("creates a repository with the current dialog draft and selects the created repo", async () => {
		const { user } = renderWithProviders(<GitHub />);

		await user.type(
			screen.getByLabelText("GitHub Personal Access Token"),
			"ghp_token",
		);
		await user.click(screen.getByRole("button", { name: "Validate" }));
		await screen.findByText("Token validated.");

		await user.click(screen.getByRole("button", { name: "Create New" }));
		const dialog = screen.getByRole("dialog", {
			name: "Create New Repository",
		});
		await user.type(
			within(dialog).getByLabelText("Repository Name"),
			"new-repo",
		);
		await user.type(
			within(dialog).getByLabelText("Description (Optional)"),
			"Repository from settings",
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Create Repository" }),
		);

		await waitFor(() =>
			expect(mockCreateRepoMutateAsync).toHaveBeenCalledWith({
				body: {
					name: "new-repo",
					description: "Repository from settings",
					private: true,
					organization: null,
				},
			}),
		);
		await waitFor(() =>
			expect(mockListGitHubBranches).toHaveBeenCalledWith(
				"fixture-owner/new-repo",
			),
		);
	});

	it("shows configured repository summary and disconnects through confirmation", async () => {
		mockUseGitHubConfig.mockReturnValue({
			data: {
				configured: true,
				token_saved: true,
				repo_url: "fixture-owner/configured",
				branch: "main",
				backup_path: null,
			},
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});
		const { user } = renderWithProviders(<GitHub />);

		const summary = screen.getByRole("region", {
			name: "Connected GitHub repository",
		});
		expect(summary).toHaveTextContent("fixture-owner/configured");
		expect(summary).toHaveTextContent("main");

		await user.click(
			within(summary).getByRole("button", { name: "Disconnect" }),
		);
		const dialog = screen.getByRole("dialog", {
			name: "Disconnect GitHub Integration",
		});
		await user.click(
			within(dialog).getByRole("button", { name: "Disconnect" }),
		);

		await waitFor(() =>
			expect(mockDisconnectMutateAsync).toHaveBeenCalledWith({}),
		);
		expect(
			screen.getByLabelText("GitHub Personal Access Token"),
		).toBeVisible();
	});
});
