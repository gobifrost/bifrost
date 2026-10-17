import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { KnowledgeScopeDialog } from "./KnowledgeScopeDialog";

const mockCanAt = vi.fn();
const mockCanAnywhere = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ user: { organizationId: "org-home" } }),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		canAt: mockCanAt,
		canAnywhere: mockCanAnywhere,
	}),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		onChange,
		knownOrganizations,
		loadOrganizations,
	}: {
		onChange: (value: string) => void;
		knownOrganizations: Array<{ id: string; name: string }>;
		loadOrganizations: boolean;
	}) => (
		<>
			<button onClick={() => onChange("org-target")}>
				Choose target organization
			</button>
			<span>{knownOrganizations[0]?.name}</span>
			<span>
				{loadOrganizations ? "discovers organizations" : "known only"}
			</span>
		</>
	),
}));

vi.mock("@/lib/api-client", () => ({ authFetch: vi.fn() }));

describe("KnowledgeScopeDialog", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockCanAnywhere.mockReturnValue(false);
	});

	it("requires knowledge write access at both the source and selected target scope", async () => {
		mockCanAt.mockImplementation(
			(_permission: string, target: { kind: string; id?: string }) =>
				target.kind === "org" && target.id === "org-source",
		);
		const { user } = renderWithProviders(
			<KnowledgeScopeDialog
				documentIds={["doc-1"]}
				sourceOrganizationIds={["org-source"]}
				onClose={vi.fn()}
				onSaved={vi.fn()}
			/>,
		);

		expect(screen.getByText("Your organization")).toBeInTheDocument();
		expect(screen.getByText("known only")).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", {
				name: "Choose target organization",
			}),
		);
		expect(
			screen.getByRole("button", { name: "Update Scope" }),
		).toBeDisabled();
		expect(mockCanAt).toHaveBeenCalledWith("knowledge.readwrite", {
			kind: "org",
			id: "org-source",
		});
		expect(mockCanAt).toHaveBeenCalledWith("knowledge.readwrite", {
			kind: "org",
			id: "org-target",
		});
	});
});
