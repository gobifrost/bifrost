/** Access-trace steps as people read them: Title Case labels and plain sentences for the server's machine reasons. */

import type {
	AccessStep,
	AccessTrace,
	NowUnavailable,
} from "@/services/access";

/** Names for the ids a step's reason and facts carry. */
export interface TraceNames {
	role: (roleId: string) => string | undefined;
	organization: (organizationId: string) => string | undefined;
	/** The permission's Graph-style name ("Read Tables"). */
	permission: (permission: string) => string;
}

const SMALL_WORDS = new Set([
	"a",
	"an",
	"and",
	"as",
	"at",
	"by",
	"for",
	"in",
	"of",
	"on",
	"or",
	"the",
	"to",
	"with",
]);

/** "Target in reach" → "Target in Reach". */
export function stepTitle(label: string): string {
	return label
		.split(" ")
		.map((word, index) =>
			index > 0 && SMALL_WORDS.has(word.toLowerCase())
				? word.toLowerCase()
				: word.charAt(0).toUpperCase() + word.slice(1),
		)
		.join(" ");
}

/** "role:<id>:<permission>@<boundary>" or "role:<id>@<boundary>" → [id, permission]. */
function roleParts(reason: string): [string, string] {
	const [id, permission = ""] = reason
		.slice("role:".length)
		.split("@")[0]
		.split(":");
	return [id, permission];
}

function organizationName(step: AccessStep, names: TraceNames): string {
	const id = step.facts.organization_id;
	return (
		(typeof id === "string" && names.organization(id)) ||
		"This organization"
	);
}

function workflowAccessSentence(reason: string): string | undefined {
	return {
		access: "They can start this workflow.",
		no_access: "They can't start this workflow.",
		unattended: "An identity runs it unattended, so no one starts it.",
	}[reason];
}

function runUserSentence(step: AccessStep): string | undefined {
	const who = { person: "this person", identity: "this identity" }[
		step.reason
	];
	if (!who) return undefined;
	return step.facts.is_platform_admin
		? `Runs as ${who}, a Platform Admin.`
		: `Runs as ${who}.`;
}

function powersSentence(step: AccessStep): string | undefined {
	const grants = Array.isArray(step.facts.grants) ? step.facts.grants : [];
	return {
		no_workflow: "No workflow: only their own roles apply.",
		full: "The workflow has Full powers: every permission, secrets included.",
		restricted: `The workflow is Restricted: ${grants.length} ${grants.length === 1 ? "grant adds" : "grants add"} to the run user's roles.`,
	}[step.reason];
}

function targetSentence(
	step: AccessStep,
	names: TraceNames,
): string | undefined {
	const organization = organizationName(step, names);
	const { reason } = step;
	if (reason.startsWith("role:")) {
		const role = names.role(roleParts(reason)[0]);
		if (reason.endsWith("@managed_organizations"))
			return role
				? `${role} reaches every customer organization.`
				: "A role placed on every customer organization reaches it.";
		return role
			? `${role} is placed on ${organization}.`
			: `A role placed there reaches ${organization}.`;
	}
	return {
		global: "Global is in everyone's reach.",
		platform_admin: "A Platform Admin reaches every organization.",
		home: `${organization} is their home organization.`,
		outside: `${organization} is outside their reach.`,
	}[reason];
}

function permissionSentence(
	step: AccessStep,
	names: TraceNames,
	runUserId: string | undefined,
): string | undefined {
	const { reason } = step;
	const named = (prefix: string) =>
		names.permission(reason.slice(prefix.length));
	if (reason.startsWith("base_role:"))
		return `Their base role grants ${named("base_role:")}.`;
	if (reason.startsWith("denied:missing:"))
		return `No role grants ${named("denied:missing:")} there.`;
	if (reason.startsWith("role:")) {
		const [roleId, held] = roleParts(reason);
		const permission = names.permission(held);
		if (roleId === runUserId)
			return `The workflow's grants include ${permission}.`;
		const role = names.role(roleId);
		return role
			? `${role} grants ${permission} there.`
			: `A role placed there grants ${permission}.`;
	}
	return {
		full: "Full powers include every permission.",
		platform_admin: "A Platform Admin holds All Permissions.",
		public: "Anyone may do this.",
		signed_in: "Anyone signed in may do this in their home organization.",
		"denied:operation_confined_to_own_org":
			"This operation works only in their home organization.",
		"denied:beyond_own_org_requires_role":
			"Outside their home organization, this needs a role placed there.",
		no_access_list_entry: "The access list doesn't name this operation.",
	}[reason];
}

/**
 * What a step decided, as a sentence: roles and organizations by name where
 * `names` knows them ("a role placed there" where it doesn't), permissions by
 * their Graph-style names. `runUserId` recognizes a Restricted workflow's
 * grants, which the server judges as a role named by the run user's id. A
 * step that wasn't reached says nothing; a reason this doesn't know shows as
 * written.
 */
export function stepSentence(
	step: AccessStep,
	names: TraceNames,
	runUserId: string | undefined,
): string {
	if (step.status === "not_reached") return "";
	const sentences: Record<string, (() => string | undefined) | undefined> = {
		workflow_access: () => workflowAccessSentence(step.reason),
		run_user: () => runUserSentence(step),
		powers: () => powersSentence(step),
		target: () => targetSentence(step, names),
		permission: () => permissionSentence(step, names, runUserId),
	};
	return sentences[step.key]?.() ?? step.reason;
}

/** The step that would stop `trace`, if any. */
export function stoppedStep(trace: AccessTrace): AccessStep | undefined {
	return trace.steps.find((step) => step.status === "stopped");
}

/** Keys of the steps that decided differently now than then. */
export function changedStepKeys(
	then: AccessTrace,
	now: AccessTrace,
): Set<string> {
	const before = new Map(then.steps.map((step) => [step.key, step]));
	return new Set(
		now.steps
			.filter((step) => {
				const earlier = before.get(step.key);
				return (
					earlier?.status !== step.status ||
					earlier.reason !== step.reason
				);
			})
			.map((step) => step.key),
	);
}

/** One sentence for what changed between a stored check and now. */
export function changeSentence(then: AccessTrace, now: AccessTrace): string {
	if (changedStepKeys(then, now).size === 0)
		return "Nothing changed since then.";
	const stop = stoppedStep(now);
	return stop
		? `Now this check would stop at ${stepTitle(stop.label)}.`
		: "Now this check would be allowed.";
}

/** Why a stored check can't be judged again, as a sentence. */
export function nowUnavailableSentence(reason: NowUnavailable): string {
	return {
		rows_not_stored:
			"Table row checks can't be tested again: the rows aren't stored.",
		run_user_missing:
			"The user or identity it ran as no longer exists, so this check can't be tested again.",
		workflow_missing:
			"The workflow no longer exists, so this check can't be tested again.",
		solution_not_recorded:
			"This file check was recorded before its Solution was stored with it, so it can't be tested again.",
	}[reason];
}

const CHECK_KINDS: Record<string, string> = {
	scope_switch: "Organization Switch",
	child_run: "Child Run",
	run_as: "Run As",
	entry: "Workflow or Agent Access",
	policy: "Data Policy",
	secret: "Secret",
};

/** An access check's kind (its resource type) in Title Case. */
export function checkKindTitle(kind: string): string {
	return CHECK_KINDS[kind] ?? stepTitle(kind.replaceAll("_", " "));
}

function isTrace(value: unknown): value is AccessTrace {
	if (typeof value !== "object" || value === null) return false;
	const trace = value as Partial<AccessTrace>;
	return typeof trace.outcome === "string" && Array.isArray(trace.steps);
}

/** The trace an `access.check` event stored in its details. */
export function storedTrace(
	details: Record<string, unknown> | null | undefined,
): AccessTrace | undefined {
	const trace = details?.trace;
	return isTrace(trace) ? trace : undefined;
}
