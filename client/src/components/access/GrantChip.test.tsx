import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { PermissionCatalogEntry } from "@/services/access";

import { GrantChip } from "./GrantChip";

const tables: PermissionCatalogEntry = {
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

describe("GrantChip", () => {
	it("names the area and says the action in plain words", () => {
		render(<GrantChip permission="tables.readwrite" entry={tables} />);

		const chip = screen.getByText("Tables").parentElement!;
		expect(screen.getByText("manage").parentElement).toBe(chip);
		expect(chip).toHaveAttribute("data-variant", "per_organization");
		expect(chip).toHaveClass("bg-[var(--bf-power-soft)]");
	});

	it("keeps the meaning of .all", () => {
		render(<GrantChip permission="tables.read.all" entry={tables} />);

		expect(screen.getByText("view all")).toBeInTheDocument();
	});

	it("uses the warning tone for a privileged permission", () => {
		render(
			<GrantChip
				permission="tables.readwrite"
				entry={{ ...tables, privileged: ["tables.readwrite"] }}
			/>,
		);

		const chip = screen.getByText("Tables").parentElement!;
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

		expect(screen.getByText("Tables").parentElement).toHaveClass(
			"before:bg-[image:var(--bf-bridge-vertical)]",
		);
		expect(screen.getByText("Platform-wide")).toBeInTheDocument();
	});

	it("shows Platform Admin's wildcard as every permission, privileged", () => {
		render(<GrantChip permission="*" entry={undefined} />);

		expect(screen.getByText("Every permission")).toHaveAttribute(
			"data-variant",
			"privileged",
		);
	});

	it("falls back to the domain when the catalog has no entry", () => {
		render(<GrantChip permission="tables.read" entry={undefined} />);

		expect(screen.getByText("tables")).toBeInTheDocument();
	});
});
