import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { Knowledge } from "./Knowledge";

const mockBoundary = vi.fn();
const mockAuthFetch = vi.fn();

vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => mockBoundary(),
}));

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/components/knowledge/KnowledgeDocumentDrawer", () => ({
	KnowledgeDocumentDrawer: () => null,
}));

vi.mock("@/components/ImportDialog", () => ({
	ImportDialog: () => null,
}));

vi.mock("@/services/exportImport", () => ({
	exportEntities: vi.fn(),
}));

beforeEach(() => {
	vi.clearAllMocks();
	mockAuthFetch.mockResolvedValue({ ok: true, json: async () => [] });
});

describe("Knowledge", () => {
	it("keeps the Managed customer collection read-only", async () => {
		mockBoundary.mockReturnValue({
			selectedBoundary: "managed_organizations",
			selectedTarget: {
				boundary: "managed_organizations",
				kind: "managed_organizations",
				label: "Managed organizations",
				organization_id: null,
				capabilities: ["knowledge.read", "knowledge.readwrite"],
			},
			hasSelectedCapability: (capability: string) =>
				["knowledge.read", "knowledge.readwrite"].includes(capability),
		});

		renderWithProviders(<Knowledge />);

		await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled());
		expect(screen.queryByRole("button", { name: /add document/i })).not.toBeInTheDocument();
		expect(screen.queryByRole("button", { name: /import/i })).not.toBeInTheDocument();
		expect(
			screen.getByText(/browse knowledge available in the current working context/i),
		).toBeInTheDocument();
	});

	it("offers authoring in an exact Organization context with knowledge.readwrite", async () => {
		mockBoundary.mockReturnValue({
			selectedBoundary: "organization:org-1",
			selectedTarget: {
				boundary: "organization:org-1",
				kind: "organization",
				label: "Acme",
				organization_id: "org-1",
				capabilities: ["knowledge.readwrite"],
			},
			hasSelectedCapability: (capability: string) =>
				capability === "knowledge.readwrite",
		});

		renderWithProviders(<Knowledge />);

		expect(
			screen.getAllByRole("button", { name: /add document/i }),
		).not.toHaveLength(0);
		expect(screen.getByRole("button", { name: /import/i })).toBeInTheDocument();
	});
});
