import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { SolutionPromotions } from "./SolutionPromotions";

const mockListPromotionReviews = vi.fn();
const mockPromoteSolution = vi.fn();

vi.mock("@/services/solutionPromotions", async () => {
	const actual = await vi.importActual<
		typeof import("@/services/solutionPromotions")
	>("@/services/solutionPromotions");
	return {
		...actual,
		listPromotionReviews: (...args: unknown[]) =>
			mockListPromotionReviews(...args),
		promoteSolution: (...args: unknown[]) => mockPromoteSolution(...args),
	};
});

vi.mock("@/hooks/useUsers", () => ({
	useUsersFiltered: () => ({ data: [], isLoading: false }),
}));

const review = {
	solution_id: "solution-1",
	slug: "dispatch-board",
	name: "Dispatch board",
	owner_user_id: "owner-1",
	organization_id: "org-1",
	promotion_status: "requested",
	pinned_revision_id: "revision-12345678",
	source_sha256: "abc123",
	source_size_bytes: 2048,
	prior_deployed_revision_id: "revision-0",
	changed_paths: ["apps/dispatch/src/App.tsx"],
	requested_at: "2026-07-28T12:00:00Z",
	requested_by: "owner-1",
	current_revision_id: "revision-12345678",
	deployed_revision_id: "revision-12345678",
	build_job_id: "build-1",
	deploy_job_id: "deploy-1",
	build_status: "succeeded",
	deploy_status: "succeeded",
	entity_counts: {
		apps: 1,
		workflows: 1,
		agents: 0,
		forms: 0,
		tables: 0,
		configs: 0,
		integrations: 0,
		roles: 1,
		files: 0,
		file_policies: 0,
		policy_rules: 0,
		events: 0,
	},
	unresolved_roles: ["Dispatcher"],
	connection_names: ["HaloPSA"],
	config_keys_requiring_reentry_for_global: ["OPENAI_API_KEY"],
	global_repo_access: false,
	ready: true,
	blockers: [],
};

beforeEach(() => {
	mockListPromotionReviews.mockReset();
	mockPromoteSolution.mockReset();
	mockListPromotionReviews.mockResolvedValue([review]);
	mockPromoteSolution.mockResolvedValue({
		solution_id: "solution-1",
		target: "company",
		visibility: "shared",
		promoted_revision_id: "revision-12345678",
		roles_created: ["Dispatcher"],
	});
});

describe("SolutionPromotions", () => {
	it("shows pinned source evidence and gates promotion on approvals", async () => {
		const { user } = renderWithProviders(<SolutionPromotions />);

		expect(
			await screen.findByRole("heading", { name: "Dispatch board" }),
		).toBeInTheDocument();
		expect(screen.getByText("abc123")).toBeInTheDocument();
		expect(
			screen.getByText("apps/dispatch/src/App.tsx"),
		).toBeInTheDocument();

		const promote = screen.getByRole("button", {
			name: /promote to company/i,
		});
		expect(promote).toBeDisabled();

		await user.click(
			screen.getByRole("checkbox", { name: /approve role creation/i }),
		);
		await user.click(screen.getByRole("checkbox", { name: "HaloPSA" }));
		expect(promote).toBeEnabled();

		await user.click(promote);
		await user.click(
			await screen.findByRole("button", { name: "Promote Solution" }),
		);

		await waitFor(() => {
			expect(mockPromoteSolution).toHaveBeenCalledWith(
				"solution-1",
				expect.objectContaining({
					target: "company",
					approve_role_creation: true,
					approved_connection_names: ["HaloPSA"],
				}),
			);
		});
	});

	it("shows the familiar empty review queue state", async () => {
		mockListPromotionReviews.mockResolvedValue([]);
		renderWithProviders(<SolutionPromotions />);

		expect(
			await screen.findByRole("heading", {
				name: /review queue is clear/i,
			}),
		).toBeInTheDocument();
	});
});
