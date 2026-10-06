import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { PermissionCatalogEntry } from "@/services/access";

import { GrantChip } from "./GrantChip";

const tables: PermissionCatalogEntry = {
	domain: "tables",
	title: "Tables",
	area: "Data & Content",
	description: "Structured data.",
	who_should_hold: "Anyone who works with data.",
	actions: ["read", "readwrite"],
	names: {
		"tables.read": "Read Tables",
		"tables.readwrite": "Read and Write Tables",
	},
	privileged: [],
	scope: "per_organization",
	enforced: false,
};

describe("GrantChip", () => {
	it("shows the permission's display name", () => {
		render(<GrantChip permission="tables.readwrite" entry={tables} />);

		const chip = screen.getByText("Read and Write Tables").parentElement!;
		expect(chip).toHaveAttribute("data-variant", "per_organization");
		expect(chip).toHaveClass("bg-[var(--bf-power-soft)]");
	});

	it("uses the warning tone for a privileged permission", () => {
		render(
			<GrantChip
				permission="tables.readwrite"
				entry={{ ...tables, privileged: ["tables.readwrite"] }}
			/>,
		);

		const chip = screen.getByText("Read and Write Tables").parentElement!;
		expect(chip).toHaveAttribute("data-variant", "privileged");
		expect(chip).not.toHaveClass("bg-[var(--bf-power-soft)]");
	});

	it("marks a platform-wide permission with the bridge edge and its label", () => {
		render(
			<GrantChip
				permission="tables.read"
				entry={{ ...tables, scope: "platform_wide" }}
			/>,
		);

		expect(screen.getByText("Read Tables").parentElement).toHaveClass(
			"before:bg-[image:var(--bf-bridge-vertical)]",
		);
		expect(screen.getByText("Platform-Wide")).toBeInTheDocument();
	});

	it("shows Platform Admin's wildcard as All Permissions, privileged", () => {
		render(<GrantChip permission="*" entry={undefined} />);

		expect(screen.getByText("All Permissions")).toHaveAttribute(
			"data-variant",
			"privileged",
		);
	});

	it("shows the permission as written when the catalog has no entry", () => {
		render(<GrantChip permission="tables.read" entry={undefined} />);

		expect(screen.getByText("tables.read")).toBeInTheDocument();
	});
});
