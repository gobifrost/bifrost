import type { components } from "@/lib/v1";

type AgentRunStep = components["schemas"]["AgentRunStepResponse"];
type AgentRunChild = components["schemas"]["AgentRunChildResponse"];

export type RunActivityKind =
	"action" | "delegation" | "response" | "error" | "warning" | "cancelled";

export interface RunActivityItem {
	id: string;
	kind: RunActivityKind;
	title: string;
	description: string | null;
	toolName: string | null;
	task: string | null;
	childRunId: string | null;
	childAgentId: string | null;
	agentName: string | null;
	childStatus: string | null;
	executionId: string | null;
	durationMs: number | null;
	isError: boolean;
	callStep: AgentRunStep | null;
	resultStep: AgentRunStep | null;
}

export interface RunActivityReference {
	activityId: string;
	label: string;
}

export type RunActivityReferenceIndex = Record<string, RunActivityReference[]>;

export function activityDomId(activityId: string): string {
	return `run-activity-item-${activityId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

/**
 * Index each recorded tool occurrence in activity order. A summary can mention
 * the same tool more than once, so consumers pair the first marker with the
 * first action, the second marker with the second action, and so on.
 */
export function buildActivityReferenceIndex(
	activity: RunActivityItem[],
): RunActivityReferenceIndex {
	const references: RunActivityReferenceIndex = {};
	for (const item of activity) {
		if (!item.toolName) continue;
		(references[item.toolName] ??= []).push({
			activityId: item.id,
			label: item.agentName ?? item.title,
		});
	}
	return references;
}

const ACTION_VERBS: Record<string, { past: string; takesFor?: boolean }> = {
	add: { past: "Added" },
	approve: { past: "Approved" },
	archive: { past: "Archived" },
	attach: { past: "Attached" },
	check: { past: "Checked" },
	cancel: { past: "Cancelled" },
	classify: { past: "Classified" },
	close: { past: "Closed" },
	create: { past: "Created" },
	delete: { past: "Deleted" },
	download: { past: "Downloaded" },
	execute: { past: "Ran" },
	fetch: { past: "Retrieved" },
	find: { past: "Found" },
	get: { past: "Looked up" },
	link: { past: "Linked" },
	list: { past: "Listed" },
	lookup: { past: "Looked up" },
	match: { past: "Matched" },
	notify: { past: "Notified" },
	open: { past: "Opened" },
	read: { past: "Read" },
	reject: { past: "Rejected" },
	remove: { past: "Removed" },
	resolve: { past: "Resolved" },
	route: { past: "Routed" },
	run: { past: "Ran" },
	publish: { past: "Published" },
	query: { past: "Queried" },
	save: { past: "Saved" },
	schedule: { past: "Scheduled" },
	search: { past: "Searched", takesFor: true },
	send: { past: "Sent" },
	set: { past: "Set" },
	submit: { past: "Submitted" },
	update: { past: "Updated" },
	upload: { past: "Uploaded" },
	validate: { past: "Validated" },
	write: { past: "Wrote" },
};

const DISPLAY_ACRONYMS: Record<string, string> = {
	ai: "AI",
	id: "ID",
	ip: "IP",
	mfa: "MFA",
	m365: "Microsoft 365",
	psa: "PSA",
	rmm: "RMM",
	sla: "SLA",
	url: "URL",
};

const RESULT_PRIORITY = [
	"message",
	"summary",
	"detail",
	"ticket_id",
	"device",
	"asset_name",
	"classification",
	"status",
	"matched",
	"success",
	"attached",
	"routed",
	"count",
	"name",
	"title",
];

const SAFE_RESULT_KEYS = new Set([...RESULT_PRIORITY, "code"]);

function identifierTokens(value: string): string[] {
	return value
		.replace(/([a-z0-9])([A-Z])/g, "$1_$2")
		.toLowerCase()
		.split(/[^a-z0-9]+/)
		.filter(Boolean);
}

function sentenceCase(words: string[]): string {
	if (!words.length) return "";
	const rendered = words.map((word) => DISPLAY_ACRONYMS[word] ?? word);
	const text = rendered.join(" ");
	return text.charAt(0).toUpperCase() + text.slice(1);
}

function lowerPhrase(words: string[]): string {
	return words.map((word) => DISPLAY_ACRONYMS[word] ?? word).join(" ");
}

export function delegationTarget(toolName: string): string | null {
	const match = toolName.match(/^delegate_to_(.+)$/i);
	if (!match) return null;
	const hasAgentSuffix = /_agent$/i.test(match[1]);
	const target = identifierTokens(match[1].replace(/_agent$/i, ""));
	const name = sentenceCase(target.length ? target : ["another"]);
	return hasAgentSuffix ? `${name} Agent` : name;
}

/**
 * Turn an executor-facing identifier into a past-tense action. Integration
 * prefixes are discarded by starting at the first recognizable action verb.
 */
export function humanizeToolAction(toolName: string): string {
	const delegatedTo = delegationTarget(toolName);
	if (delegatedTo) return "Delegated work";

	const tokens = identifierTokens(toolName);
	const verbIndex = tokens.findIndex((token) => token in ACTION_VERBS);
	if (verbIndex === -1) {
		return "Completed an action";
	}

	const verb = ACTION_VERBS[tokens[verbIndex]];
	const remainder = tokens.slice(verbIndex + 1);
	const conjugated = remainder.map((token, index) => {
		if (
			index > 0 &&
			remainder[index - 1] === "and" &&
			ACTION_VERBS[token]
		) {
			return ACTION_VERBS[token].past.toLowerCase();
		}
		return token;
	});
	const object = lowerPhrase(conjugated);
	if (!object) return verb.past;
	return `${verb.past}${verb.takesFor ? " for" : ""} ${object}`;
}

/** Friendly noun label for a tool marker embedded in summarizer prose. */
export function humanizeToolReference(toolName: string): string {
	const delegatedTo = delegationTarget(toolName);
	if (delegatedTo) return "Delegated agent";

	const tokens = identifierTokens(toolName);
	const verbIndex = tokens.findIndex((token) => token in ACTION_VERBS);
	const meaningful = verbIndex >= 0 ? tokens.slice(verbIndex) : [];
	return sentenceCase(meaningful.length ? meaningful : ["agent", "action"]);
}

function parseJsonish(value: unknown): unknown {
	if (typeof value !== "string") return value;
	const trimmed = value.trim();
	if (!(
		(trimmed.startsWith("{") && trimmed.endsWith("}")) ||
		(trimmed.startsWith("[") && trimmed.endsWith("]"))
	)) {
		return value;
	}
	try {
		return JSON.parse(trimmed);
	} catch {
		return value;
	}
}

function truncate(value: string, length = 150): string {
	const collapsed = value.replace(/\s+/g, " ").trim();
	return collapsed.length > length
		? `${collapsed.slice(0, length - 1)}…`
		: collapsed;
}

function titleForKey(key: string): string {
	const aliases: Record<string, string> = {
		asset_name: "Device",
		classification: "Classification",
		ticket_id: "Ticket",
	};
	return aliases[key] ?? sentenceCase(identifierTokens(key));
}

function readableValue(value: string | number | boolean): string {
	if (typeof value === "boolean") return value ? "Yes" : "No";
	if (typeof value === "string") {
		const clean = truncate(value, 80);
		if (/^[a-z0-9]+(?:_[a-z0-9]+)+$/i.test(clean)) {
			return sentenceCase(identifierTokens(clean));
		}
		return clean.charAt(0).toUpperCase() + clean.slice(1);
	}
	return value.toLocaleString();
}

function describeEntry(key: string, value: string | number | boolean): string {
	if (key === "matched") return value ? "Match found" : "No match found";
	if (key === "success")
		return value ? "Completed successfully" : "Not completed";
	if (key === "attached")
		return value ? "Attached successfully" : "Not attached";
	if (key === "routed") return value ? "Routed successfully" : "Not routed";
	if (
		["message", "summary", "detail"].includes(key) &&
		typeof value === "string"
	) {
		return truncate(value);
	}
	if (key.endsWith("_id")) {
		return `${titleForKey(key)}: ${String(value)}`;
	}
	return `${titleForKey(key)}: ${readableValue(value)}`;
}

/**
 * Produce a compact semantic result. Objects become human-readable fields;
 * raw keys, braces, arrays, and nested payloads stay in Advanced.
 */
export function summarizeActivityValue(value: unknown): string | null {
	const parsed = parseJsonish(value);
	if (parsed === null || parsed === undefined || parsed === "") return null;
	if (typeof parsed === "string") {
		const trimmed = parsed.trim();
		// A malformed/Python-repr object is still a raw payload. Do not leak
		// it into the normal story simply because JSON.parse rejected it.
		if (
			(trimmed.startsWith("{") && trimmed.endsWith("}")) ||
			(trimmed.startsWith("[") && trimmed.endsWith("]"))
		) {
			return null;
		}
		return truncate(parsed);
	}
	if (typeof parsed === "number" || typeof parsed === "boolean") {
		return readableValue(parsed);
	}
	if (Array.isArray(parsed)) {
		return `Returned ${parsed.length.toLocaleString()} ${parsed.length === 1 ? "item" : "items"}`;
	}
	if (typeof parsed !== "object") return null;

	const entries = Object.entries(parsed as Record<string, unknown>).filter(
		([key, entryValue]) =>
			SAFE_RESULT_KEYS.has(key) &&
			(typeof entryValue === "string" ||
				typeof entryValue === "number" ||
				typeof entryValue === "boolean"),
	);
	entries.sort(([a], [b]) => {
		const ai = RESULT_PRIORITY.indexOf(a);
		const bi = RESULT_PRIORITY.indexOf(b);
		return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
	});

	const descriptions = entries
		.slice(0, 3)
		.map(([key, entryValue]) =>
			describeEntry(key, entryValue as string | number | boolean),
		)
		.filter(Boolean);
	return descriptions.length ? descriptions.join(" · ") : null;
}

function stepContent(step: AgentRunStep): Record<string, unknown> {
	return (step.content ?? {}) as Record<string, unknown>;
}

function stepId(step: AgentRunStep, fallback: string): string {
	return step.id || fallback;
}

function stringField(value: unknown): string | null {
	return typeof value === "string" && value.trim() ? value.trim() : null;
}

function toolNameFor(step: AgentRunStep): string {
	return stringField(stepContent(step).tool_name) ?? "agent_action";
}

function newToolItem(step: AgentRunStep, index: number): RunActivityItem {
	const content = stepContent(step);
	const toolName = toolNameFor(step);
	const target = delegationTarget(toolName);
	return {
		id: stepId(step, `action-${index}`),
		kind: target ? "delegation" : "action",
		title: humanizeToolAction(toolName),
		description: null,
		toolName,
		task: target
			? stringField(
					(content.arguments as Record<string, unknown> | undefined)
						?.task,
				)
			: null,
		childRunId: null,
		childAgentId: null,
		agentName: null,
		childStatus: null,
		executionId: stringField(content.execution_id),
		durationMs: step.duration_ms ?? null,
		isError: false,
		callStep: step,
		resultStep: null,
	};
}

function attachResult(item: RunActivityItem, step: AgentRunStep): void {
	const content = stepContent(step);
	const isError = step.type === "tool_error" || content.is_error === true;
	const delegatedTo = item.toolName ? delegationTarget(item.toolName) : null;
	item.resultStep = step;
	item.isError = isError;
	item.kind = isError && !delegatedTo ? "error" : item.kind;
	item.title = isError
		? delegatedTo
			? "Delegation failed"
			: `Could not ${humanizeToolReference(item.toolName ?? "action").toLowerCase()}`
		: item.title;
	item.description = summarizeActivityValue(
		isError ? (content.error ?? content.result) : content.result,
	);
	item.childRunId = stringField(content.child_run_id) ?? item.childRunId;
	item.executionId = stringField(content.execution_id) ?? item.executionId;
	item.durationMs = step.duration_ms ?? item.durationMs;
}

/** Build the normal, user-facing projection of a raw executor trace. */
export function buildRunActivity(
	steps: AgentRunStep[] | null | undefined,
	childRunIds: string[] | null | undefined = [],
	childRuns: AgentRunChild[] | null | undefined = [],
): RunActivityItem[] {
	const ordered = [...(steps ?? [])].sort(
		(a, b) => (a.step_number ?? 0) - (b.step_number ?? 0),
	);
	const activity: RunActivityItem[] = [];

	for (const [index, step] of ordered.entries()) {
		const content = stepContent(step);
		switch (step.type) {
			case "tool_call":
				activity.push(newToolItem(step, index));
				break;
			case "tool_result":
			case "tool_error": {
				const toolName = toolNameFor(step);
				const match = [...activity]
					.reverse()
					.find(
						(item) =>
							item.toolName === toolName && !item.resultStep,
					);
				if (match) {
					attachResult(match, step);
				} else {
					const orphan = newToolItem(step, index);
					orphan.callStep = null;
					attachResult(orphan, step);
					activity.push(orphan);
				}
				break;
			}
			case "llm_response": {
				const toolCalls = Array.isArray(content.tool_calls)
					? content.tool_calls
					: [];
				const response = stringField(content.content);
				if (toolCalls.length === 0 && response) {
					activity.push({
						id: stepId(step, `response-${index}`),
						kind: "response",
						title: "Final response",
						description: summarizeActivityValue(response),
						toolName: null,
						task: null,
						childRunId: null,
						childAgentId: null,
						agentName: null,
						childStatus: null,
						executionId: null,
						durationMs: step.duration_ms ?? null,
						isError: false,
						callStep: null,
						resultStep: step,
					});
				}
				break;
			}
			case "error":
			case "budget_warning":
			case "cancelled":
				activity.push({
					id: stepId(step, `notice-${index}`),
					kind:
						step.type === "error"
							? "error"
							: step.type === "cancelled"
								? "cancelled"
								: "warning",
					title:
						step.type === "error"
							? "The run hit an error"
							: step.type === "cancelled"
								? "The run was cancelled"
								: "The run approached its budget",
					description: summarizeActivityValue(
						content.error ?? content.result ?? content,
					),
					toolName: null,
					task: null,
					childRunId: null,
					childAgentId: null,
					agentName: null,
					childStatus: null,
					executionId: null,
					durationMs: step.duration_ms ?? null,
					isError: step.type === "error",
					callStep: null,
					resultStep: step,
				});
				break;
			default:
				// LLM requests, tool-planning responses, and unknown executor
				// plumbing remain available in Advanced but do not compete with
				// meaningful work in the normal activity story.
				break;
		}
	}

	const linkedChildIds = new Set(
		activity
			.map((item) => item.childRunId)
			.filter((id): id is string => !!id),
	);
	const orderedChildIds = [
		...(childRunIds ?? []),
		...(childRuns ?? []).map((child) => child.id),
	].filter((id, index, ids) => ids.indexOf(id) === index);
	const unboundDelegations = activity.filter(
		(item) => item.kind === "delegation" && !item.childRunId,
	);
	const unlinkedChildIds = orderedChildIds.filter(
		(childRunId) => !linkedChildIds.has(childRunId),
	);

	// Older traces did not persist child_run_id on delegation results. Preserve
	// the convenient inline binding only when there is exactly one possible
	// pairing. With multiple handoffs, order alone is not authoritative: an
	// earlier failed handoff may not have produced a child at all.
	if (unboundDelegations.length === 1 && unlinkedChildIds.length === 1) {
		unboundDelegations[0].childRunId = unlinkedChildIds[0];
		linkedChildIds.add(unlinkedChildIds[0]);
	}

	for (const childRunId of unlinkedChildIds) {
		if (linkedChildIds.has(childRunId)) continue;
		activity.push({
			id: `delegation-${childRunId}`,
			kind: "delegation",
			title: "Delegated work",
			description: null,
			toolName: null,
			task: null,
			childRunId,
			childAgentId: null,
			agentName: null,
			childStatus: null,
			executionId: null,
			durationMs: null,
			isError: false,
			callStep: null,
			resultStep: null,
		});
		linkedChildIds.add(childRunId);
	}

	const childrenById = new Map(
		(childRuns ?? []).map((child) => [child.id, child]),
	);
	for (const item of activity) {
		if (item.kind !== "delegation" || !item.childRunId) continue;
		const child = childrenById.get(item.childRunId);
		if (!child) continue;
		item.childAgentId = child.agent_id;
		item.agentName = child.agent_name ?? null;
		item.childStatus = child.status;
		item.task = child.asked?.trim() || item.task;
		item.title = child.agent_name?.trim() || "Delegated agent";
		item.durationMs = child.duration_ms ?? item.durationMs;
	}

	return activity;
}
