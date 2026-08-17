import { describe, expect, it } from "vitest";
import {
	buildApplicationOrganizationUpdateBody,
	buildApplicationUpdateBody,
} from "./AppInfoDialogPayload";

const values = {
	name: "Example App",
	slug: "example-app",
	description: "Existing description",
	organization_id: "org-1",
	access_level: "authenticated" as const,
	role_ids: [],
};

describe("AppInfoDialog update payload", () => {
	it("sends explicit null when an administrator moves an App global", () => {
		expect(
			buildApplicationUpdateBody(
				{ ...values, organization_id: null },
				true,
			),
		).toEqual({
			name: "Example App",
			slug: "example-app",
			description: "Existing description",
			access_level: "authenticated",
			role_ids: [],
			organization_id: null,
		});
	});

	it("omits organization_id for non-administrator updates", () => {
		const body = buildApplicationUpdateBody(values, false);

		expect(body).not.toHaveProperty("organization_id");
	});

	it("uses the canonical organization field for administrative drag-and-drop", () => {
		expect(buildApplicationOrganizationUpdateBody(null)).toEqual({
			organization_id: null,
		});
		expect(buildApplicationOrganizationUpdateBody("org-1")).toEqual({
			organization_id: "org-1",
		});
	});
});
