import { describe, expect, it } from "vitest";

import type { components } from "@/lib/v1";

import {
	activityDomId,
	buildActivityReferenceIndex,
	buildRunActivity,
	humanizeToolAction,
	humanizeToolReference,
	summarizeActivityValue,
} from "./run-activity";

type AgentRunStep = components["schemas"]["AgentRunStepResponse"];
type AgentRunChild = components["schemas"]["AgentRunChildResponse"];

function step(
	type: string,
	content: Record<string, unknown>,
	stepNumber: number,
): AgentRunStep {
	return {
		id: `step-${stepNumber}`,
		run_id: "00000000-0000-0000-0000-000000000001",
		step_number: stepNumber,
		type,
		content,
		duration_ms: stepNumber * 100,
		created_at: "2026-07-15T12:00:00Z",
	};
}

function child(
	id: string,
	agentName: string,
	task: string,
	overrides: Partial<AgentRunChild> = {},
): AgentRunChild {
	return {
		id,
		agent_id: `agent-${id}`,
		agent_name: agentName,
		status: "completed",
		asked: task,
		did: null,
		answered: null,
		duration_ms: 1_200,
		created_at: "2026-07-15T12:00:00Z",
		...overrides,
	};
}

describe("run activity projection", () => {
	it("groups a decision, call, and object result into one readable action", () => {
		const activity = buildRunActivity([
			step("llm_request", { messages_count: 2, tools_count: 17 }, 1),
			step(
				"llm_response",
				{ tool_calls: [{ name: "ai_ticketing_get_ticket_details" }] },
				2,
			),
			step(
				"tool_call",
				{
					tool_name: "ai_ticketing_get_ticket_details",
					arguments: { ticket_id: 428950 },
				},
				3,
			),
			step(
				"tool_result",
				{
					tool_name: "ai_ticketing_get_ticket_details",
					result: {
						status: "open",
						matched: true,
						ticket_id: 428950,
					},
				},
				4,
			),
			step(
				"llm_response",
				{ content: "The ticket is ready for triage.", tool_calls: [] },
				5,
			),
		]);

		expect(activity).toHaveLength(2);
		expect(activity[0]).toMatchObject({
			kind: "action",
			title: "Looked up ticket details",
			description: "Ticket: 428950 · Status: Open · Match found",
		});
		expect(activity[1]).toMatchObject({
			kind: "response",
			title: "Final response",
			description: "The ticket is ready for triage.",
		});
	});

	it("keeps a structured final response semantic and hides arbitrary fields", () => {
		const activity = buildRunActivity([
			step(
				"llm_response",
				{
					content:
						'{"ticket_id":428950,"status":"complete","access_token":"hidden"}',
					tool_calls: [],
				},
				1,
			),
		]);

		expect(activity[0]).toMatchObject({
			kind: "response",
			title: "Final response",
			description: "Ticket: 428950 · Status: Complete",
		});
	});

	it("pairs repeated calls with the nearest unmatched result", () => {
		const activity = buildRunActivity([
			step(
				"tool_call",
				{ tool_name: "check_status", arguments: { id: 1 } },
				1,
			),
			step(
				"tool_result",
				{ tool_name: "check_status", result: "First" },
				2,
			),
			step(
				"tool_call",
				{ tool_name: "check_status", arguments: { id: 2 } },
				3,
			),
			step(
				"tool_result",
				{ tool_name: "check_status", result: "Second" },
				4,
			),
		]);

		expect(activity.map((item) => item.description)).toEqual([
			"First",
			"Second",
		]);
		expect(buildActivityReferenceIndex(activity)).toEqual({
			check_status: [
				{
					activityId: "step-1",
					label: "Checked status",
				},
				{
					activityId: "step-3",
					label: "Checked status",
				},
			],
		});
	});

	it("preserves a workflow execution destination on its grouped action", () => {
		const activity = buildRunActivity([
			step(
				"tool_call",
				{ tool_name: "update_ticket", arguments: { ticket_id: 42 } },
				1,
			),
			step(
				"tool_result",
				{
					tool_name: "update_ticket",
					result: { ticket_id: 42, status: "updated" },
					execution_id: "execution-42",
				},
				2,
			),
		]);

		expect(activity[0]).toMatchObject({
			title: "Updated ticket",
			executionId: "execution-42",
		});
		expect(activityDomId(activity[0].id)).toBe("run-activity-item-step-1");
	});

	it("keeps structured and JSON-string results semantic", () => {
		expect(
			summarizeActivityValue({ matched: true, device: "ELIJAH-LT" }),
		).toBe("Device: ELIJAH-LT · Match found");
		expect(
			summarizeActivityValue(
				'{"classification":"service request","routed":true}',
			),
		).toBe("Classification: Service request · Routed successfully");
		expect(summarizeActivityValue([{ id: 1 }, { id: 2 }])).toBe(
			"Returned 2 items",
		);
		expect(
			summarizeActivityValue("{'raw': true, 'ticket_id': 42}"),
		).toBeNull();
		expect(summarizeActivityValue({ status: "in_progress" })).toBe(
			"Status: In progress",
		);
		expect(
			summarizeActivityValue({
				status: "open",
				access_token: "should-never-render",
				password: "also-hidden",
				arbitrary_field: "not-an-outcome",
			}),
		).toBe("Status: Open");
	});

	it("makes linked and historical unlinked child runs distinct delegations", () => {
		const activity = buildRunActivity(
			[
				step(
					"tool_call",
					{
						tool_name: "delegate_to_troubleshooting_agent",
						arguments: { task: "Collect endpoint evidence" },
					},
					1,
				),
				step(
					"tool_result",
					{
						tool_name: "delegate_to_troubleshooting_agent",
						result: "Evidence collected",
						child_run_id: "child-1",
					},
					2,
				),
			],
			["child-1", "historic-child"],
			[
				child(
					"child-1",
					"Endpoint Evidence Collector",
					"Collect endpoint evidence",
				),
				child(
					"historic-child",
					"Historical Specialist",
					"Review the old run",
				),
			],
		);

		expect(activity).toHaveLength(2);
		expect(activity[0]).toMatchObject({
			kind: "delegation",
			title: "Endpoint Evidence Collector",
			childAgentId: "agent-child-1",
			agentName: "Endpoint Evidence Collector",
			task: "Collect endpoint evidence",
			childRunId: "child-1",
		});
		expect(activity[1]).toMatchObject({
			kind: "delegation",
			title: "Historical Specialist",
			agentName: "Historical Specialist",
			task: "Review the old run",
			childRunId: "historic-child",
		});
	});

	it("keeps a failed handoff identifiable as delegated work", () => {
		const activity = buildRunActivity([
			step(
				"tool_call",
				{
					tool_name: "delegate_to_security_agent",
					arguments: { task: "Inspect the alert" },
				},
				1,
			),
			step(
				"tool_error",
				{
					tool_name: "delegate_to_security_agent",
					error: "Timed out",
					child_run_id: "failed-child",
				},
				2,
			),
		]);

		expect(activity[0]).toMatchObject({
			kind: "delegation",
			isError: true,
			title: "Delegation failed",
			description: "Timed out",
			childRunId: "failed-child",
		});
	});

	it("binds historical child IDs to delegation calls that predate child_run_id", () => {
		const activity = buildRunActivity(
			[
				step(
					"tool_call",
					{
						tool_name: "delegate_to_agreement_agent",
						arguments: { task: "Check coverage" },
					},
					1,
				),
				step(
					"tool_result",
					{
						tool_name: "delegate_to_agreement_agent",
						result: "Covered",
					},
					2,
				),
			],
			["historical-child"],
			[child("historical-child", "Agreement Analyst", "Check coverage")],
		);

		expect(activity).toHaveLength(1);
		expect(activity[0]).toMatchObject({
			kind: "delegation",
			childRunId: "historical-child",
			title: "Agreement Analyst",
			agentName: "Agreement Analyst",
		});
	});

	it("does not guess which historical delegation produced a child run", () => {
		const activity = buildRunActivity(
			[
				step(
					"tool_call",
					{
						tool_name: "delegate_to_security_agent",
						arguments: { task: "Inspect the alert" },
					},
					1,
				),
				step(
					"tool_error",
					{
						tool_name: "delegate_to_security_agent",
						error: "Timed out",
					},
					2,
				),
				step(
					"tool_call",
					{
						tool_name: "delegate_to_agreement_agent",
						arguments: { task: "Check coverage" },
					},
					3,
				),
				step(
					"tool_result",
					{
						tool_name: "delegate_to_agreement_agent",
						result: "Covered",
					},
					4,
				),
			],
			["historical-child"],
			[child("historical-child", "Agreement Analyst", "Check coverage")],
		);

		expect(activity).toHaveLength(3);
		expect(activity[0]).toMatchObject({
			kind: "delegation",
			title: "Delegation failed",
			childRunId: null,
		});
		expect(activity[1]).toMatchObject({
			kind: "delegation",
			childRunId: null,
		});
		expect(activity[2]).toMatchObject({
			kind: "delegation",
			title: "Agreement Analyst",
			childRunId: "historical-child",
			childAgentId: "agent-historical-child",
		});
	});

	it("preserves errors and incomplete calls without exposing raw payloads", () => {
		const activity = buildRunActivity([
			step(
				"tool_call",
				{ tool_name: "ai_ticketing_submit_triage", arguments: {} },
				1,
			),
			step(
				"tool_error",
				{
					tool_name: "ai_ticketing_submit_triage",
					error: { message: "Permission denied", code: "forbidden" },
				},
				2,
			),
			step("tool_call", { tool_name: "send_email", arguments: {} }, 3),
		]);

		expect(activity[0]).toMatchObject({
			kind: "error",
			title: "Could not submit triage",
			description: "Permission denied · Code: Forbidden",
		});
		expect(activity[1]).toMatchObject({
			kind: "action",
			title: "Sent email",
			resultStep: null,
		});
	});
});

describe("tool labels", () => {
	it("drops integration prefixes and conjugates compound actions", () => {
		expect(humanizeToolAction("halopsa_halo_update_ticket")).toBe(
			"Updated ticket",
		);
		expect(
			humanizeToolAction("supportability_resolve_and_attach_device"),
		).toBe("Resolved and attached device");
		expect(humanizeToolReference("ai_ticketing_get_ticket_details")).toBe(
			"Get ticket details",
		);
	});

	it("does not invent a label from an unknown executor identifier", () => {
		expect(humanizeToolAction("vendor_internal_opaque_command")).toBe(
			"Completed an action",
		);
		expect(humanizeToolReference("vendor_internal_opaque_command")).toBe(
			"Agent action",
		);
	});
});
