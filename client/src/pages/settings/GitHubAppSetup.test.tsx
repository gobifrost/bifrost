import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GitHubAppSetup } from "./GitHubAppSetup";
import * as service from "@/services/githubGitApp";

vi.mock("@/services/githubGitApp", () => ({
	getGitAppSetup: vi.fn(),
	takeGitAppCallback: vi.fn(),
	completeGitAppCallback: vi.fn(),
	createGitApp: vi.fn(),
	configureExistingGitApp: vi.fn(),
	submitGitAppManifest: vi.fn(),
	authorizeGitApp: vi.fn(),
	openGitAppAuthorization: vi.fn(),
	refreshGitAppInstallations: vi.fn(),
	listGitAppRepositories: vi.fn(),
	selectGitAppRepository: vi.fn(),
}));
const ready = {
	purpose: "workspace" as const,
	setup_id: "id",
	stage: "repository" as const,
	expires_at: "2030-01-01",
	app_name: "App",
	installations: [{ id: 10, account: "org" }],
	repositories: [],
};

beforeEach(() => {
	vi.resetAllMocks();
	vi.mocked(service.getGitAppSetup).mockResolvedValue(null);
	vi.mocked(service.takeGitAppCallback).mockReturnValue(null);
});

it("routes a Solution callback even when workspace Git is already connected", async () => {
	vi.mocked(service.takeGitAppCallback).mockReturnValue({
		code: "late",
		state: "manifest.state",
	});
	const callbackSetup = { ...ready, purpose: "solution" as const };
	vi.mocked(service.completeGitAppCallback).mockResolvedValue(callbackSetup);
	const onPurposeDetected = vi.fn();
	render(
		<GitHubAppSetup
			onPurposeDetected={onPurposeDetected}
			onReady={vi.fn()}
			onMigrated={vi.fn()}
			connectedWithApp={true}
		/>,
	);
	await waitFor(() =>
		expect(service.takeGitAppCallback).toHaveBeenCalledOnce(),
	);
	expect(service.completeGitAppCallback).toHaveBeenCalledWith({
		code: "late",
		state: "manifest.state",
	});
	await waitFor(() =>
		expect(onPurposeDetected).toHaveBeenCalledWith(
			"solution",
			callbackSetup,
		),
	);
	expect(service.getGitAppSetup).not.toHaveBeenCalled();
});

it("creates an App with least permissions and explicit visibility", async () => {
	const user = userEvent.setup();
	render(
		<GitHubAppSetup
			onReady={vi.fn()}
			onMigrated={vi.fn()}
			connectedWithApp={false}
		/>,
	);
	await user.type(screen.getByLabelText("GitHub Owner"), "our-org");
	await user.click(
		screen.getByRole("button", { name: /^Create GitHub App$/ }),
	);
	await waitFor(() =>
		expect(service.createGitApp).toHaveBeenCalledWith({
			owner: "our-org",
			owner_type: "organization",
			name: "Bifrost Git Sync",
			public: false,
			workflows: false,
			purpose: "workspace",
		}),
	);
});

it("only returns the verified selected repository to the existing Git UI", async () => {
	const user = userEvent.setup();
	const repository = {
		id: 20,
		full_name: "org/repo",
		private: true,
		default_branch: "main",
	};
	vi.mocked(service.getGitAppSetup).mockResolvedValue(ready);
	vi.mocked(service.listGitAppRepositories).mockResolvedValue({
		...ready,
		repositories: [repository],
	});
	vi.mocked(service.selectGitAppRepository).mockResolvedValue({
		...ready,
		stage: "ready",
		repositories: [repository],
	});
	const onReady = vi.fn();
	render(
		<GitHubAppSetup
			onReady={onReady}
			onMigrated={vi.fn()}
			connectedWithApp={false}
		/>,
	);
	await user.selectOptions(
		await screen.findByLabelText("Installation"),
		"10",
	);
	await user.selectOptions(screen.getByLabelText("App Repository"), "20");
	await user.click(
		screen.getByRole("button", { name: "Use Selected Repository" }),
	);
	await waitFor(() => expect(onReady).toHaveBeenCalledWith("id", repository));
});

it("keeps an uncertain registration in recovery instead of offering another create", async () => {
	vi.mocked(service.getGitAppSetup).mockResolvedValue({
		...ready,
		app_name: null,
		stage: "registration_needs_review",
	});
	render(
		<GitHubAppSetup
			onReady={vi.fn()}
			onMigrated={vi.fn()}
			connectedWithApp={false}
		/>,
	);
	await screen.findByText(/Registration may have completed/);
	expect(
		screen.queryByRole("button", { name: /^Create GitHub App$/ }),
	).not.toBeInTheDocument();
	expect(
		screen.getByRole("button", { name: "Use Existing App" }),
	).toBeVisible();
});

it("shows callback failures without changing a working connection", async () => {
	vi.mocked(service.takeGitAppCallback).mockImplementation(() => {
		throw new Error("GitHub authorization was cancelled");
	});
	const onMigrated = vi.fn();
	render(
		<GitHubAppSetup
			onReady={vi.fn()}
			onMigrated={onMigrated}
			connectedWithApp={false}
		/>,
	);
	expect(await screen.findByRole("alert")).toHaveTextContent("cancelled");
	expect(onMigrated).not.toHaveBeenCalled();
});
