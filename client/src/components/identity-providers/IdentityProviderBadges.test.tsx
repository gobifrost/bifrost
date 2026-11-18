import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";

import {
	IdentitySourceLabel,
	InactiveStatusBadge,
} from "./IdentityProviderBadges";

const source = {
	identity_provider_id: "4f0c7a0e-2a3b-4c5d-8e9f-0a1b2c3d4e5f",
	name: "Contoso Entra ID",
	vendor: "entra" as const,
	access_pending_since: null,
	activating: false,
};

describe("IdentitySourceLabel", () => {
	it("names a user Bifrost manages Native", () => {
		renderWithProviders(<IdentitySourceLabel user={{ identity_source: null }} />);

		expect(screen.getByText("Native")).toBeInTheDocument();
	});

	it("names the connection that manages a user", () => {
		renderWithProviders(<IdentitySourceLabel user={{ identity_source: source }} />);

		expect(screen.getByText("Contoso Entra ID")).toBeInTheDocument();
		expect(screen.queryByText("Access Pending")).not.toBeInTheDocument();
	});

	it("says when access from the identity provider is waiting to apply", () => {
		renderWithProviders(
			<IdentitySourceLabel
				user={{
					identity_source: {
						...source,
						access_pending_since: "2026-11-17T15:00:00Z",
					},
				}}
			/>,
		);

		expect(screen.getByText("Access Pending")).toHaveAttribute(
			"title",
			"Changes from Contoso Entra ID apply within about two minutes",
		);
	});
});

describe("InactiveStatusBadge", () => {
	it("calls an inactive user Inactive", () => {
		renderWithProviders(<InactiveStatusBadge user={{ identity_source: source }} />);

		expect(screen.getByText("Inactive")).toBeInTheDocument();
	});

	it("calls a new hire the identity provider is activating Activating", () => {
		renderWithProviders(
			<InactiveStatusBadge
				user={{
					identity_source: {
						...source,
						access_pending_since: "2026-11-17T15:00:00Z",
						activating: true,
					},
				}}
			/>,
		);

		expect(screen.getByText("Activating")).toHaveAttribute(
			"title",
			"Contoso Entra ID added this user; Bifrost activates them within about two minutes",
		);
		expect(screen.queryByText("Inactive")).not.toBeInTheDocument();
	});
});
