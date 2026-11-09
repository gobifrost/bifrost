import { beforeEach, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import userEvent from "@testing-library/user-event";
import { getSolutionGitSources } from "@/services/solutions";
import {
	SolutionGitSourcePicker,
	PrivateSolutionRepositoryFields,
} from "./SolutionGitSourcePicker";

vi.mock("@/services/solutions", () => ({ getSolutionGitSources: vi.fn() }));
beforeEach(() => vi.resetAllMocks());

it("keeps Home and Global source inventories in separate cache entries", async () => {
	vi.mocked(getSolutionGitSources).mockImplementation(async (scope) => ({
		sources: [
			{
				id: scope === undefined ? "home" : "global",
				app_name: scope === undefined ? "Home" : "Global",
				installation_id: 7,
				organization_id: scope ?? null,
				enabled: true,
				repositories: [
					{
						id: 9,
						full_name: "acme/private",
						private: true,
						default_branch: "main",
					},
				],
			},
		],
	}));
	const props = { sourceId: null, repoUrl: "", onChange: vi.fn() };
	const view = renderWithProviders(
		<SolutionGitSourcePicker {...props} organizationId={undefined} />,
	);
	await screen.findByRole("option", { name: /Home/ });
	view.rerender(<SolutionGitSourcePicker {...props} organizationId={null} />);
	await screen.findByRole("option", { name: /Global/ });
	expect(
		screen.queryByRole("option", { name: /Home/ }),
	).not.toBeInTheDocument();
	expect(getSolutionGitSources).toHaveBeenCalledWith(undefined);
	expect(getSolutionGitSources).toHaveBeenCalledWith(null);
});

it("lists only enabled sources for the exact target and selects a pinned repository", async () => {
	vi.mocked(getSolutionGitSources).mockResolvedValue({
		sources: [
			{
				id: "source",
				app_name: "App",
				installation_id: 7,
				organization_id: "org",
				enabled: true,
				repositories: [
					{
						id: 9,
						full_name: "acme/private",
						private: true,
						default_branch: "release",
					},
				],
			},
		],
	});
	const change = vi.fn();
	renderWithProviders(
		<SolutionGitSourcePicker
			organizationId="org"
			sourceId={null}
			repoUrl=""
			onChange={change}
		/>,
	);
	await screen.findByRole("option", { name: "App · acme/private" });
	await userEvent
		.setup()
		.selectOptions(
			screen.getByRole("combobox", { name: "Repository Access" }),
			"source:9",
		);
	expect(getSolutionGitSources).toHaveBeenCalledWith("org");
	expect(change).toHaveBeenCalledWith(
		"source",
		"https://github.com/acme/private",
		"release",
	);
});

it("keeps an unavailable binding selected without silently switching to public", async () => {
	vi.mocked(getSolutionGitSources).mockResolvedValue({ sources: [] });
	const change = vi.fn();
	renderWithProviders(
		<SolutionGitSourcePicker
			organizationId={null}
			sourceId="missing"
			repoUrl="https://github.com/acme/private"
			onChange={change}
		/>,
	);
	expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
	expect(screen.getByRole("combobox")).toHaveValue("unavailable");
	expect(change).not.toHaveBeenCalled();
});

it("locks private coordinates to the selected repository and disconnects explicitly", async () => {
	const disconnect = vi.fn();
	renderWithProviders(
		<PrivateSolutionRepositoryFields
			repoUrl="https://github.com/acme/private"
			subpath="pkg"
			gitRef="main"
			onSubpathChange={vi.fn()}
			onRefChange={vi.fn()}
			onDisconnect={disconnect}
		/>,
	);
	expect(screen.getByLabelText("Repository URL")).toHaveAttribute("readonly");
	await userEvent
		.setup()
		.click(screen.getByRole("button", { name: "Disconnect Git" }));
	expect(disconnect).toHaveBeenCalledOnce();
});
