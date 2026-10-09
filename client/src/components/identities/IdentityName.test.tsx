import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { IdentityName, IdentityOrganizationChip } from "./IdentityName";

describe("IdentityName", () => {
	it("shows the identity's glyph, name and organization", () => {
		render(
			<IdentityName
				identity={{
					name: "Default Identity",
					organization_name: "Contoso",
				}}
			/>,
		);

		expect(
			screen.getByRole("img", { name: "Identity" }),
		).toBeInTheDocument();
		expect(screen.getByText("Default Identity")).toBeInTheDocument();
		expect(screen.getByLabelText("Organization")).toHaveTextContent(
			"Contoso",
		);
	});

	it("places an identity of no organization in Global", () => {
		render(
			<IdentityName
				identity={{ name: "Default Identity", organization_name: null }}
			/>,
		);

		expect(screen.getByLabelText("Organization")).toHaveTextContent(
			"Global",
		);
	});
});

describe("IdentityOrganizationChip", () => {
	it("tints Global in the reach colours", () => {
		const { rerender } = render(
			<IdentityOrganizationChip organizationName={null} />,
		);
		expect(screen.getByLabelText("Organization")).toHaveAttribute(
			"data-global",
			"true",
		);

		rerender(<IdentityOrganizationChip organizationName="Fabrikam" />);
		expect(screen.getByLabelText("Organization")).not.toHaveAttribute(
			"data-global",
		);
	});
});
