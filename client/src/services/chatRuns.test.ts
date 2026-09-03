import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/lib/api-client";
import {
	cancelChatRun,
	createChatRun,
	getChatRunState,
} from "./chatRuns";

vi.mock("@/lib/api-client", () => ({
	apiClient: {
		GET: vi.fn(),
		POST: vi.fn(),
	},
}));

describe("chatRuns service", () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	it("creates a durable run through the generated API contract", async () => {
		vi.mocked(apiClient.POST).mockResolvedValue({
			data: { run_id: "run-1" },
			error: undefined,
			response: new Response(),
		} as never);
		const body = {
			conversation_id: "conversation-1",
			content: "hello",
			client_run_id: "run-1",
			user_message_id: "message-1",
			attachment_ids: ["attachment-1"],
			model_profile_id: "profile-pro",
		};

		await createChatRun(body);

		expect(apiClient.POST).toHaveBeenCalledWith("/api/chat/runs", { body });
	});

	it("loads the durable conversation state", async () => {
		vi.mocked(apiClient.GET).mockResolvedValue({
			data: { latest_sequence: 0 },
			error: undefined,
			response: new Response(),
		} as never);

		await getChatRunState("conversation-1");

		expect(apiClient.GET).toHaveBeenCalledWith(
			"/api/chat/conversations/{conversation_id}/state",
			{ params: { path: { conversation_id: "conversation-1" } } },
		);
	});

	it("cancels a durable run", async () => {
		vi.mocked(apiClient.POST).mockResolvedValue({
			data: { run_id: "run-1", status: "cancelled" },
			error: undefined,
			response: new Response(),
		} as never);

		await cancelChatRun("run-1");

		expect(apiClient.POST).toHaveBeenCalledWith(
			"/api/chat/runs/{run_id}/cancel",
			{ params: { path: { run_id: "run-1" } } },
		);
	});
});
