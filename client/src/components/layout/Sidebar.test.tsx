import userEvent from "@testing-library/user-event";
import { within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, renderWithProviders, screen, waitFor } from "@/test-utils";
import { TerminologyContext, mergeTerminology } from "@/lib/terminology";
import type { HomeCollection } from "@/services/home";
import {
	meetsRequirement,
	type AuthorizationSummary,
	type PermissionRequirement,
} from "@/lib/authorization";
import { Sidebar } from "./Sidebar";

const state = vi.hoisted(() => ({
	isPlatformAdmin: true,
	authorization: undefined as AuthorizationSummary | undefined,
	home: {
		data: undefined as
			{ resources: []; collections: HomeCollection[] } | undefined,
	},
	useQuery: vi.fn(),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: state.isPlatformAdmin }),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		meets: (requirement: PermissionRequirement) =>
			meetsRequirement(state.authorization, requirement),
	}),
}));

function summary(
	isPlatformAdmin: boolean,
	permissions: string[] = [],
): AuthorizationSummary {
	return {
		is_platform_admin: isPlatformAdmin,
		home_organization_id: "00000000-0000-0000-0000-000000000002",
		provider_organization_id: "00000000-0000-0000-0000-000000000002",
		base_role: {
			id: "r",
			name: isPlatformAdmin ? "Platform Admin" : "User",
		},
		grants: permissions.map((permission) => ({
			permission,
			boundary: { kind: "managed_organizations", organization_id: null },
		})),
	};
}

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => <div aria-label="Logo" />,
}));

vi.mock("@/lib/api-client", () => ({
	$api: {
		useQuery: (...args: unknown[]) => state.useQuery(...args),
	},
}));

const collections: HomeCollection[] = [
	{
		id: "col-1",
		name: "Operations",
		description: "Daily work",
		icon: "briefcase-business",
		shared: false,
		organization_id: null,
		organization_name: "",
		resource_keys: ["app:dispatch"],
		can_edit: true,
	},
	{
		id: "col-2",
		name: "Escalations",
		description: "Higher-touch work",
		icon: "shield-alert",
		shared: true,
		organization_id: "org-1",
		organization_name: "Acme",
		resource_keys: ["form:escalation"],
		can_edit: false,
	},
];

beforeEach(() => {
	state.isPlatformAdmin = true;
	state.authorization = summary(true);
	state.home = { data: { resources: [], collections } };
	state.useQuery.mockReset();
	state.useQuery.mockReturnValue(state.home);
});

describe("Sidebar terminology", () => {
	it("renders branded product nouns in navigation", async () => {
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
		expect(
			screen.queryByRole("link", { name: "Dashboard" }),
		).not.toBeInTheDocument();
	});

	it("preserves the desktop navigation scroll position across route changes", async () => {
		const { user } = renderWithProviders(
			<Sidebar
				isMobileMenuOpen={false}
				setIsMobileMenuOpen={vi.fn()}
				isCollapsed={false}
			/>,
		);

		const nav = screen.getByRole("navigation", {
			name: "Primary navigation",
		});
		Object.defineProperty(nav, "scrollTop", {
			value: 180,
			writable: true,
			configurable: true,
		});
		fireEvent.scroll(nav);

		nav.scrollTop = 0;
		await user.click(screen.getByRole("link", { name: "Users" }));

		await waitFor(() => expect(nav.scrollTop).toBe(180));
	});
});

describe("Sidebar structure", () => {
	it("shows an operator the identity pages their roles grant, not Roles", () => {
		state.isPlatformAdmin = false;
		state.authorization = summary(false, [
			"users.read",
			"organizations.read",
		]);

		renderWithProviders(
			<Sidebar
				isMobileMenuOpen={false}
				setIsMobileMenuOpen={vi.fn()}
				isCollapsed={false}
			/>,
		);

		expect(
			screen.getByRole("heading", { name: "Platform" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("link", { name: "Organizations" }),
		).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: "Roles" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: "Settings" }),
		).not.toBeInTheDocument();
	});

	it("shows Access Checks only to holders of roleassignments.read", () => {
		state.isPlatformAdmin = false;
		state.authorization = summary(false, ["users.read"]);
		const { unmount } = renderWithProviders(
			<Sidebar
				isMobileMenuOpen={false}
				setIsMobileMenuOpen={vi.fn()}
				isCollapsed={false}
			/>,
		);
		expect(
			screen.queryByRole("link", { name: "Access Checks" }),
		).not.toBeInTheDocument();
		unmount();

		state.authorization = summary(false, ["roleassignments.read"]);
		renderWithProviders(
			<Sidebar
				isMobileMenuOpen={false}
				setIsMobileMenuOpen={vi.fn()}
				isCollapsed={false}
			/>,
		);
		expect(
			screen.getByRole("link", { name: "Access Checks" }),
		).toHaveAttribute("href", "/audit/access-checks");
		expect(
			screen.queryByRole("link", { name: "Audit Log" }),
		).not.toBeInTheDocument();
	});

	it("marks only Access Checks current on its page, not the Audit Log", () => {
		renderWithProviders(
			<Sidebar
				isMobileMenuOpen={false}
				setIsMobileMenuOpen={vi.fn()}
				isCollapsed={false}
			/>,
			{ initialEntries: ["/audit/access-checks"] },
		);

		expect(
			screen.getByRole("link", { name: "Access Checks" }),
		).toHaveAttribute("aria-current", "page");
		expect(
			screen.getByRole("link", { name: "Audit Log" }),
		).not.toHaveAttribute("aria-current");
	});

	it("shows a platform admin the whole Platform section", () => {
		renderWithProviders(
			<Sidebar
				isMobileMenuOpen={false}
				setIsMobileMenuOpen={vi.fn()}
				isCollapsed={false}
			/>,
		);

		for (const name of ["Organizations", "Users", "Roles", "Settings"]) {
			expect(screen.getByRole("link", { name })).toBeInTheDocument();
		}
	});

	it("keeps launch destinations visible for ordinary users and hides admin management", () => {
		state.isPlatformAdmin = false;
		state.authorization = summary(false);

		renderWithProviders(
			<Sidebar
				isMobileMenuOpen={false}
				setIsMobileMenuOpen={vi.fn()}
				isCollapsed={false}
			/>,
		);

		expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
		expect(screen.getByRole("link", { name: "Chat" })).toBeInTheDocument();
		expect(
			screen.getByRole("link", { name: "History" }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: "Operations" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: "Apps" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: "Forms" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: "Agents" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: "Dashboard" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("heading", { name: "Platform" }),
		).not.toBeInTheDocument();
	});

	it.each(["/", "/history", "/workflows"])(
		"keeps collections out of desktop and mobile navigation on %s",
		(route) => {
			renderWithProviders(
				<Sidebar
					isMobileMenuOpen
					setIsMobileMenuOpen={vi.fn()}
					isCollapsed={false}
				/>,
				{ initialEntries: [route] },
			);
			expect(state.useQuery).not.toHaveBeenCalledWith("get", "/api/home");
			expect(
				screen.queryByRole("heading", { name: "Collections" }),
			).not.toBeInTheDocument();
			for (const name of [
				"Operations",
				"Escalations",
				"New collection",
			]) {
				expect(
					screen.queryByRole("link", { name }),
				).not.toBeInTheDocument();
			}
		},
	);
});

describe("Sidebar mobile navigation", () => {
	it("exposes a named modal and lets Escape dismiss it", async () => {
		const close = vi.fn();
		renderWithProviders(
			<Sidebar
				isMobileMenuOpen
				setIsMobileMenuOpen={close}
				isCollapsed={false}
			/>,
		);
		const dialog = screen.getByRole("dialog", { name: "Navigation" });
		expect(
			within(dialog).getByRole("navigation", {
				name: "Primary navigation",
			}),
		).toBeInTheDocument();
		await userEvent.setup().keyboard("{Escape}");
		expect(close).toHaveBeenCalledWith(false);
	});
	it("closes after a mobile destination is selected", async () => {
		const close = vi.fn();
		renderWithProviders(
			<Sidebar
				isMobileMenuOpen
				setIsMobileMenuOpen={close}
				isCollapsed={false}
			/>,
		);
		const dialog = screen.getByRole("dialog", { name: "Navigation" });
		await userEvent
			.setup()
			.click(within(dialog).getByRole("link", { name: "Workflows" }));
		expect(close).toHaveBeenCalledWith(false);
	});
});
