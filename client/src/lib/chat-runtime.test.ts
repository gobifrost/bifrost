import { describe, expect, it } from "vitest";
import {
	applyChatStreamEnvelope,
	applyChatStreamEnvelopes,
	deriveActiveRun,
	getCurrentStreamingMessage,
	hydrateChatProjection,
	makeEmptyChatProjection,
	stageOptimisticUserTurn,
	type ChatProjection,
	type ChatStreamEnvelope,
} from "./chat-runtime";

function makeEnvelope(
	sequence: number,
	runId: string,
	chunk: ChatStreamEnvelope["chunk"],
	eventId = `evt-${sequence}`,
	occurredAt = `2026-09-02T00:00:${String(sequence).padStart(2, "0")}Z`,
): ChatStreamEnvelope {
	return {
		protocol_version: 1,
		event_id: eventId,
		sequence,
		conversation_id: "conversation-1",
		run_id: runId,
		occurred_at: occurredAt,
		chunk,
	};
}

function makeSnapshotProjection(): ChatProjection {
	return hydrateChatProjection(makeEmptyChatProjection(), {
		conversation_id: "conversation-1",
		messages: [
			{
				id: "archived-user",
				conversation_id: "conversation-1",
				role: "user",
				content: "archived",
				sequence: 1,
				created_at: "2026-09-02T00:00:01Z",
			},
			{
				id: "archived-assistant",
				conversation_id: "conversation-1",
				role: "assistant",
				content: "archived reply",
				sequence: 2,
				created_at: "2026-09-02T00:00:02Z",
			},
		],
		runs: {
			"archived-run": {
				run_id: "archived-run",
				conversation_id: "conversation-1",
				status: "succeeded",
				last_sequence: 2,
				last_event_id: "evt-2",
				applied_event_ids: ["evt-1", "evt-2"],
				user_message_id: "archived-user",
				assistant_message_id: "archived-assistant",
				streaming_message_id: null,
			},
		},
		last_sequence: 2,
	});
}

describe("chat-runtime reducer", () => {
	it("applies a render-frame batch without mutating the prior projection", () => {
		const original = stageOptimisticUserTurn(
			makeEmptyChatProjection("conversation-1"),
			{
				conversation_id: "conversation-1",
				run_id: "run-1",
				user_message_id: "user-1",
				content: "Hello",
			},
		);
		const events = [
			makeEnvelope(1, "run-1", {
				type: "message_start",
				user_message_id: "user-1",
				assistant_message_id: "assistant-1",
			}),
			makeEnvelope(2, "run-1", { type: "delta", content: "Hel" }),
			makeEnvelope(3, "run-1", { type: "delta", content: "lo" }),
		];

		const batched = applyChatStreamEnvelopes(original, events);

		expect(original.messages).toHaveLength(1);
		expect(
			batched.messages.find((message) => message.id === "assistant-1")
				?.content,
		).toBe("Hello");
		expect(batched.runs["run-1"].last_sequence).toBe(3);
	});

	it("rejects out-of-order and duplicate events per run", () => {
		const staged = stageOptimisticUserTurn(
			makeEmptyChatProjection("conversation-1"),
			{
				conversation_id: "conversation-1",
				run_id: "run-1",
				user_message_id: "user-local",
				content: "Hello",
				created_at: "2026-09-02T00:00:00Z",
			},
		);

		const started = applyChatStreamEnvelope(
			staged,
			makeEnvelope(1, "run-1", {
				type: "message_start",
				conversation_id: "conversation-1",
				user_message_id: "user-server",
				assistant_message_id: "assistant-1",
				local_id: "user-local",
			}),
		);

		const advanced = applyChatStreamEnvelope(
			started,
			makeEnvelope(3, "run-1", {
				type: "delta",
				conversation_id: "conversation-1",
				content: " world",
			}),
		);

		const ignored = applyChatStreamEnvelope(
			advanced,
			makeEnvelope(2, "run-1", {
				type: "delta",
				conversation_id: "conversation-1",
				content: "ignored",
			}),
		);

		expect(ignored.last_sequence).toBe(3);
		expect(
			ignored.messages.filter((message) => message.run_id === "run-1"),
		).toHaveLength(2);
		expect(
			ignored.messages.find((message) => message.role === "assistant"),
		)?.toMatchObject({
			id: "assistant-1",
			content: " world",
			is_streaming: true,
		});
	});

	it("hydrates snapshots without losing optimistic unsent turns and stays idempotent on replay", () => {
		const staged = stageOptimisticUserTurn(
			makeEmptyChatProjection("conversation-1"),
			{
				conversation_id: "conversation-1",
				run_id: "run-1",
				user_message_id: "user-local",
				content: "draft",
				created_at: "2026-09-02T00:00:00Z",
			},
		);

		const hydrated = hydrateChatProjection(staged, {
			conversation_id: "conversation-1",
			messages: [
				{
					id: "archived-user",
					conversation_id: "conversation-1",
					role: "user",
					content: "archived",
					sequence: 1,
					created_at: "2026-09-02T00:00:01Z",
				},
			],
		});

		const replayed = hydrateChatProjection(hydrated, {
			conversation_id: "conversation-1",
			messages: [
				{
					id: "archived-user",
					conversation_id: "conversation-1",
					role: "user",
					content: "archived",
					sequence: 1,
					created_at: "2026-09-02T00:00:01Z",
				},
			],
		});

		expect(hydrated.messages.map((message) => message.id)).toEqual([
			"user-local",
			"archived-user",
		]);
		expect(replayed).toEqual(hydrated);
	});

	it("reconciles optimistic user turns against authoritative server IDs", () => {
		const staged = stageOptimisticUserTurn(
			makeEmptyChatProjection("conversation-1"),
			{
				conversation_id: "conversation-1",
				run_id: "run-1",
				user_message_id: "user-local",
				content: "draft",
				created_at: "2026-09-02T00:00:00Z",
			},
		);

		const hydrated = hydrateChatProjection(
			staged,
			makeSnapshotProjection() as ChatProjection,
		);
		expect(
			hydrated.messages.some((message) => message.id === "user-local"),
		).toBe(true);

		const reconciled = applyChatStreamEnvelope(
			hydrated,
			makeEnvelope(1, "run-1", {
				type: "message_start",
				conversation_id: "conversation-1",
				user_message_id: "user-server",
				assistant_message_id: "assistant-1",
				local_id: "user-local",
			}),
		);

		expect(
			reconciled.messages.some((message) => message.id === "user-local"),
		).toBe(false);
		expect(
			reconciled.messages.find((message) => message.id === "user-server"),
		).toMatchObject({
			local_id: "user-local",
			is_optimistic: false,
		});
		expect(getCurrentStreamingMessage(reconciled)?.id).toBe("assistant-1");
	});

	it("treats done content as authoritative after assistant boundaries", () => {
		const staged = stageOptimisticUserTurn(
			makeEmptyChatProjection("conversation-1"),
			{
				conversation_id: "conversation-1",
				run_id: "run-1",
				user_message_id: "user-local",
				content: "draft",
				created_at: "2026-09-02T00:00:00Z",
			},
		);

		const started = applyChatStreamEnvelope(
			staged,
			makeEnvelope(1, "run-1", {
				type: "message_start",
				conversation_id: "conversation-1",
				user_message_id: "user-server",
				assistant_message_id: "assistant-1",
				local_id: "user-local",
			}),
		);

		const streaming = applyChatStreamEnvelope(
			started,
			makeEnvelope(2, "run-1", {
				type: "delta",
				conversation_id: "conversation-1",
				content: "Hello",
			}),
		);

		const boundary = applyChatStreamEnvelope(
			streaming,
			makeEnvelope(3, "run-1", {
				type: "assistant_message_end",
				conversation_id: "conversation-1",
				message_id: "assistant-1",
			}),
		);

		expect(getCurrentStreamingMessage(boundary)).toBeNull();
		expect(
			boundary.messages.find((message) => message.id === "assistant-1"),
		).toMatchObject({
			content: "Hello",
			is_streaming: false,
			is_final: true,
		});

		const done = applyChatStreamEnvelope(
			boundary,
			makeEnvelope(4, "run-1", {
				type: "done",
				conversation_id: "conversation-1",
				message_id: "assistant-1",
				content: "Hello, world",
				token_count_input: 100,
				token_count_output: 10,
				duration_ms: 123,
			}),
		);

		expect(
			done.messages.find((message) => message.id === "assistant-1"),
		).toMatchObject({
			content: "Hello, world",
			is_streaming: false,
			is_final: true,
			token_count_input: 100,
			token_count_output: 10,
			duration_ms: 123,
		});
	});

	it("terminalizes a batched run after routing, tool, and artifact events", () => {
		const staged = stageOptimisticUserTurn(
			makeEmptyChatProjection("conversation-1"),
			{
				conversation_id: "conversation-1",
				run_id: "run-1",
				user_message_id: "user-1",
				content: "Summarize this file",
			},
		);
		const events: ChatStreamEnvelope[] = [
			{
				...makeEnvelope(1, "run-1", {
					type: "run_status",
					run_status: "running",
				}),
				status: "running",
			},
			makeEnvelope(2, "run-1", {
				type: "message_start",
				user_message_id: "user-1",
				local_id: "user-1",
				assistant_message_id: "assistant-1",
			}),
			makeEnvelope(3, "run-1", {
				type: "agent_switch",
				agent_switch: {
					agent_id: "agent-document",
					agent_name: "Document Agent",
					reason: "automatic",
				},
			}),
			makeEnvelope(4, "run-1", {
				type: "delta",
				content: "I'll create that.",
			}),
			makeEnvelope(5, "run-1", {
				type: "assistant_message_end",
				message_id: "assistant-boundary",
			}),
			makeEnvelope(6, "run-1", {
				type: "tool_call",
				message_id: "tool-message",
				tool_call: {
					id: "tool-call",
					name: "create_text_artifact",
					arguments: {
						filename: "Generated Report.md",
						format: "markdown",
					},
				},
			}),
			makeEnvelope(7, "run-1", {
				type: "tool_result",
				message_id: "tool-message",
				tool_result: {
					tool_call_id: "tool-call",
					tool_name: "create_text_artifact",
					result: { type: "bifrost_artifact" },
					duration_ms: 304,
				},
			}),
			makeEnvelope(8, "run-1", {
				type: "artifact_ready",
				message_id: "tool-message",
				artifact: {
					type: "bifrost_artifact",
					id: "artifact-1",
					filename: "Generated Report.md",
					content_type: "text/markdown",
					size_bytes: 40,
				},
			}),
			makeEnvelope(9, "run-1", {
				type: "delta",
				content: "I created the report.",
			}),
			{
				...makeEnvelope(10, "run-1", {
					type: "done",
					message_id: "assistant-1",
					content: "I created the report.",
					duration_ms: 1_240,
					run_status: "completed",
				}),
				status: "completed",
			},
		];

		const completed = applyChatStreamEnvelopes(staged, events);

		expect(deriveActiveRun(completed)).toBeNull();
		expect(completed.runs["run-1"].status).toBe("completed");
		expect(
			completed.messages.find(
				(message) =>
					message.role === "assistant" &&
					message.content === "I created the report.",
			),
		).toMatchObject({
			is_streaming: false,
			is_final: true,
			duration_ms: 1_240,
		});
	});

	it("does not regress a terminal socket event with a stale nonterminal snapshot", () => {
		const staged = stageOptimisticUserTurn(
			makeEmptyChatProjection("conversation-1"),
			{
				conversation_id: "conversation-1",
				run_id: "run-1",
				user_message_id: "user-1",
				content: "Hello",
			},
		);
		const completed = applyChatStreamEnvelope(staged, {
			...makeEnvelope(10, "run-1", {
				type: "done",
				content: "Complete response",
				duration_ms: 1_200,
			}),
			status: "completed",
		});

		const hydrated = hydrateChatProjection(completed, {
			conversation_id: "conversation-1",
			runs: {
				"run-1": {
					run_id: "run-1",
					conversation_id: "conversation-1",
					status: "running",
					last_sequence: 0,
				},
			},
			active_run_id: "run-1",
			last_sequence: 0,
		});

		expect(hydrated.runs["run-1"].status).toBe("completed");
		expect(hydrated.runs["run-1"].last_sequence).toBe(10);
		expect(deriveActiveRun(hydrated)).toBeNull();
	});

	it("projects artifact-ready events onto the persisted tool message", () => {
		let projection = stageOptimisticUserTurn(
			makeEmptyChatProjection("conversation-1"),
			{
				conversation_id: "conversation-1",
				run_id: "run-1",
				user_message_id: "user-1",
				content: "Create a report",
			},
		);
		projection = applyChatStreamEnvelope(
			projection,
			makeEnvelope(1, "run-1", {
				type: "tool_call",
				message_id: "tool-message-1",
				tool_call: {
					id: "tool-call-1",
					name: "create_text_artifact",
					arguments: { filename: "Report.md" },
				},
			}),
		);
		projection = applyChatStreamEnvelope(
			projection,
			makeEnvelope(2, "run-1", {
				type: "artifact_ready",
				message_id: "tool-message-1",
				artifact: {
					type: "bifrost_artifact",
					id: "artifact-1",
					filename: "Report.md",
					content_type: "text/markdown",
					size_bytes: 42,
				},
			}),
		);

		expect(
			projection.messages.find(
				(message) => message.id === "tool-message-1",
			)?.attachments,
		).toEqual([
			{
				id: "artifact-1",
				filename: "Report.md",
				content_type: "text/markdown",
				size_bytes: 42,
				kind: "artifact",
			},
		]);
	});

	it("keeps multiple runs per conversation distinct and derives the active run", () => {
		let projection = makeEmptyChatProjection("conversation-1");

		projection = stageOptimisticUserTurn(projection, {
			conversation_id: "conversation-1",
			run_id: "run-1",
			user_message_id: "user-1",
			content: "first",
			created_at: "2026-09-02T00:00:00Z",
		});

		projection = applyChatStreamEnvelope(
			projection,
			makeEnvelope(1, "run-1", {
				type: "message_start",
				conversation_id: "conversation-1",
				user_message_id: "user-1-server",
				assistant_message_id: "assistant-1",
				local_id: "user-1",
			}),
		);

		projection = applyChatStreamEnvelope(
			projection,
			makeEnvelope(2, "run-1", {
				type: "done",
				conversation_id: "conversation-1",
				message_id: "assistant-1",
				content: "first reply",
			}),
		);

		projection = stageOptimisticUserTurn(projection, {
			conversation_id: "conversation-1",
			run_id: "run-2",
			user_message_id: "user-2",
			content: "second",
			created_at: "2026-09-02T00:00:03Z",
		});

		projection = applyChatStreamEnvelope(
			projection,
			makeEnvelope(3, "run-2", {
				type: "message_start",
				conversation_id: "conversation-1",
				user_message_id: "user-2-server",
				assistant_message_id: "assistant-2",
				local_id: "user-2",
			}),
		);

		expect(projection.runs["run-1"].status).toBe("succeeded");
		expect(projection.runs["run-2"].status).toBe("running");
		expect(deriveActiveRun(projection)?.run_id).toBe("run-2");
		expect(getCurrentStreamingMessage(projection)?.id).toBe("assistant-2");
		expect(projection.run_order).toEqual(["run-1", "run-2"]);
	});

	it("keeps platform-job tool results running until the job is terminal", () => {
		let projection = makeEmptyChatProjection("conversation-1");

		projection = stageOptimisticUserTurn(projection, {
			conversation_id: "conversation-1",
			run_id: "run-1",
			user_message_id: "user-1",
			content: "start",
			created_at: "2026-09-02T00:00:00Z",
		});

		projection = applyChatStreamEnvelope(
			projection,
			makeEnvelope(1, "run-1", {
				type: "message_start",
				conversation_id: "conversation-1",
				user_message_id: "user-1-server",
				assistant_message_id: "assistant-1",
				local_id: "user-1",
			}),
		);

		projection = applyChatStreamEnvelope(
			projection,
			makeEnvelope(2, "run-1", {
				type: "tool_call",
				conversation_id: "conversation-1",
				message_id: "tool-msg-1",
				execution_id: "exec-1",
				tool_call: {
					id: "tool-1",
					name: "video_generation",
					arguments: { filename: "clip.mp4" },
				},
			}),
		);

		projection = applyChatStreamEnvelope(
			projection,
			makeEnvelope(3, "run-1", {
				type: "tool_result",
				conversation_id: "conversation-1",
				tool_result: {
					tool_call_id: "tool-1",
					tool_name: "video_generation",
					result: {
						type: "platform_job",
						kind: "video_generation",
						job_id: "job-1",
						status: "running",
					},
					error: null,
					duration_ms: 50,
				},
			}),
		);

		expect(
			projection.messages.find((message) => message.id === "tool-msg-1"),
		).toMatchObject({
			tool_state: "running",
			tool_result: {
				type: "platform_job",
				kind: "video_generation",
				job_id: "job-1",
				status: "running",
			},
		});

		projection = applyChatStreamEnvelope(
			projection,
			makeEnvelope(4, "run-1", {
				type: "tool_result",
				conversation_id: "conversation-1",
				tool_result: {
					tool_call_id: "tool-1",
					tool_name: "video_generation",
					result: {
						type: "platform_job",
						kind: "video_generation",
						job_id: "job-1",
						status: "succeeded",
					},
					error: null,
					duration_ms: 75,
				},
			}),
		);

		expect(
			projection.messages.find((message) => message.id === "tool-msg-1"),
		).toMatchObject({
			tool_state: "completed",
			tool_result: {
				type: "platform_job",
				kind: "video_generation",
				job_id: "job-1",
				status: "succeeded",
			},
		});
	});

	it("never projects a server diagnostic into a user-visible chat error", () => {
		const rawError =
			"1 validation error for ChatStreamChunk tool_result.result Field required";
		const projection = applyChatStreamEnvelope(
			makeEmptyChatProjection("conversation-1"),
			{
				...makeEnvelope(1, "run-1", {
					type: "error",
					run_status: "failed",
					error: rawError,
				}),
				status: "failed",
			},
		);

		expect(projection.system_events).toContainEqual(
			expect.objectContaining({
				type: "error",
				error: "We couldn't complete this response. Please try again.",
			}),
		);
		expect(JSON.stringify(projection.system_events)).not.toContain(rawError);
	});

	it("uses safe timeout copy for timed-out chat runs", () => {
		const projection = applyChatStreamEnvelope(
			makeEmptyChatProjection("conversation-1"),
			{
				...makeEnvelope(1, "run-1", {
					type: "error",
					error: "Chat run timed out after 300s",
				}),
				status: "timeout",
			},
		);

		expect(projection.system_events).toContainEqual(
			expect.objectContaining({
				type: "error",
				error:
					"This response took too long to complete. Please try again.",
			}),
		);
	});
});
