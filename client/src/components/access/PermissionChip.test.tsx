import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type {
	AccessGrant,
	PermissionCatalogEntry,
	Place,
} from "@/services/access";

import { PermissionChip } from "./PermissionChip";

const grant: AccessGrant = {
	permission: "tables.read",
	domain: "tables",
	action: "read",
	scope: "per_organization",
	sources: [
		{ role_id: "r1", role_name: "Helpdesk", via: "additional" },
		{ role_id: "r2", role_name: "Technician", via: "base" },
	],
};

const entry: PermissionCatalogEntry = {
	domain: "tables",
	title: "Tables",
	area: "Data & Content",
	description: "Structured data.",
	who_should_hold: "Anyone who works with data.",
	actions: ["read", "read.all", "readwrite"],
	names: {
		"tables.read": "Read Tables",
		"tables.read.all": "Read All Tables",
		"tables.readwrite": "Read and Write Tables",
	},
	privileged: [],
	scope: "per_organization",
	enforced: false,
};

const contoso: Place = {
	kind: "organization",
	organization_id: "org-1",
	organization_name: "Contoso",
	label: "Contoso",
};

describe("PermissionChip", () => {
	it("draws a per-organization grant as a plain chip in the power colours", () => {
		render(<PermissionChip grant={grant} catalogEntry={entry} />);

		const chip = screen.getByRole("button", { name: /Tables/ });
		expect(chip).toHaveAttribute("data-variant", "per_organization");
		expect(chip).toHaveClass(
			"bg-[var(--bf-power-soft)]",
			"text-[var(--bf-power)]",
		);
		expect(chip).not.toHaveClass("bg-secondary");
		expect(chip).not.toHaveClass(
			"before:bg-[image:var(--bf-bridge-vertical)]",
		);
		expect(screen.queryByText("Platform-Wide")).not.toBeInTheDocument();
	});

	it("marks a platform-wide grant with the bridge edge and its label", () => {
		render(
			<PermissionChip
				grant={{
					...grant,
					permission: "organizations.read",
					domain: "organizations",
					scope: "platform_wide",
				}}
				catalogEntry={{
					...entry,
					domain: "organizations",
					title: "Organizations",
					names: { "organizations.read": "Read Organizations" },
				}}
			/>,
		);

		const chip = screen.getByRole("button", { name: /Organizations/ });
		expect(chip).toHaveAttribute("data-variant", "platform_wide");
		expect(chip).toHaveClass("before:bg-[image:var(--bf-bridge-vertical)]");
		// The tag sits apart from the name, so the name still reads "Read Organizations".
		expect(screen.getByText("Read Organizations")).toBeInTheDocument();
		expect(
			screen.getByText("Platform-Wide").closest("[data-tag]"),
		).toHaveClass("border-l");
	});

	it("draws a varying grant solid with a dashed edge and says why in the tooltip", async () => {
		const user = userEvent.setup();
		render(
			<PermissionChip
				grant={{ ...grant, scope: "varies" }}
				catalogEntry={entry}
				place={contoso}
			/>,
		);

		const chip = screen.getByRole("button", { name: /Tables/ });
		expect(chip).toHaveAttribute("data-variant", "varies");
		expect(chip).toHaveClass(
			"before:border-dashed",
			"bg-[var(--bf-power-soft)]",
		);
		expect(chip).not.toHaveClass(
			"before:bg-[image:var(--bf-bridge-vertical)]",
		);
		expect(screen.queryByText("Platform-Wide")).not.toBeInTheDocument();

		await user.hover(chip);

		expect(await screen.findByRole("tooltip")).toHaveTextContent(
			"Some operations in this area are platform-wide and apply only through a Global placement.",
		);
	});

	it("does not add the varies note to other grants", async () => {
		const user = userEvent.setup();
		render(
			<PermissionChip
				grant={grant}
				catalogEntry={entry}
				place={contoso}
			/>,
		);

		await user.hover(screen.getByRole("button", { name: /Tables/ }));

		expect(await screen.findByRole("tooltip")).not.toHaveTextContent(
			"Some operations",
		);
	});

	it("keeps chip text at full opacity so it stays readable", () => {
		render(<PermissionChip grant={grant} catalogEntry={entry} />);

		expect(screen.getByText("Read Tables").className).not.toContain(
			"opacity",
		);
	});

	it("names the grant the way the catalog does", () => {
		render(
			<PermissionChip
				grant={{
					...grant,
					permission: "tables.read.all",
					action: "read.all",
				}}
				catalogEntry={entry}
			/>,
		);

		expect(screen.getByText("Read All Tables")).toBeInTheDocument();
		expect(screen.queryByText("tables.read.all")).not.toBeInTheDocument();
	});

	it("uses the warning tone for a privileged permission", () => {
		render(
			<PermissionChip
				grant={{
					...grant,
					permission: "secrets.read",
					domain: "secrets",
				}}
				catalogEntry={{
					...entry,
					domain: "secrets",
					title: "Secret Values",
					names: { "secrets.read": "Read Secret Values" },
					privileged: ["secrets.read"],
				}}
			/>,
		);

		const chip = screen.getByRole("button", { name: /Secret Values/ });
		expect(chip).toHaveAttribute("data-variant", "privileged");
		expect(chip).toHaveClass(
			"bg-[var(--bf-warning-soft)]",
			"text-[var(--bf-warning)]",
		);
	});

	it("shows the permission as written when no catalog entry is given", () => {
		render(<PermissionChip grant={grant} />);

		expect(
			screen.getByRole("button", { name: "tables.read" }),
		).toBeInTheDocument();
	});

	it("lists each source role and where it applies on hover", async () => {
		const user = userEvent.setup();
		render(
			<PermissionChip
				grant={grant}
				catalogEntry={entry}
				place={contoso}
			/>,
		);

		await user.hover(screen.getByRole("button", { name: /Tables/ }));

		const tooltip = await screen.findByRole("tooltip");
		expect(tooltip).toHaveTextContent("Helpdesk at Contoso");
		expect(tooltip).toHaveTextContent("Technician (base role) at Contoso");
	});

	it("opens the same tooltip on keyboard focus", async () => {
		const user = userEvent.setup();
		render(
			<PermissionChip
				grant={grant}
				catalogEntry={entry}
				place={contoso}
			/>,
		);

		await user.tab();

		expect(await screen.findByRole("tooltip")).toHaveTextContent(
			"Helpdesk at Contoso",
		);
	});

	it("words each kind of place in the tooltip", async () => {
		const user = userEvent.setup();
		render(
			<PermissionChip
				grant={{ ...grant, sources: [grant.sources[0]] }}
				catalogEntry={entry}
				place={{
					kind: "managed_organizations",
					organization_id: null,
					organization_name: null,
					label: "All Customer Organizations",
				}}
			/>,
		);

		await user.hover(screen.getByRole("button", { name: /Tables/ }));

		expect(await screen.findByRole("tooltip")).toHaveTextContent(
			"Helpdesk in all customer organizations",
		);
	});
});
