// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
	fireEvent,
	renderWithProviders,
	screen,
	waitFor,
	within,
} from "@/test-utils";
import { AppCodeEditorPage } from "./AppCodeEditorPage";

const mockUseApplication = vi.fn();
const mockUseCreateApplication = vi.fn();
const mockUsePublishApplication = vi.fn();
const mockMutateAsync = vi.fn();
const mockReset = vi.fn();
const mockNavigate = vi.fn();
const mockParams = vi.fn();
const mockEditorLayout = vi.fn();

let mockPublishState: Record<string, unknown>;

vi.mock("@/hooks/useApplications", () => ({
	useApplication: () => mockUseApplication(),
	useCreateApplication: () => ({
		mutateAsync: mockUseCreateApplication,
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
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return {
		...actual,
		useNavigate: () => mockNavigate,
		useParams: () => mockParams(),
		useLocation: () => ({ search: "" }),
	};
});

vi.mock("@/components/app-code-editor/AppCodeEditorLayout", () => ({
	AppCodeEditorLayout: (props: unknown) => {
		mockEditorLayout(props);
		return <div>Code editor</div>;
	},
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
	OrganizationSelect: ({
		onChange,
	}: {
		onChange: (value: string) => void;
	}) => (
		<button onClick={() => onChange("org-1")}>
			Choose test organization
		</button>
	),
}));

beforeEach(() => {
	vi.clearAllMocks();
	mockParams.mockReturnValue({ applicationId: "portal" });
	mockUseApplication.mockReturnValue({
		data: {
			id: "app-1",
			name: "Covi Portal",
			slug: "portal",
			solution_id: "solution-1",
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
	it("passes the loaded app's owning Solution to the preview layout", () => {
		renderWithProviders(<AppCodeEditorPage />);
		expect(mockEditorLayout).toHaveBeenCalledWith(
			expect.objectContaining({
				appId: "app-1",
				solutionId: "solution-1",
			}),
		);
	});

	it("queues once and closes the dialog for WebSocket notification progress", async () => {
		const { user } = renderWithProviders(<AppCodeEditorPage />);

		expect(
			screen.getByRole("button", { name: "Back to Apps" }),
		).toHaveAttribute("data-size", "icon-lg");
		expect(
			screen.getByRole("button", { name: "Settings" }),
		).toHaveAttribute("data-size", "icon-lg");
		expect(screen.getByRole("button", { name: "Publish" })).toHaveAttribute(
			"data-size",
			"lg",
		);

		await user.click(screen.getByRole("button", { name: "Publish" }));
		const dialog = screen.getByRole("dialog");
		fireEvent.change(within(dialog).getByLabelText(/publish message/i), {
			target: { value: "Release current source" },
		});
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

describe("AppCodeEditorPage create form", () => {
	it("keeps the creation shell scrollable and uses 44px header/actions on mobile", async () => {
		mockParams.mockReturnValue({});
		mockUseApplication.mockReturnValue({
			data: undefined,
			isLoading: false,
		});
		renderWithProviders(<AppCodeEditorPage />);

		expect(
			screen.getByRole("button", { name: "Back to Apps" }),
		).toHaveAttribute("data-size", "icon-lg");
		expect(
			screen.getByRole("button", { name: "Create Application" }),
		).toHaveAttribute("data-size", "lg");
		expect(screen.getByRole("button", { name: "Cancel" })).toHaveAttribute(
			"data-size",
			"lg",
		);
	});
});

describe("AppCodeEditorPage read recovery", () => {
	it("never offers app creation when an existing app fails to load", async () => {
		const refetch = vi.fn();
		mockUseApplication.mockReturnValue({
			data: undefined,
			isLoading: false,
			isFetching: false,
			refetch,
		});
		const { user } = renderWithProviders(<AppCodeEditorPage />);
		expect(
			screen.getByRole("heading", { name: "Application unavailable" }),
		).toBeVisible();
		expect(
			screen.queryByText("New Code Application"),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Retry loading" }));
		expect(refetch).toHaveBeenCalledOnce();
		expect(mockUseCreateApplication).not.toHaveBeenCalled();
	});
});

describe("AppCodeEditorPage creation", () => {
	it("keeps an automatic slug current, preserves a custom slug, and retries with the selected scope", async () => {
		mockParams.mockReturnValue({});
		mockUseApplication.mockReturnValue({
			data: undefined,
			isLoading: false,
		});
		mockUseCreateApplication
			.mockRejectedValueOnce(new Error("Synthetic create failure"))
			.mockResolvedValueOnce({ slug: "custom-portal" });
		const { user } = renderWithProviders(<AppCodeEditorPage />);
		await user.type(
			screen.getByLabelText("Name", { exact: true }),
			"Customer Portal",
		);
		expect(screen.getByLabelText("URL Slug")).toHaveValue(
			"customer-portal",
		);
		await user.clear(screen.getByLabelText("URL Slug"));
		await user.type(screen.getByLabelText("URL Slug"), "custom-portal");
		await user.type(
			screen.getByLabelText("Name", { exact: true }),
			" Updated",
		);
		expect(screen.getByLabelText("URL Slug")).toHaveValue("custom-portal");
		await user.click(
			screen.getByRole("button", { name: "Choose test organization" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Create Application" }),
		);
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Synthetic create failure",
		);
		expect(mockNavigate).not.toHaveBeenCalled();
		await user.click(screen.getByRole("button", { name: "Retry create" }));
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith(
				"/apps/custom-portal/edit",
				{ replace: true },
			),
		);
		expect(mockUseCreateApplication).toHaveBeenLastCalledWith({
			body: expect.objectContaining({
				name: "Customer Portal Updated",
				slug: "custom-portal",
				organization_id: "org-1",
				app_model: "standalone_v2",
			}),
		});
	});
});

it("keeps publish message and inline failure available for retry", async () => {
	mockMutateAsync.mockRejectedValueOnce(
		new Error("Temporary publish failure"),
	);
	const { user } = renderWithProviders(<AppCodeEditorPage />);
	await user.click(screen.getByRole("button", { name: "Publish" }));
	const dialog = screen.getByRole("dialog");
	await user.type(
		within(dialog).getByLabelText(/publish message/i),
		"Release review",
	);
	await user.click(within(dialog).getByRole("button", { name: "Publish" }));
	expect(await within(dialog).findByRole("alert")).toHaveTextContent(
		"Temporary publish failure",
	);
	expect(within(dialog).getByLabelText(/publish message/i)).toHaveValue(
		"Release review",
	);
	await user.click(within(dialog).getByRole("button", { name: "Publish" }));
	await waitFor(() =>
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
	);
	expect(mockMutateAsync).toHaveBeenCalledTimes(2);
});
