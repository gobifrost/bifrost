/**
 * Component tests for IntegrationTestPanel.
 *
 * Covers the result-success/failure branches + Test button wiring. The
 * OrganizationSelect child is mocked out because it fetches organizations
 * on mount.
 */

import { describe, it, expect, vi } from "vitest";
import { renderWithProviders, screen, fireEvent } from "@/test-utils";

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => <div data-marker="org-select" />,
}));

import { IntegrationTestPanel } from "./IntegrationTestPanel";

function renderPanel(
	overrides: Partial<Parameters<typeof IntegrationTestPanel>[0]> = {},
) {
	const onOpenChange = vi.fn();
	const onTestOrgIdChange = vi.fn();
	const onTestEndpointChange = vi.fn();
	const onClearResult = vi.fn();
	const onTest = vi.fn();
	const utils = renderWithProviders(
		<IntegrationTestPanel
			open
			onOpenChange={onOpenChange}
			testOrgId={null}
			onTestOrgIdChange={onTestOrgIdChange}
			testEndpoint=""
			onTestEndpointChange={onTestEndpointChange}
			testResult={null}
			onClearResult={onClearResult}
			onTest={onTest}
			isTestPending={false}
			canTestAt={() => true}
			testableOrganizations={[]}
			{...overrides}
		/>,
	);
	return {
		...utils,
		onOpenChange,
		onTestOrgIdChange,
		onTestEndpointChange,
		onClearResult,
		onTest,
	};
}

describe("IntegrationTestPanel", () => {
	it("calls onTest when the Test button is clicked", async () => {
		const { user, onTest } = renderPanel();
		await user.click(screen.getByRole("button", { name: /^test$/i }));
		expect(onTest).toHaveBeenCalledTimes(1);
	});

	it("fires endpoint-change and clears the cached result on typing", () => {
		const { onTestEndpointChange, onClearResult } = renderPanel();
		fireEvent.change(screen.getByLabelText(/endpoint/i), {
			target: { value: "/api/users" },
		});
		expect(onTestEndpointChange).toHaveBeenLastCalledWith("/api/users");
		expect(onClearResult).toHaveBeenCalled();
	});

	it("renders a success panel when testResult.success is true", () => {
		renderPanel({
			testResult: {
				success: true,
				message: "Connection OK",
				method_called: "ping",
				duration_ms: 12,
			} as Parameters<typeof IntegrationTestPanel>[0]["testResult"],
		});
		expect(screen.getByText("Connection OK")).toBeInTheDocument();
		expect(screen.getByText(/ping\(\)/)).toBeInTheDocument();
	});

	it("renders the error detail when testResult.success is false", () => {
		renderPanel({
			testResult: {
				success: false,
				message: "Request failed",
				error_details: "403 Forbidden",
			} as Parameters<typeof IntegrationTestPanel>[0]["testResult"],
		});
		expect(screen.getByText("Request failed")).toBeInTheDocument();
		expect(screen.getByText("403 Forbidden")).toBeInTheDocument();
	});

	it("disables the Test button and shows 'Testing...' while pending", () => {
		renderPanel({ isTestPending: true });
		expect(screen.getByRole("button", { name: /testing/i })).toBeDisabled();
	});

	it("offers only an organization-scoped test when Global is not readable", () => {
		renderPanel({ canTestAt: () => false, testOrgId: "customer-org" });
		expect(
			screen.getByText(
				"Choose an organization to test with its merged configuration and OAuth.",
			),
		).toBeVisible();
		expect(screen.getByRole("button", { name: /^test$/i })).toBeDisabled();
	});
});
