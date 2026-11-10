import { expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { previewWorkspaceBundleFromRepo } from "@/services/solutions";
import { WorkspaceImportBody } from "./WorkspaceImportBody";
import { Dialog, DialogContent } from "@/components/ui/dialog";

vi.mock("./InstallSession", async (importOriginal) => ({
	...(await importOriginal<typeof import("./InstallSession")>()),
	useInstallSession: () => ({
		setWide: vi.fn(),
		setTall: vi.fn(),
		finish: vi.fn(),
	}),
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		value,
		onChange,
	}: {
		value: string | null;
		onChange: (id: string | null) => void;
	}) => (
		<select
			aria-label="Target Scope"
			value={value ?? "global"}
			onChange={(event) =>
				onChange(
					event.target.value === "global" ? null : event.target.value,
				)
			}
		>
			<option value="global">Global</option>
			<option value="other">Other</option>
		</select>
	),
}));
vi.mock("@/services/solutions", () => ({
	getSolutionGitSources: vi.fn(async () => ({
		sources: [
			{
				id: "source",
				enabled: true,
				app_name: "App",
				installation_id: 7,
				organization_id: null,
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
	})),
	previewWorkspaceBundleFromRepo: vi.fn(),
	previewWorkspaceBundle: vi.fn(),
	importWorkspaceBundle: vi.fn(),
}));

it("retains a private binding across scope changes instead of choosing anonymous access", async () => {
	vi.mocked(previewWorkspaceBundleFromRepo).mockRejectedValue(
		new Error("Source unavailable in this scope"),
	);
	const { user } = renderWithProviders(
		<Dialog open>
			<DialogContent>
				<WorkspaceImportBody
					source="repo"
					initialRepo={null}
					initialFile={null}
					onBack={vi.fn()}
					onClose={vi.fn()}
				/>
			</DialogContent>
		</Dialog>,
	);
	await screen.findByRole("option", { name: "App · acme/private" });
	await user.selectOptions(
		screen.getByRole("combobox", { name: "Repository Access" }),
		"source:9",
	);
	await user.selectOptions(
		screen.getByRole("combobox", { name: "Target Scope" }),
		"other",
	);
	await user.click(screen.getByRole("button", { name: "Preview snapshot" }));
	await waitFor(() =>
		expect(previewWorkspaceBundleFromRepo).toHaveBeenCalledWith({
			repo_url: "https://github.com/acme/private",
			git_ref: "main",
			repo_subpath: null,
			organization_id: "other",
			git_source_id: "source",
		}),
	);
	expect(
		await screen.findByText("Source unavailable in this scope"),
	).toBeVisible();
});
