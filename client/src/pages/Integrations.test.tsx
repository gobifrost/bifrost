import { beforeEach, describe, expect, it, vi } from "vitest";
import { waitFor } from "@testing-library/react";
import { fireEvent, renderWithProviders, screen } from "@/test-utils";
import type { Integration } from "@/services/integrations";

import { Integrations } from "./Integrations";
const mockIsDesktop = vi.fn(() => true);
vi.mock("@/hooks/useMediaQuery", () => ({
	useIsDesktop: () => mockIsDesktop(),
}));

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return {
		...actual,
		useNavigate: () => mockNavigate,
	};
});

const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();
vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => mockToastSuccess(...args),
		error: (...args: unknown[]) => mockToastError(...args),
	},
}));

const mockUseIntegrations = vi.fn();
const mockDeleteIntegration = vi.fn();
vi.mock("@/services/integrations", async () => {
	const actual = await vi.importActual<
		typeof import("@/services/integrations")
	>("@/services/integrations");
	return {
		...actual,
		useIntegrations: () => mockUseIntegrations(),
		useDeleteIntegration: () => mockDeleteIntegration(),
	};
});

const mockExportEntities = vi.fn();
vi.mock("@/services/exportImport", () => ({
	exportEntities: (...args: unknown[]) => mockExportEntities(...args),
}));

vi.mock("@/components/search/SearchBox", () => ({
	SearchBox: ({
		value = "",
		onChange,
		placeholder,
	}: {
		value?: string;
		onChange: (value: string) => void;
		placeholder?: string;
	}) => (
		<label>
			<span className="sr-only">{placeholder ?? "Search"}</span>
			<input
				aria-label={placeholder ?? "Search"}
				value={value}
				onChange={(event) => onChange(event.target.value)}
			/>
		</label>
	),
}));

vi.mock("@/components/integrations/CreateIntegrationDialog", () => ({
	CreateIntegrationDialog: ({
		open,
		editIntegrationId,
	}: {
		open: boolean;
		editIntegrationId?: string;
	}) =>
		open ? (
			<div data-testid="create-integration-dialog">
				{editIntegrationId ? `edit:${editIntegrationId}` : "create"}
			</div>
		) : null,
}));

vi.mock("@/components/ImportDialog", () => ({
	ImportDialog: ({ open }: { open: boolean }) =>
		open ? <div data-testid="import-dialog">Import</div> : null,
}));

async function renderPage() {
	return renderWithProviders(<Integrations />);
}

function makeIntegration(
	overrides: Partial<Integration> & Pick<Integration, "id" | "name">,
): Integration {
	const { id, name, ...rest } = overrides;
	return {
		...rest,
		mapping_count: overrides.mapping_count ?? 0,
		connected_count: overrides.connected_count ?? 0,
		needs_reconnection_count: overrides.needs_reconnection_count ?? 0,
		connection_status_counts: overrides.connection_status_counts ?? {},
		id,
		name,
		has_oauth_config: overrides.has_oauth_config ?? false,
		is_deleted: overrides.is_deleted ?? false,
		created_at: overrides.created_at ?? "2026-09-07T00:00:00Z",
		updated_at: overrides.updated_at ?? "2026-09-07T00:00:00Z",
		config_schema: overrides.config_schema ?? [
			{ key: "tenant_id", type: "string", required: true },
			{ key: "region", type: "string", required: false },
			{ key: "extra", type: "string", required: false },
		],
		list_entities_data_provider_id:
			overrides.list_entities_data_provider_id ?? "data-provider",
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	mockIsDesktop.mockReturnValue(true);
	mockUseIntegrations.mockReturnValue({
		data: { items: [], total: 0 },
		isLoading: false,
		isFetching: false,
		isError: false,
		refetch: vi.fn(),
	});
	mockDeleteIntegration.mockReturnValue({
		mutateAsync: vi.fn(),
	});
});

describe("Integrations", () => {
	it("renders the desktop table, exports selected integrations, and opens actions", async () => {
		const refetch = vi.fn();
		const mutateAsync = vi.fn();
		const integrations = [
			makeIntegration({
				id: "int-1",
				name: "Slack",
				has_oauth_config: true,
			}),
			makeIntegration({
				id: "int-2",
				name: "Salesforce",
				list_entities_data_provider_id: null,
				config_schema: [],
				has_oauth_config: false,
			}),
		];
		mockUseIntegrations.mockReturnValue({
			data: { items: integrations, total: 2 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch,
		});
		mockDeleteIntegration.mockReturnValue({ mutateAsync });

		const { user } = await renderPage();
		const open = vi.spyOn(window, "open").mockImplementation(() => null);

		expect(screen.getByText("Slack")).toBeInTheDocument();
		expect(screen.getByText("Salesforce")).toBeInTheDocument();
		expect(screen.getByText("OAuth configured")).toBeInTheDocument();
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		await user.click(screen.getByRole("radio", { name: "Table view" }));
		expect(screen.getByRole("table")).toBeInTheDocument();

		await user.click(
			screen.getByRole("checkbox", {
				name: /select all visible integrations/i,
			}),
		);
		await user.click(screen.getByRole("button", { name: /export \(2\)/i }));

		expect(mockExportEntities).toHaveBeenCalledWith("integrations", [
			"int-1",
			"int-2",
		]);

		expect(screen.getByRole("link", { name: "Slack" })).toHaveAttribute(
			"href",
			"/integrations/int-1",
		);
		fireEvent.click(screen.getByRole("cell", { name: "OAuth configured" }), {
			ctrlKey: true,
		});
		expect(open).toHaveBeenCalledWith("/integrations/int-1", "_blank");
		expect(
			screen.queryByRole("button", { name: /open slack/i }),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: "Slack actions" }));
		await user.click(screen.getByRole("menuitem", { name: "Edit" }));
		expect(
			screen.getByTestId("create-integration-dialog"),
		).toHaveTextContent("edit:int-1");
	});

	it("renders mobile cards with 44px action buttons and supports delete recovery", async () => {
		mockIsDesktop.mockReturnValue(false);
		const refetch = vi.fn();
		let rejectDelete!: (error: Error) => void;
		const mutateAsync = vi
			.fn()
			.mockImplementationOnce(
				() =>
					new Promise((_resolve, reject) => {
						rejectDelete = reject;
					}),
			)
			.mockResolvedValueOnce({});
		mockUseIntegrations.mockReturnValue({
			data: {
				items: [
					makeIntegration({
						id: "int-1",
						name: "Slack",
						has_oauth_config: true,
					}),
				],
				total: 1,
			},
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch,
		});
		mockDeleteIntegration.mockReturnValue({ mutateAsync });

		const { user } = await renderPage();

		expect(screen.getByTestId("integration-card")).toBeInTheDocument();
		expect(
			screen.queryByRole("checkbox", { name: /select slack/i }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Slack" })).toHaveAttribute(
			"href",
			"/integrations/int-1",
		);
		expect(
			screen.getByRole("button", { name: /slack actions/i }),
		).toHaveAttribute("data-size", "icon-lg");

		await user.click(
			screen.getByRole("button", { name: /slack actions/i }),
		);
		await user.click(screen.getByRole("menuitem", { name: /^delete$/i }));

		await user.click(
			screen.getByRole("button", { name: /delete integration/i }),
		);
		rejectDelete(new Error("synthetic failure"));
		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Failed to delete integration. Try again.",
		);
		expect(
			screen.getByRole("alertdialog", { name: /delete integration/i }),
		).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: /delete integration/i }),
		);
		await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(2));
		expect(mockToastSuccess).toHaveBeenCalledWith(
			"Integration deleted successfully",
		);
	});

	it("uses explicit card selection mode before exporting selected integrations", async () => {
		mockIsDesktop.mockReturnValue(false);
		mockUseIntegrations.mockReturnValue({
			data: {
				items: [
					makeIntegration({ id: "int-1", name: "Slack" }),
					makeIntegration({ id: "int-2", name: "Salesforce" }),
				],
				total: 2,
			},
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});

		const { user } = await renderPage();

		expect(
			screen.queryByRole("checkbox", { name: /select slack/i }),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: /^select$/i }));

		const slackCard = screen.getByRole("button", { name: "Select Slack" });
		expect(
			screen.queryByRole("link", { name: "Slack" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Slack actions" }),
		).not.toBeInTheDocument();
		await user.click(slackCard);
		expect(
			screen.getByRole("button", { name: "Deselect Slack" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(screen.getByText("1 selected")).toBeInTheDocument();

		await user.click(screen.getByRole("button", { name: /export \(1\)/i }));
		expect(mockExportEntities).toHaveBeenCalledWith("integrations", [
			"int-1",
		]);

		await user.click(screen.getByRole("button", { name: /^done$/i }));
		expect(
			screen.queryByRole("button", { name: "Deselect Slack" }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Slack" })).toHaveAttribute(
			"href",
			"/integrations/int-1",
		);
	});

	it("shows cached records alongside a load error and retries in place", async () => {
		const refetch = vi.fn();
		const integrations = [makeIntegration({ id: "int-1", name: "Slack" })];
		mockUseIntegrations.mockReturnValue({
			data: { items: integrations, total: 1 },
			isLoading: false,
			isFetching: false,
			isError: true,
			refetch,
		});

		const { user } = await renderPage();

		expect(screen.getByRole("alert")).toHaveTextContent(
			"Couldn't load integrations.",
		);
		expect(screen.getByText("Slack")).toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: /retry loading/i }),
		);
		expect(refetch).toHaveBeenCalledTimes(1);
	});

	it("filters integrations from the search box", async () => {
		mockUseIntegrations.mockReturnValue({
			data: {
				items: [
					makeIntegration({ id: "int-1", name: "Slack" }),
					makeIntegration({ id: "int-2", name: "Salesforce" }),
				],
				total: 2,
			},
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});

		const { user } = await renderPage();

		expect(screen.getByText("Slack")).toBeInTheDocument();
		expect(screen.getByText("Salesforce")).toBeInTheDocument();

		await user.type(
			screen.getByRole("textbox", { name: /search integrations/i }),
			"sales",
		);

		await waitFor(() => expect(screen.queryByText("Slack")).toBeNull());
		expect(screen.getByText("Salesforce")).toBeInTheDocument();
	});
});
