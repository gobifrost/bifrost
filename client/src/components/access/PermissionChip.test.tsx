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
	area: "Data & content",
	description: "Structured data.",
	who_should_hold: "Anyone who works with data.",
	actions: ["read", "readwrite"],
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
	it("draws a per-organization grant as a plain chip", () => {
		render(<PermissionChip grant={grant} catalogEntry={entry} />);

		const chip = screen.getByRole("button", { name: /Tables/ });
		expect(chip).toHaveAttribute("data-variant", "per_organization");
		expect(chip).not.toHaveClass(
			"before:bg-[image:var(--bf-bridge-vertical)]",
		);
		expect(screen.queryByText("Platform-wide")).not.toBeInTheDocument();
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
				}}
			/>,
		);

		const chip = screen.getByRole("button", { name: /Organizations/ });
		expect(chip).toHaveAttribute("data-variant", "platform_wide");
		expect(chip).toHaveClass("before:bg-[image:var(--bf-bridge-vertical)]");
		expect(screen.getByText("Platform-wide")).toBeInTheDocument();
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
					title: "Secrets",
					privileged: ["secrets.read"],
				}}
			/>,
		);

		const chip = screen.getByRole("button", { name: /Secrets/ });
		expect(chip).toHaveAttribute("data-variant", "privileged");
		expect(chip).toHaveClass(
			"bg-[var(--bf-warning-soft)]",
			"text-[var(--bf-warning)]",
		);
	});

	it("falls back to the domain when no catalog entry is given", () => {
		render(<PermissionChip grant={grant} />);

		expect(
			screen.getByRole("button", { name: /tables/ }),
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
					label: "All customer organizations",
				}}
			/>,
		);

		await user.hover(screen.getByRole("button", { name: /Tables/ }));

		expect(await screen.findByRole("tooltip")).toHaveTextContent(
			"Helpdesk in all customer organizations",
		);
	});
});
