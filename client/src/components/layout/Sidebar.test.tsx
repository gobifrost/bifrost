import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import {
	TerminologyContext,
	mergeTerminology,
} from "@/lib/terminology";
import { Sidebar } from "./Sidebar";

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: true }),
}));

const mockUseBuilderAccess = vi.fn(() => ({
	canBuild: true,
	isLoading: false,
	solutions: [],
}));
vi.mock("@/hooks/useBuilderAccess", () => ({
	useBuilderAccess: () => mockUseBuilderAccess(),
}));

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <div aria-label="Logo" />,
}));

describe("Sidebar terminology", () => {
	it("renders branded product nouns in navigation", () => {
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
		expect(screen.getByRole("link", { name: "Characters" })).toHaveAttribute(
			"href",
			"/agents",
		);
		expect(screen.getByRole("link", { name: "Quests" })).toHaveAttribute(
			"href",
			"/forms",
		);
		expect(screen.getByRole("link", { name: "Build" })).toHaveAttribute(
			"href",
			"/build",
		);
		expect(
			screen.getByRole("link", { name: "Promotion review" }),
		).toHaveAttribute("href", "/solution-promotions");
	});

	it("hides Build when the capability probe fails closed", () => {
		mockUseBuilderAccess.mockReturnValue({
			canBuild: false,
			isLoading: false,
			solutions: [],
		});

		renderWithProviders(
			<Sidebar
				isMobileMenuOpen={false}
				setIsMobileMenuOpen={vi.fn()}
				isCollapsed={false}
			/>,
		);

		expect(screen.queryByRole("link", { name: "Build" })).not.toBeInTheDocument();
	});
});
