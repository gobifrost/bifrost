import { describe, expect, it } from "vitest";

import type { AccessStep } from "@/services/access";

import { stepSentence, stepTitle, type TraceNames } from "./access-trace";

const names: TraceNames = {
	role: (id) => ({ "role-helpdesk": "Helpdesk" })[id],
	organization: (id) => ({ "org-1": "Contoso", "org-2": "Fabrikam" })[id],
	permission: (permission) =>
		({
			"tables.read": "Read Tables",
			"tables.readwrite": "Read and Write Tables",
		})[permission] ?? permission,
};

function step(
	key: string,
	reason: string,
	facts: AccessStep["facts"] = {},
	status: AccessStep["status"] = "passed",
): AccessStep {
	return { key, label: key, status, reason, facts };
}

const sentence = (s: AccessStep, runUserId = "user-1") =>
	stepSentence(s, names, runUserId);

describe("stepTitle", () => {
	it("title-cases the server's label, keeping short words lower case", () => {
		expect(stepTitle("Target in reach")).toBe("Target in Reach");
		expect(stepTitle("Run as another user")).toBe("Run as Another User");
		expect(stepTitle("Workflow powers")).toBe("Workflow Powers");
	});
});

describe("stepSentence", () => {
	it("says whether a person can start the workflow", () => {
		expect(sentence(step("workflow_access", "access"))).toBe(
			"They can start this workflow.",
		);
		expect(sentence(step("workflow_access", "no_access"))).toBe(
			"They can't start this workflow.",
		);
		expect(sentence(step("workflow_access", "unattended"))).toBe(
			"An identity runs it unattended, so no one starts it.",
		);
	});

	it("names who the run user is", () => {
		expect(
			sentence(step("run_user", "person", { is_platform_admin: false })),
		).toBe("Runs as this person.");
		expect(
			sentence(step("run_user", "identity", { is_platform_admin: true })),
		).toBe("Runs as this identity, a Platform Admin.");
	});

	it("explains the workflow's powers", () => {
		expect(sentence(step("powers", "no_workflow"))).toBe(
			"No workflow: only their own roles apply.",
		);
		expect(sentence(step("powers", "full", { grants: [] }))).toBe(
			"The workflow has Full powers: every permission, secrets included.",
		);
		expect(
			sentence(step("powers", "restricted", { grants: [{}, {}] })),
		).toBe(
			"The workflow is Restricted: 2 grants add to the run user's roles.",
		);
	});

	it("says how the target is reached, naming roles and organizations", () => {
		const target = (
			reason: string,
			organizationId: string | null = "org-1",
		) =>
			sentence(
				step("target", reason, { organization_id: organizationId }),
			);

		expect(target("global", null)).toBe("Global is in everyone's reach.");
		expect(target("platform_admin")).toBe(
			"A Platform Admin reaches every organization.",
		);
		expect(target("home")).toBe("Contoso is their home organization.");
		expect(target("role:role-helpdesk@organization")).toBe(
			"Helpdesk is placed on Contoso.",
		);
		expect(target("role:role-unknown@organization")).toBe(
			"A role placed there reaches Contoso.",
		);
		expect(
			target("role:role-helpdesk@managed_organizations", "org-2"),
		).toBe("Helpdesk reaches every customer organization.");
		expect(target("outside", "org-2")).toBe(
			"Fabrikam is outside their reach.",
		);
		expect(target("outside", "org-unknown")).toBe(
			"This organization is outside their reach.",
		);
	});

	it("says which role grants the permission, by name", () => {
		const permission = (reason: string) =>
			sentence(step("permission", reason, { permission: "tables.read" }));

		expect(permission("full")).toBe(
			"Full powers include every permission.",
		);
		expect(permission("platform_admin")).toBe(
			"A Platform Admin holds All Permissions.",
		);
		expect(permission("base_role:tables.read")).toBe(
			"Their base role grants Read Tables.",
		);
		expect(permission("role:role-helpdesk:tables.read@organization")).toBe(
			"Helpdesk grants Read Tables there.",
		);
		expect(permission("role:role-unknown:tables.read@organization")).toBe(
			"A role placed there grants Read Tables.",
		);
		// A Restricted workflow's grants are judged as a role named by the run user's id.
		expect(permission("role:user-1:tables.read@organization")).toBe(
			"The workflow's grants include Read Tables.",
		);
		expect(permission("denied:missing:tables.readwrite")).toBe(
			"No role grants Read and Write Tables there.",
		);
		expect(permission("public")).toBe("Anyone may do this.");
		expect(permission("signed_in")).toBe(
			"Anyone signed in may do this in their home organization.",
		);
		expect(permission("denied:operation_confined_to_own_org")).toBe(
			"This operation works only in their home organization.",
		);
		expect(permission("denied:beyond_own_org_requires_role")).toBe(
			"Outside their home organization, this needs a role placed there.",
		);
		expect(permission("no_access_list_entry")).toBe(
			"The access list doesn't name this operation.",
		);
	});

	it("says nothing for a step that wasn't reached", () => {
		expect(sentence(step("permission", "", {}, "not_reached"))).toBe("");
	});

	it("shows a reason it doesn't know as written", () => {
		expect(sentence(step("permission", "denied:something_new"))).toBe(
			"denied:something_new",
		);
	});
});
