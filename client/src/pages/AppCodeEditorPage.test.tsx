// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";

const mockUseApplication = vi.fn();
const mockUsePublishApplication = vi.fn();
const mockMutateAsync = vi.fn();
const mockReset = vi.fn();
const mockNavigate = vi.fn();

let mockPublishState: Record<string, unknown>;

vi.mock("@/hooks/useApplications", () => ({
	useApplication: () => mockUseApplication(),
	useCreateApplication: () => ({
		mutateAsync: vi.fn(),
		isPending: false,
	}),
	usePublishApplication: () => mockUsePublishApplication(),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		user: { organizationId: null },
		isPlatformAdmin: true,
	}),
}));

vi.mock("react-router-dom", async () => {
	const actual = await vi.importActual<typeof import("react-router-dom")>(
		"react-router-dom",
	);
	return {
		...actual,
		useNavigate: () => mockNavigate,
		useParams: () => ({ applicationId: "portal" }),
		useLocation: () => ({ search: "" }),
	};
});

vi.mock("@/components/app-code-editor/AppCodeEditorLayout", () => ({
	AppCodeEditorLayout: () => <div>Code editor</div>,
}));
vi.mock("@/components/app-builder/AppInfoDialog", () => ({
	AppInfoDialog: () => null,
}));
vi.mock("@/components/app-builder/EmbedSettingsDialog", () => ({
	EmbedSettingsDialog: () => null,
}));
vi.mock("@/components/solutions/SolutionManagedBanner", () => ({
	SolutionManagedBanner: () => null,
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));

beforeEach(() => {
	vi.clearAllMocks();
	mockUseApplication.mockReturnValue({
		data: {
			id: "app-1",
			name: "Covi Portal",
			slug: "portal",
			has_unpublished_changes: true,
			is_solution_managed: false,
		},
		isLoading: false,
	});
	mockMutateAsync.mockResolvedValue({
		job_id: "job-1",
		notification_id: "notification-1",
		status: "queued",
		reused: false,
	});
	mockPublishState = {
		mutateAsync: mockMutateAsync,
		reset: mockReset,
		isPending: false,
	};
	mockUsePublishApplication.mockImplementation(() => mockPublishState);
});

describe("AppCodeEditorPage publish flow", () => {
	it("queues once and closes the dialog for WebSocket notification progress", async () => {
		const { AppCodeEditorPage } = await import("./AppCodeEditorPage");
		const { user } = renderWithProviders(<AppCodeEditorPage />);

		await user.click(screen.getByRole("button", { name: "Publish" }));
		const dialog = screen.getByRole("dialog");
		await user.type(
			within(dialog).getByLabelText(/publish message/i),
			"Release current source",
		);
		await user.click(
			within(dialog).getByRole("button", { name: "Publish" }),
		);

		expect(mockMutateAsync).toHaveBeenCalledWith({
			params: { path: { app_id: "app-1" } },
			body: { message: "Release current source" },
		});
		await waitFor(() => {
			expect(screen.queryByRole("dialog")).toBeNull();
		});
		expect(mockReset).toHaveBeenCalledOnce();
	});
});
