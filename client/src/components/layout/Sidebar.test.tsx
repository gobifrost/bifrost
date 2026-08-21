import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { TerminologyContext, mergeTerminology } from "@/lib/terminology";
import { Sidebar } from "./Sidebar";

let selectedCapabilities = new Set<string>();
let canAccessBuilder = true;

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		isPlatformAdmin: true,
		hasCapability: (capability: string) => selectedCapabilities.has(capability),
	}),
}));

vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => ({
		hasSelectedCapability: (capability: string) =>
			selectedCapabilities.has(capability),
		selectedTarget: { kind: "platform" },
	}),
}));

vi.mock("@/hooks/useBuilderAccess", () => ({
	useBuilderAccess: () => ({ canAccessBuilder }),
}));

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <div aria-label="Logo" />,
}));

describe("Sidebar terminology", () => {
	it("renders branded product nouns in navigation", () => {
		selectedCapabilities = new Set([
			"agents.read",
			"apps.read",
			"forms.read",
			"workflows.read",
		]);
		canAccessBuilder = true;
		const terminology = mergeTerminology({
			app: { singular: "Game", plural: "Games" },
			agent: { singular: "Character", plural: "Characters" },
			form: { singular: "Quest", plural: "Quests" },
		});

		renderWithProviders(
			<TerminologyContext.Provider value={terminology}>
				<Sidebar
					isMobileMenuOpen={false}
					setIsMobileMenuOpen={vi.fn()}
					isCollapsed={false}
				/>
			</TerminologyContext.Provider>,
		);

		expect(screen.getByRole("link", { name: "Games" })).toHaveAttribute(
			"href",
			"/apps",
		);
		expect(
			screen.getByRole("link", { name: "Characters" }),
		).toHaveAttribute("href", "/agents");
		expect(screen.getByRole("link", { name: "Quests" })).toHaveAttribute(
			"href",
			"/forms",
		);
	});

	it("does not show admin surfaces for the default Builder capability set", () => {
		selectedCapabilities = new Set([
			"agents.execute",
			"agents.read",
			"apps.read",
			"builder.execute",
			"builder.read",
			"executions.read",
			"forms.read",
			"knowledge.read",
			"managedfiles.read",
			"solutions.build.execute",
			"solutions.deploy.execute",
			"solutions.read",
			"solutions.readwrite",
			"tabledocuments.read",
			"tables.read",
			"workflows.execute",
			"workflows.read",
		]);
		canAccessBuilder = true;

		renderWithProviders(
			<Sidebar
				isMobileMenuOpen={false}
				setIsMobileMenuOpen={vi.fn()}
				isCollapsed={false}
			/>,
		);

		expect(screen.getByRole("link", { name: "Build" })).toBeInTheDocument();
		expect(screen.queryByRole("link", { name: "Organizations" })).toBeNull();
		expect(screen.queryByRole("link", { name: "Users" })).toBeNull();
		expect(screen.queryByRole("link", { name: "Roles" })).toBeNull();
	});
});
