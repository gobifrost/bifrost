import { describe, expect, it } from "vitest";

import type { AccessStep, AccessTrace } from "@/services/access";

import {
	changeSentence,
	changedStepKeys,
	checkKindTitle,
	nowUnavailableSentence,
	stepSentence,
	stepTitle,
	stoppedStep,
	storedTrace,
	type TraceNames,
} from "./access-trace";

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

function trace(
	outcome: AccessTrace["outcome"],
	...steps: AccessStep[]
): AccessTrace {
	return { outcome, enforced: false, steps };
}

const allowedThen = trace(
	"success",
	step("run_user", "person"),
	step("target", "home"),
	step("permission", "base_role:tables.read"),
);
const stoppedNow = trace(
	"failure",
	{ ...step("run_user", "person"), label: "Run user" },
	{ ...step("target", "outside", {}, "stopped"), label: "Target in reach" },
	step("permission", "", {}, "not_reached"),
);

const grantA = {
	boundary: { kind: "organization", organization_id: "org-1" },
	permission: "tables.read",
};
const grantB = {
	boundary: { kind: "organization", organization_id: "org-2" },
	permission: "tables.readwrite",
};
function restricted(...grants: object[]): AccessTrace {
	return trace(
		"success",
		step("run_user", "identity"),
		step("powers", "restricted", { grants }),
		step("permission", "base_role:tables.read"),
	);
}

describe("changedStepKeys", () => {
	it("names the steps whose status or reason differ", () => {
		expect([...changedStepKeys(allowedThen, stoppedNow)]).toEqual([
			"target",
			"permission",
		]);
	});

	it("is empty when nothing changed", () => {
		expect(changedStepKeys(allowedThen, allowedThen).size).toBe(0);
	});

	it("names a step whose facts changed under the same status and reason", () => {
		expect([
			...changedStepKeys(restricted(grantA, grantB), restricted(grantA)),
		]).toEqual(["powers"]);
	});

	it("ignores the order of keys and list items in facts", () => {
		const reordered = restricted(grantB, {
			permission: grantA.permission,
			boundary: grantA.boundary,
		});
		expect(
			changedStepKeys(restricted(grantA, grantB), reordered).size,
		).toBe(0);
	});
});

describe("changeSentence", () => {
	it("says an allowed check is still allowed when only its inputs changed", () => {
		expect(
			changeSentence(restricted(grantA, grantB), restricted(grantA)),
		).toBe(
			"This check would still be allowed now; the marked steps changed.",
		);
	});

	it("says nothing changed", () => {
		expect(changeSentence(allowedThen, allowedThen)).toBe(
			"Nothing changed since then.",
		);
	});

	it("names the step the check would now stop at", () => {
		expect(changeSentence(allowedThen, stoppedNow)).toBe(
			"Now this check would stop at Target in Reach.",
		);
	});

	it("says the check would now be allowed", () => {
		expect(changeSentence(stoppedNow, allowedThen)).toBe(
			"Now this check would be allowed.",
		);
	});
});

describe("stoppedStep", () => {
	it("finds the step that would stop the check", () => {
		expect(stoppedStep(stoppedNow)?.key).toBe("target");
		expect(stoppedStep(allowedThen)).toBeUndefined();
	});
});

describe("storedTrace", () => {
	it("reads the trace an access check stored", () => {
		expect(storedTrace({ trace: stoppedNow })).toEqual(stoppedNow);
	});

	it("is undefined without a stored trace", () => {
		expect(storedTrace(null)).toBeUndefined();
		expect(storedTrace({ trace: "nope" })).toBeUndefined();
		expect(storedTrace({ trace: { outcome: "failure" } })).toBeUndefined();
	});
});

describe("nowUnavailableSentence", () => {
	it("explains why a check can't be judged again", () => {
		expect(nowUnavailableSentence("workflow_missing")).toBe(
			"The workflow no longer exists, so this check can't be tested again.",
		);
		expect(nowUnavailableSentence("run_user_missing")).toBe(
			"The user or identity it ran as no longer exists, so this check can't be tested again.",
		);
		expect(nowUnavailableSentence("rows_not_stored")).toBe(
			"Table row checks can't be tested again: the rows aren't stored.",
		);
		expect(nowUnavailableSentence("solution_not_recorded")).toBe(
			"This file check was recorded before its Solution was stored with it, so it can't be tested again.",
		);
	});
});

describe("checkKindTitle", () => {
	it("names each kind of access check in Title Case", () => {
		expect(checkKindTitle("scope_switch")).toBe("Organization Switch");
		expect(checkKindTitle("child_run")).toBe("Child Run");
		expect(checkKindTitle("run_as")).toBe("Run As");
		expect(checkKindTitle("entry")).toBe("Workflow or Agent Access");
		expect(checkKindTitle("policy")).toBe("Data Policy");
		expect(checkKindTitle("secret")).toBe("Secret");
	});

	it("title-cases a kind it doesn't know", () => {
		expect(checkKindTitle("new_kind")).toBe("New Kind");
	});
});
