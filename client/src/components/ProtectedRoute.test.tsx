import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const useAuth = vi.fn();
const useAuthorizationBoundary = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => useAuth() }));
vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => useAuthorizationBoundary(),
}));

import { ProtectedRoute } from "./ProtectedRoute";

describe("ProtectedRoute", () => {
	it("authorizes a capability against the context the person selected", () => {
		useAuth.mockReturnValue({
			isPlatformAdmin: false,
			isOrgUser: true,
			isLoading: false,
			hasRole: () => false,
			logout: vi.fn(),
		});
		useAuthorizationBoundary.mockReturnValue({
			hasSelectedCapability: (capability: string) =>
				capability === "solutions.read",
			isLoading: false,
			selectedTarget: { kind: "organization" },
		});

		renderWithProviders(
			<ProtectedRoute requireCapability="solutions.read">
				<p>Solution library</p>
			</ProtectedRoute>,
		);

		expect(screen.getByText("Solution library")).toBeVisible();
	});

	it("does not reuse a capability assigned in some other context", () => {
		useAuth.mockReturnValue({
			isPlatformAdmin: false,
			isOrgUser: true,
			isLoading: false,
			hasRole: () => false,
			logout: vi.fn(),
		});
		useAuthorizationBoundary.mockReturnValue({
			hasSelectedCapability: () => false,
			isLoading: false,
			selectedTarget: { kind: "organization" },
		});

		renderWithProviders(
			<ProtectedRoute requireCapability="solutions.publish.read">
				<p>Promotion review</p>
			</ProtectedRoute>,
		);

		expect(screen.getByText("Access Denied")).toBeVisible();
		expect(screen.queryByText("Promotion review")).not.toBeInTheDocument();
	});

	it("requires an explicit Platform working context for global operations", () => {
		useAuth.mockReturnValue({
			isPlatformAdmin: true,
			isOrgUser: false,
			isLoading: false,
			hasRole: () => false,
			logout: vi.fn(),
		});
		useAuthorizationBoundary.mockReturnValue({
			hasSelectedCapability: () => true,
			isLoading: false,
			selectedTarget: { kind: "organization" },
		});

		renderWithProviders(
			<ProtectedRoute
				requireCapability="audit.read"
				requireBoundaryKind="platform"
			>
				<p>Audit log</p>
			</ProtectedRoute>,
		);

		expect(screen.getByText("Choose another working context")).toBeVisible();
		expect(
			screen.getByText(/select global from working in/i),
		).toBeVisible();
		expect(screen.queryByText("Audit log")).not.toBeInTheDocument();
	});
});
