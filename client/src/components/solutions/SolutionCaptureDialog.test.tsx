import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mockGetSolutionCaptureCandidates = vi.fn();
const mockCaptureSolutionEntities = vi.fn();
const mockPreviewSolutionCapture = vi.fn();
const mockOnClose = vi.fn();
const mockOnCaptured = vi.fn();

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/services/solutions", () => ({
	getSolutionCaptureCandidates: (...a: unknown[]) =>
		mockGetSolutionCaptureCandidates(...a),
	captureSolutionEntities: (...a: unknown[]) => mockCaptureSolutionEntities(...a),
	previewSolutionCapture: (...a: unknown[]) => mockPreviewSolutionCapture(...a),
}));

async function renderDialog() {
	const { SolutionCaptureDialog } = await import("./SolutionCaptureDialog");
	return renderWithProviders(
		<SolutionCaptureDialog
			open
			solutionId="sol-1"
			onClose={mockOnClose}
			onCaptured={mockOnCaptured}
		/>,
	);
}

beforeEach(() => {
	vi.clearAllMocks();
	mockGetSolutionCaptureCandidates.mockResolvedValue({
		workflows: [{ id: "wf-1", name: "Sync Tickets" }],
		apps: [],
		forms: [],
		agents: [],
		tables: [{ id: "tbl-1", name: "Customers" }],
		claims: [{ id: "claim-1", name: "customer_regions" }],
		configs: [
			{
				id: "cfg-1",
				key: "api_token",
				type: "secret",
				required: false,
				description: "API token",
				value_set: true,
			},
		],
	});
	mockCaptureSolutionEntities.mockResolvedValue({
		solution_id: "sol-1",
		workflows_captured: 1,
		apps_captured: 0,
		forms_captured: 0,
		agents_captured: 0,
		tables_captured: 0,
		claims_captured: 1,
		config_declarations_captured: 1,
	});
	mockPreviewSolutionCapture.mockResolvedValue({
		pulled_in: [],
		outside_references: [],
		scan_is_static: true,
	});
});

describe("SolutionCaptureDialog", () => {
	it("submits selected entity ids and config keys", async () => {
		const { user } = await renderDialog();

		await screen.findByRole("heading", { name: /capture existing entities/i });
		await user.click(screen.getByLabelText(/capture sync tickets/i));
		await user.click(screen.getByLabelText(/capture customer_regions/i));
		await user.click(screen.getByLabelText(/capture api_token/i));
		await user.click(screen.getByRole("button", { name: /capture 3/i }));

		expect(mockCaptureSolutionEntities).toHaveBeenCalledWith("sol-1", {
			workflows: ["wf-1"],
			apps: [],
			forms: [],
			agents: [],
			tables: [],
			claims: ["claim-1"],
			configs: ["api_token"],
			include_imports: false,
		});
		expect(mockOnCaptured).toHaveBeenCalled();
		expect(mockOnClose).toHaveBeenCalled();
	});

	it("shows the dependency preview: pulled-in items and outside-reference warnings", async () => {
		mockPreviewSolutionCapture.mockResolvedValue({
			pulled_in: [{ kind: "table", ref: "tbl-9", name: "orders", in_selection: false }],
			outside_references: [
				{
					referencer_kind: "workflow",
					referencer_ref: "wf-9",
					referencer_name: "nightly-sync",
					target_kind: "table",
					target_ref: "tbl-9",
					target_name: "orders",
				},
			],
			scan_is_static: true,
		});

		const { user } = await renderDialog();
		await screen.findByRole("heading", { name: /capture existing entities/i });
		await user.click(screen.getByLabelText(/capture sync tickets/i));

		// Preview surfaces the pulled-in table and the outside-reference warning.
		expect(await screen.findByText(/dependency preview/i)).toBeVisible();
		expect(await screen.findByText(/also pulled in/i)).toBeVisible();
		expect(await screen.findByText(/outside references/i)).toBeVisible();
		expect(
			await screen.findByText(/is also used by workflow/i),
		).toBeVisible();
	});

	it("captures with include_imports when the toggle is on", async () => {
		const { user } = await renderDialog();
		await screen.findByRole("heading", { name: /capture existing entities/i });
		await user.click(screen.getByLabelText(/capture sync tickets/i));
		await user.click(screen.getByLabelText(/include shared imports/i));
		await user.click(screen.getByRole("button", { name: /capture 1/i }));

		expect(mockCaptureSolutionEntities).toHaveBeenCalledWith(
			"sol-1",
			expect.objectContaining({ include_imports: true }),
		);
	});
});
