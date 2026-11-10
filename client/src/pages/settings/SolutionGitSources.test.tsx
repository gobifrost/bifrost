import { beforeEach, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import userEvent from "@testing-library/user-event";
import {
	listSolutionGitSources,
	disconnectSolutionGitSource,
	reuseSolutionGitApp,
} from "@/services/githubGitApp";
import { SolutionGitSources } from "./SolutionGitSources";

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ user: { organizationId: "home" } }),
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		value,
		onChange,
	}: {
		value: string | null;
		onChange: (value: string | null) => void;
	}) => (
		<select
			aria-label="Source Organization"
			value={value ?? "global"}
			onChange={(event) =>
				onChange(
					event.target.value === "global" ? null : event.target.value,
				)
			}
		>
			<option value="home">Home</option>
			<option value="global">Global</option>
		</select>
	),
}));
vi.mock("./GitHubAppSetup", () => ({ GitHubAppSetup: () => <p>Setup</p> }));
vi.mock("@/services/githubGitApp", () => ({
	listSolutionGitSources: vi.fn(),
	disconnectSolutionGitSource: vi.fn(),
	reuseSolutionGitApp: vi.fn(),
}));

beforeEach(() => {
	vi.resetAllMocks();
	vi.mocked(listSolutionGitSources).mockResolvedValue({
		sources: [
			{
				id: "source",
				enabled: true,
				organization_id: "home",
				app_name: "App",
				installation_id: 1,
				repositories: [
					{
						id: 2,
						full_name: "acme/private",
						private: true,
						default_branch: "main",
					},
				],
			},
		],
	});
});

it("disconnects a source without changing its installed Solution bindings", async () => {
	renderWithProviders(<SolutionGitSources onPurposeDetected={vi.fn()} />);
	await userEvent
		.setup()
		.click(
			await screen.findByRole("button", { name: "Disconnect Source" }),
		);
	expect(disconnectSolutionGitSource).toHaveBeenCalledWith("source", "home");
});

it("reuses registration only for the explicit selected scope", async () => {
	renderWithProviders(<SolutionGitSources onPurposeDetected={vi.fn()} />);
	const user = userEvent.setup();
	await user.selectOptions(screen.getByRole("combobox"), "global");
	await user.click(
		screen.getByRole("button", { name: "Use Workspace App Registration" }),
	);
	expect(reuseSolutionGitApp).toHaveBeenCalledWith(null, undefined);
});
