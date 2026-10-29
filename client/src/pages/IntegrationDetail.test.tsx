import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mockUseAuthorization = vi.fn();
const mockUseIntegration = vi.fn();
const mockUseOrganizations = vi.fn();

vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return { ...actual, useParams: () => ({ id: "integration-1" }) };
});

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => mockUseAuthorization(),
}));

vi.mock("@/lib/api-client", () => ({
	$api: { useQuery: () => mockUseOrganizations() },
}));

vi.mock("@/services/integrations", () => ({
	useIntegration: () => mockUseIntegration(),
	useUpdateMapping: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useDeleteMapping: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useUpdateIntegrationConfig: () => ({
		isPending: false,
		mutateAsync: vi.fn(),
	}),
	useTestIntegration: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useBatchUpsertMappings: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useCreateMapping: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useAuthorizeMapping: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useDisconnectMapping: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useRefreshMapping: () => ({ isPending: false, mutateAsync: vi.fn() }),
	planOrgConfigSave: () => ({ kind: "noop" }),
}));

vi.mock("@/hooks/useOAuth", () => ({
	useAuthorizeOAuthConnection: () => ({ isPending: false, mutate: vi.fn() }),
	useRefreshOAuthToken: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useDeleteOAuthConnection: () => ({
		isPending: false,
		mutateAsync: vi.fn(),
	}),
}));

vi.mock("@/hooks/useIntegrationEntities", () => ({
	useIntegrationEntities: () => ({
		data: [],
		isLoading: false,
		isError: false,
	}),
}));

vi.mock("@/hooks/useAutoMatch", () => ({
	useAutoMatch: () => ({
		suggestions: new Map(),
		matchStats: null,
		isMatching: false,
		runAutoMatch: vi.fn(),
		acceptSuggestion: vi.fn(),
		rejectSuggestion: vi.fn(),
		acceptAll: vi.fn(() => []),
		clearSuggestions: vi.fn(),
	}),
}));

vi.mock("@/components/integrations/IntegrationPageHeader", () => ({
	IntegrationPageHeader: ({
		canManageDefinitions,
		canTestConnection,
	}: {
		canManageDefinitions: boolean;
		canTestConnection: boolean;
	}) => (
		<div>
			{canTestConnection && (
				<button type="button">Test Connection</button>
			)}
			{canManageDefinitions && <button type="button">Edit</button>}
			{canManageDefinitions && (
				<button type="button">Generate SDK</button>
			)}
		</div>
	),
}));

vi.mock("@/components/integrations/IntegrationOverview", () => ({
	IntegrationOverview: ({
		canManageDefinitions,
	}: {
		canManageDefinitions: boolean;
	}) =>
		canManageDefinitions ? (
			<button type="button">Configure Defaults</button>
		) : null,
}));

vi.mock("@/components/integrations/IntegrationMappingsTab", () => ({
	IntegrationMappingsTab: ({
		canManageMapping,
	}: {
		canManageMapping: (organizationId: string) => boolean;
	}) => (
		<div>
			{canManageMapping("provider-org") && (
				<button type="button">Edit Provider Mapping</button>
			)}
			{canManageMapping("customer-org") && (
				<button type="button">Edit Customer Mapping</button>
			)}
		</div>
	),
}));

vi.mock("@/components/integrations/ConfigOverridesTab", () => ({
	ConfigOverridesTab: () => null,
}));
vi.mock("@/components/integrations/IntegrationDefaultsDialog", () => ({
	IntegrationDefaultsDialog: () => null,
}));
vi.mock("@/components/integrations/IntegrationDeleteDialog", () => ({
	IntegrationDeleteDialog: () => null,
}));
vi.mock("@/components/integrations/CreateIntegrationDialog", () => ({
	CreateIntegrationDialog: () => null,
}));
vi.mock("@/components/integrations/GenerateSDKDialog", () => ({
	GenerateSDKDialog: () => null,
}));
vi.mock("@/components/integrations/IntegrationTestPanel", () => ({
	IntegrationTestPanel: () => null,
}));
vi.mock("@/components/oauth/CreateOAuthConnectionDialog", () => ({
	CreateOAuthConnectionDialog: () => null,
}));

import { IntegrationDetail } from "./IntegrationDetail";

function renderDetail() {
	return renderWithProviders(<IntegrationDetail />);
}

function setAuthorization({
	globalWrite = false,
	customerWrite = false,
	globalRead = false,
	customerRead = false,
}: {
	globalWrite?: boolean;
	customerWrite?: boolean;
	globalRead?: boolean;
	customerRead?: boolean;
}) {
	mockUseAuthorization.mockReturnValue({
		authorization: {
			home_organization_id: "customer-org",
			home_organization_name: "Customer",
		},
		canAt: (permission: string, target: { kind: string; id?: string }) => {
			if (permission === "integrations.readwrite") {
				return target.kind === "global"
					? globalWrite
					: customerWrite && target.id === "customer-org";
			}
			return target.kind === "global"
				? globalRead
				: customerRead && target.id === "customer-org";
		},
	});
}

describe("IntegrationDetail authorization", () => {
	beforeEach(() => {
		mockUseIntegration.mockReturnValue({
			data: {
				id: "integration-1",
				name: "Slack",
				mappings: [],
				config_schema: [],
				has_oauth_config: false,
			},
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});
		mockUseOrganizations.mockReturnValue({
			data: [
				{ id: "provider-org", name: "Provider" },
				{ id: "customer-org", name: "Customer" },
			],
			isLoading: false,
			isError: false,
			isFetching: false,
			refetch: vi.fn(),
		});
	});

	it("keeps the integration definition and mappings read-only for a reader", () => {
		setAuthorization({ globalRead: true, customerRead: true });
		renderDetail();

		expect(
			screen.getByRole("button", { name: "Test Connection" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Edit" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Generate SDK" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Configure Defaults" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Edit Customer Mapping" }),
		).not.toBeInTheDocument();
	});

	it("lets a managed-organization writer edit only its own mapping", () => {
		setAuthorization({ customerWrite: true, customerRead: true });
		renderDetail();

		expect(
			screen.queryByRole("button", { name: "Edit" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Generate SDK" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Edit Provider Mapping" }),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Edit Customer Mapping" }),
		).toBeInTheDocument();
	});

	it("offers Global definition controls to a Global writer", () => {
		setAuthorization({ globalWrite: true, globalRead: true });
		renderDetail();

		expect(
			screen.getByRole("button", { name: "Edit" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Generate SDK" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Configure Defaults" }),
		).toBeInTheDocument();
	});
});
