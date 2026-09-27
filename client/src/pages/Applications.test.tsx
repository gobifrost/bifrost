import { Applications } from "./Applications";
/**
 * Tests for the Applications page — focused on managed app affordances:
 * managed apps show a lock indicator and hide destructive/edit controls;
 * non-managed apps keep their management controls.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { useLocation } from "react-router-dom";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";
import { toast } from "sonner";

const mockUseApplications = vi.fn();
const mockUseDeleteApplication = vi.fn();
const mockUseUpdateApplicationSdk = vi.fn();
const mockBatchUpdateApplicationSdks = vi.fn();
const mockTrackAccepted = vi.fn();
let mockSdkStates: Record<string, string> = {};
vi.mock("@/lib/detail-route-loaders", () => ({
	prefetchApplicationDetail: vi.fn(),
}));
vi.mock("@/hooks/useMediaQuery", () => ({ useIsDesktop: () => true }));

vi.mock("@/hooks/useApplications", () => ({
	useApplications: () => mockUseApplications(),
	useDeleteApplication: () => mockUseDeleteApplication(),
	useUpdateApplicationSdk: () => mockUseUpdateApplicationSdk(),
	batchUpdateApplicationSdks: (ids: string[]) =>
		mockBatchUpdateApplicationSdks(ids),
}));

vi.mock("@/hooks/useApplicationSdkUpdateJobs", () => ({
	useApplicationSdkUpdateJobs: () => ({
		trackAccepted: mockTrackAccepted,
		getUpdateState: (id: string) => mockSdkStates[id] ?? "idle",
	}),
}));

vi.mock("sonner", () => ({
	toast: {
		success: vi.fn(),
		warning: vi.fn(),
		error: vi.fn(),
	},
}));

const mockUseAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

// Keep the icon contract observable without loading image resources in happy-dom.
vi.mock("@/components/EntityLogo", () => ({
	EntityLogo: ({ logo }: { logo?: string | null }) => (
		<span data-testid="entity-logo" data-logo={logo ?? ""} />
	),
}));
vi.mock("@/components/app-builder/AppInfoDialog", () => ({
	AppInfoDialog: () => null,
}));
vi.mock("@/components/search/SearchBox", () => ({
	SearchBox: ({
		value,
		onChange,
		"aria-label": ariaLabel,
		placeholder,
	}: {
		value: string;
		onChange: (value: string) => void;
		"aria-label"?: string;
		placeholder?: string;
	}) => (
		<input
			aria-label={ariaLabel ?? "Search"}
			placeholder={placeholder}
			value={value}
			onChange={(event) => onChange(event.currentTarget.value)}
		/>
	),
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));

function makeApp(overrides: Partial<Record<string, unknown>> = {}) {
	return {
		id: "app-1",
		name: "Live Dash",
		slug: "live-dash",
		description: "A dashboard",
		organization_id: null,
		is_published: true,
		has_unpublished_changes: false,
		is_solution_managed: false,
		solution_id: null,
		logo_url: "/api/applications/app-1/logo",
		app_model: "legacy",
		sdk_status: "update_available",
		sdk_source_available: true,
		sdk_package_version: "1.0.0",
		sdk_fingerprint: "old",
		sdk_contract_version: 1,
		sdk_built_at: "2026-09-12T12:00:00Z",
		...overrides,
	};
}

beforeEach(() => {
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	mockUseDeleteApplication.mockReturnValue({
		mutateAsync: vi.fn(),
		isPending: false,
	});
	mockUseUpdateApplicationSdk.mockReturnValue({
		mutateAsync: vi.fn(),
	});
	mockBatchUpdateApplicationSdks.mockResolvedValue({
		accepted: [],
		skipped: [],
	});
	mockTrackAccepted.mockReset();
	mockSdkStates = {};
	vi.mocked(toast.success).mockClear();
	vi.mocked(toast.warning).mockClear();
	vi.mocked(toast.error).mockClear();
	mockUseApplications.mockReturnValue({
		data: { applications: [] },
		isLoading: false,
		refetch: vi.fn(),
	});
});

describe("Applications — bulk SDK updates", () => {
	it("updates every actionable app in organization scope while ignoring search text", async () => {
		let resolveBatch: (
			value: Awaited<ReturnType<typeof mockBatchUpdateApplicationSdks>>,
		) => void = () => {};
		mockUseApplications.mockReturnValue({
			data: {
				applications: [
					makeApp({ id: "app-1", name: "Live Dash" }),
					makeApp({
						id: "app-2",
						name: "Workflow Monitor",
						sdk_status: "unknown",
					}),
					makeApp({
						id: "app-3",
						name: "Client Portal",
						sdk_status: "current",
					}),
					makeApp({
						id: "app-4",
						name: "Asset Intake",
						sdk_status: "update_required",
						sdk_source_available: false,
					}),
				],
			},
			isLoading: false,
			refetch: vi.fn(),
		});
		mockBatchUpdateApplicationSdks.mockReturnValue(
			new Promise((resolve) => {
				resolveBatch = resolve;
			}),
		);
		const { user } = await renderPage();

		await user.type(screen.getByLabelText(/search applications/i), "Asset");
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Update all SDKs (2)" }),
			).toHaveAccessibleDescription(
				"Includes all actionable Applications in the current organization scope, including 2 hidden by search.",
			),
		);
		await user.click(
			screen.getByRole("button", { name: "Update all SDKs (2)" }),
		);

		expect(screen.getByRole("button", { name: "Queueing…" })).toBeDisabled();
		expect(screen.getByRole("button", { name: "Select" })).toBeDisabled();
		expect(mockBatchUpdateApplicationSdks).toHaveBeenCalledWith([
			"app-1",
			"app-2",
		]);
		resolveBatch({
			accepted: [
				{
					application_id: "app-1",
					job_id: "job-1",
					status: "queued",
					reused: false,
				},
			],
			skipped: [{ application_id: "app-2", reason: "conflict" }],
		});
		await waitFor(() =>
			expect(toast.success).toHaveBeenCalledWith(
				"Queued SDK updates for 1 App. 1 skipped.",
			),
		);
		expect(mockTrackAccepted).toHaveBeenCalledWith([
			expect.objectContaining({ application_id: "app-1" }),
		]);
	});

	it("warns when every requested SDK update is skipped", async () => {
		mockUseApplications.mockReturnValue({
			data: {
				applications: [
					makeApp({ id: "app-1", name: "Live Dash" }),
					makeApp({ id: "app-2", name: "Workflow Monitor" }),
				],
			},
			isLoading: false,
			refetch: vi.fn(),
		});
		mockBatchUpdateApplicationSdks.mockResolvedValue({
			accepted: [],
			skipped: [
				{ application_id: "app-1", reason: "conflict" },
				{ application_id: "app-2", reason: "conflict" },
			],
		});
		const { user } = await renderPage();

		await user.click(
			screen.getByRole("button", { name: "Update all SDKs (2)" }),
		);

		await waitFor(() =>
			expect(toast.warning).toHaveBeenCalledWith(
				"No SDK updates were queued. 2 skipped.",
			),
		);
		expect(toast.success).not.toHaveBeenCalled();
	});

	it("keeps selection after a batch request failure", async () => {
		mockUseApplications.mockReturnValue({
			data: {
				applications: [
					makeApp({ id: "app-1", name: "Live Dash" }),
					makeApp({ id: "app-2", name: "Workflow Monitor" }),
				],
			},
			isLoading: false,
			refetch: vi.fn(),
		});
		mockBatchUpdateApplicationSdks.mockRejectedValue(new Error("boom"));
		const { user } = await renderPage();

		await user.click(screen.getByRole("button", { name: "Select" }));
		await user.click(screen.getByRole("button", { name: "Live Dash" }));
		await user.click(
			screen.getByRole("button", { name: "Update selected (1)" }),
		);

		expect(toast.error).toHaveBeenCalledWith("Failed to queue SDK updates");
		expect(
			screen.getByRole("button", { name: "Live Dash" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(
			screen.getByRole("button", { name: "Update selected (1)" }),
		).toBeVisible();
	});

	it("clears and exits selection mode after accepted selected updates", async () => {
		let resolveBatch: (
			value: Awaited<ReturnType<typeof mockBatchUpdateApplicationSdks>>,
		) => void = () => {};
		mockUseApplications.mockReturnValue({
			data: {
				applications: [
					makeApp({ id: "app-1", name: "Live Dash" }),
					makeApp({
						id: "app-2",
						name: "Client Portal",
						sdk_status: "current",
					}),
					makeApp({ id: "app-3", name: "Workflow Monitor" }),
				],
			},
			isLoading: false,
			refetch: vi.fn(),
		});
		mockBatchUpdateApplicationSdks.mockReturnValue(
			new Promise((resolve) => {
				resolveBatch = resolve;
			}),
		);
		const { user } = await renderPage();

		await user.click(screen.getByRole("button", { name: "Select" }));
		await user.click(screen.getByRole("button", { name: "Select all" }));
		await user.click(
			screen.getByRole("button", { name: "Update selected (2)" }),
		);

		expect(screen.getByRole("button", { name: "Select all" })).toBeDisabled();
		expect(screen.getByRole("button", { name: "Queueing…" })).toBeDisabled();
		expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
		expect(mockBatchUpdateApplicationSdks).toHaveBeenCalledWith([
			"app-1",
			"app-3",
		]);
		resolveBatch({
			accepted: [
				{
					application_id: "app-1",
					job_id: "job-1",
					status: "queued",
					reused: false,
				},
				{
					application_id: "app-3",
					job_id: "job-3",
					status: "queued",
					reused: false,
				},
			],
			skipped: [],
		});
		await waitFor(() =>
			expect(mockTrackAccepted).toHaveBeenCalledWith([
				expect.objectContaining({ application_id: "app-1" }),
				expect.objectContaining({ application_id: "app-3" }),
			]),
		);
		expect(mockTrackAccepted).toHaveBeenCalledWith([
			expect.objectContaining({ application_id: "app-1" }),
			expect.objectContaining({ application_id: "app-3" }),
		]);
		expect(
			screen.queryByRole("button", { name: "Update selected (2)" }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Select" })).toBeVisible();
	});

	it("passes accepted SDK jobs back into the list badges", async () => {
		mockSdkStates = { "app-1": "updating" };
		mockUseApplications.mockReturnValue({
			data: { applications: [makeApp({ id: "app-1" })] },
			isLoading: false,
			refetch: vi.fn(),
		});

		await renderPage();

		expect(screen.getByLabelText("Updating SDK")).toBeVisible();
	});
});

async function renderPage() {
	return renderWithProviders(
		<>
			<Applications />
			<LocationProbe />
		</>,
	);
}

function LocationProbe() {
	const location = useLocation();
	return <output aria-label="location">{location.pathname}</output>;
}

describe("Applications — app launch behavior", () => {
	it("shows a loading indicator while the list loads", async () => {
		mockUseApplications.mockReturnValue({
			data: undefined,
			isLoading: true,
			refetch: vi.fn(),
		});
		await renderPage();
		expect(
			screen.getByRole("status", { name: /loading applications/i }),
		).toBeInTheDocument();
	});

	it("does not expose the deprecated create code application action", async () => {
		await renderPage();
		expect(
			screen.queryByTitle(/create application/i),
		).not.toBeInTheDocument();
	});

	it("opens v2 apps directly without showing an Open Published hover action", async () => {
		mockUseApplications.mockReturnValue({
			data: {
				applications: [
					makeApp({
						app_model: "standalone_v2",
						is_published: true,
					}),
				],
			},
			isLoading: false,
			refetch: vi.fn(),
		});
		await renderPage();
		expect(
			screen.getByRole("link", { name: "Live Dash" }),
		).toHaveAttribute("href", "/apps/live-dash");
		expect(screen.queryByText(/open published/i)).not.toBeInTheDocument();
	});

	it("shows an undeployed v2 App without an in-platform code editor", async () => {
		mockUseApplications.mockReturnValue({
			data: {
				applications: [
					makeApp({
						app_model: "standalone_v2",
						is_published: false,
					}),
				],
			},
			isLoading: false,
			refetch: vi.fn(),
		});
		await renderPage();
		expect(screen.getByText("Not deployed")).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /code editor/i }),
		).not.toBeInTheDocument();
	});
});

describe("Applications — solution-managed badge (grid view)", () => {
	it("shows the badge and hides Edit/Code on a managed app", async () => {
		mockUseApplications.mockReturnValue({
			data: {
				applications: [
					makeApp({
						id: "m",
						name: "Managed App",
						is_solution_managed: true,
						solution_id: "s1",
					}),
				],
			},
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		const badge = screen.getByLabelText("Managed by a Solution");
		expect(badge).toHaveAttribute("href", "/solutions/s1");
		await user.click(
			screen.getByRole("button", { name: "Managed App actions" }),
		);
		expect(
			screen.getByRole("menuitem", { name: /open published/i }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: /delete application/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("menuitem", { name: /^edit$/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("menuitem", { name: /code editor/i }),
		).not.toBeInTheDocument();
	});

	it("shows Edit/Code controls and no badge on a non-managed app", async () => {
		mockUseApplications.mockReturnValue({
			data: { applications: [makeApp()] },
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		await user.click(
			screen.getByRole("button", { name: "Live Dash actions" }),
		);
		expect(
			screen.queryByLabelText("Managed by a Solution"),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: /^edit$/i }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: /code editor/i }),
		).toBeInTheDocument();
	});
});

describe("Applications — solution-managed badge (table view)", () => {
	async function renderTable(apps: ReturnType<typeof makeApp>[]) {
		mockUseApplications.mockReturnValue({
			data: { applications: apps },
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		await user.click(screen.getByLabelText(/table view/i));
		return user;
	}

	it("shows the badge and hides Delete on a managed app row", async () => {
		await renderTable([
			makeApp({
				id: "m",
				name: "Managed App",
				is_solution_managed: true,
				solution_id: "s1",
			}),
		]);
		const table = document.querySelector("table")!;
		const badge = within(table).getByLabelText("Managed by a Solution");
		expect(badge).toHaveAttribute("href", "/solutions/s1");
		expect(
			within(table).queryByRole("button", { name: /delete/i }),
		).not.toBeInTheDocument();
	});

	it("shows Delete and no badge on a non-managed app row", async () => {
		const user = await renderTable([makeApp()]);
		await user.click(
			screen.getByRole("button", { name: "Live Dash actions" }),
		);
		const table = document.querySelector("table")!;
		expect(within(table).getByTestId("entity-logo")).toHaveAttribute(
			"data-logo",
			"/api/applications/app-1/logo",
		);
		expect(
			within(table).queryByLabelText("Managed by a Solution"),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Delete" }),
		).toBeInTheDocument();
	});

	it("opens the published app from the table row", async () => {
		const user = await renderTable([makeApp()]);
		const table = document.querySelector("table")!;

		await user.click(
			within(table).getByRole("row", { name: /Live Dash/i }),
		);

		expect(screen.getByLabelText("location")).toHaveTextContent(
			"/apps/live-dash",
		);
	});
});

it("distinguishes a failed initial lookup from an empty list and retries", async () => {
	const refetch = vi.fn();
	mockUseApplications.mockReturnValue({
		data: undefined,
		isLoading: false,
		isError: true,
		isFetching: false,
		refetch,
	});
	const { user } = await renderPage();
	expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load");
	expect(screen.queryByText(/No .* found/i)).not.toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Retry loading" }));
	expect(refetch).toHaveBeenCalledTimes(1);
});

it("opens card deletion without launching the app", async () => {
	mockUseApplications.mockReturnValue({
		data: { applications: [makeApp()] },
		isLoading: false,
		refetch: vi.fn(),
	});
	const { user } = await renderPage();
	screen.getByRole("button", { name: "Live Dash actions" }).focus();
	await user.keyboard("{Enter}");
	screen.getByRole("menuitem", { name: "Delete" }).focus();
	await user.keyboard("{Enter}");
	expect(screen.getByRole("alertdialog")).toHaveTextContent("Live Dash");
});
