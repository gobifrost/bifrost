import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type {
	AccessGrant,
	AccessRow,
	PermissionCatalogEntry,
	Place,
} from "@/services/access";

const mockUseMediaQuery = vi.fn(() => false);
vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => mockUseMediaQuery(),
}));
afterEach(() => mockUseMediaQuery.mockReturnValue(false));

import { AccessMap } from "./AccessMap";

function entry(
	domain: string,
	title: string,
	area: PermissionCatalogEntry["area"],
	overrides: Partial<PermissionCatalogEntry> = {},
): PermissionCatalogEntry {
	return {
		domain,
		title,
		area,
		description: "",
		who_should_hold: "",
		actions: ["read", "readwrite"],
		privileged: [],
		scope: "per_organization",
		enforced: true,
		...overrides,
	};
}

// Sorted by area, then title, as the server sends it.
const catalog: PermissionCatalogEntry[] = [
	entry("workflows", "Workflows", "Automation"),
	entry("tables", "Tables", "Data & content"),
	entry("organizations", "Organizations", "Identity & access", {
		scope: "platform_wide",
	}),
	entry("users", "Users", "Identity & access", {
		privileged: ["users.readwrite"],
	}),
	entry("secrets", "Secrets", "Integrations & secrets", {
		privileged: ["secrets.read"],
	}),
	entry("settings", "Settings", "Platform"),
];

function grant(
	permission: string,
	scope: AccessGrant["scope"] = "per_organization",
	roleName = "Helpdesk",
): AccessGrant {
	const [domain, action] = permission.split(".");
	return {
		permission,
		domain,
		action,
		scope,
		sources: [
			{
				role_id: `role-${roleName}`,
				role_name: roleName,
				via: "additional",
			},
		],
	};
}

const home: Place = {
	kind: "home",
	organization_id: "org-1",
	organization_name: "Contoso",
	label: "Contoso (home)",
};
const fabrikam: Place = {
	kind: "organization",
	organization_id: "org-2",
	organization_name: "Fabrikam",
	label: "Fabrikam",
};
const global: Place = {
	kind: "platform",
	organization_id: null,
	organization_name: null,
	label: "Global",
};

const rows: AccessRow[] = [
	{
		place: home,
		grants: [grant("tables.read"), grant("users.readwrite")],
	},
	{ place: fabrikam, grants: [grant("workflows.execute")] },
	{ place: global, grants: [grant("organizations.read", "platform_wide")] },
];

describe("AccessMap", () => {
	it("lays out places as rows and only the areas they hold as columns", () => {
		render(<AccessMap rows={rows} catalog={catalog} />);

		const table = screen.getByRole("table", { name: "Access by place" });
		expect(
			within(table)
				.getAllByRole("columnheader")
				.map((cell) => cell.textContent),
		).toEqual([
			"Place",
			"Automation",
			"Data & content",
			"Identity & access",
		]);
		expect(
			within(table)
				.getAllByRole("rowheader")
				.map((cell) => cell.textContent),
		).toEqual(["Contoso (home)", "Fabrikam", "Global"]);
	});

	it("puts each permission in its place's row under its area", () => {
		render(<AccessMap rows={rows} catalog={catalog} />);

		const contoso = screen.getByRole("row", { name: /Contoso \(home\)/ });
		const cells = within(contoso).getAllByRole("cell");
		expect(cells[0]).toHaveTextContent("None");
		expect(
			within(cells[1]).getByRole("button", { name: /Tables/ }),
		).toHaveAttribute("data-variant", "per_organization");
		expect(
			within(cells[2]).getByRole("button", { name: /Users/ }),
		).toHaveAttribute("data-variant", "privileged");
	});

	it("marks a platform-wide permission on its chip", () => {
		render(<AccessMap rows={rows} catalog={catalog} />);

		const globalRow = screen.getByRole("row", { name: /Global/ });
		const chip = within(globalRow).getByRole("button", {
			name: /Organizations/,
		});
		expect(chip).toHaveAttribute("data-variant", "platform_wide");
		expect(within(chip).getByText("Platform-wide")).toBeInTheDocument();
	});

	it("shows a Platform Admin's wildcard as every permission across the row", async () => {
		const user = userEvent.setup();
		render(
			<AccessMap
				catalog={catalog}
				rows={[
					{
						place: { ...global, label: "All organizations" },
						grants: [
							{
								...grant(
									"*",
									"platform_wide",
									"Platform Admin",
								),
								domain: "*",
								action: "*",
							},
							grant("secrets.read", "varies", "Secrets Reader"),
						],
					},
				]}
			/>,
		);

		const row = screen.getByRole("row", { name: /All organizations/ });
		const [cell] = within(row).getAllByRole("cell");
		expect(cell).toHaveAttribute("colspan", "1");
		const wildcard = within(cell).getByRole("button", {
			name: "All permissions",
		});
		expect(wildcard).toHaveAttribute("data-variant", "platform_wide");
		expect(
			within(cell).getByRole("button", { name: /Secrets/ }),
		).toBeInTheDocument();

		await user.hover(wildcard);
		expect(await screen.findByRole("tooltip")).toHaveTextContent(
			"Platform Admin in all organizations",
		);
	});

	it("lists one record per place on narrow screens", () => {
		mockUseMediaQuery.mockReturnValue(true);
		render(<AccessMap rows={rows} catalog={catalog} />);

		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		const records = within(
			screen.getByRole("list", { name: "Access by place" }),
		).getAllByRole("listitem");
		expect(records).toHaveLength(3);
		expect(
			within(records[0]).getByRole("heading", { name: "Contoso (home)" }),
		).toBeInTheDocument();
		expect(
			within(records[0]).getByText("Data & content"),
		).toBeInTheDocument();
		expect(
			within(records[0]).getByRole("button", { name: /Tables/ }),
		).toBeInTheDocument();
		expect(
			within(records[0]).queryByText("Automation"),
		).not.toBeInTheDocument();
	});

	it("keeps every permission in one column without the catalog", () => {
		render(<AccessMap rows={rows} />);

		expect(
			screen.getAllByRole("columnheader").map((cell) => cell.textContent),
		).toEqual(["Place", "Permissions"]);
		expect(
			screen.getByRole("button", { name: /workflows/ }),
		).toBeInTheDocument();
	});

	it("says so when the person holds nothing anywhere", () => {
		render(<AccessMap rows={[]} catalog={catalog} />);

		expect(screen.getByText("No permissions yet")).toBeInTheDocument();
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
	});
});
