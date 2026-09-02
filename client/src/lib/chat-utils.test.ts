import { describe, expect, it, vi, afterEach } from "vitest";
import {
	generateLocalId,
	generateMessageId,
	integrateMessages,
} from "./chat-utils";

const originalRandomUUID = crypto.randomUUID;
const originalGetRandomValues = crypto.getRandomValues.bind(crypto);
const originalCrypto = globalThis.crypto;

afterEach(() => {
	Object.defineProperty(globalThis, "crypto", {
		configurable: true,
		value: originalCrypto,
	});
	Object.defineProperty(originalCrypto, "randomUUID", {
		configurable: true,
		value: originalRandomUUID,
	});
	Object.defineProperty(originalCrypto, "getRandomValues", {
		configurable: true,
		value: originalGetRandomValues,
	});
	vi.restoreAllMocks();
});

describe("chat-utils ids", () => {
	it("uses crypto.randomUUID when the browser exposes it", () => {
		Object.defineProperty(crypto, "randomUUID", {
			configurable: true,
			value: vi.fn(() => "11111111-1111-4111-8111-111111111111"),
		});

		expect(generateMessageId()).toBe(
			"11111111-1111-4111-8111-111111111111",
		);
		expect(generateLocalId()).toBe(
			"local-11111111-1111-4111-8111-111111111111",
		);
	});

	it("falls back to getRandomValues when randomUUID is unavailable", () => {
		Object.defineProperty(crypto, "randomUUID", {
			configurable: true,
			value: undefined,
		});
		Object.defineProperty(crypto, "getRandomValues", {
			configurable: true,
			value: vi.fn((array: Uint8Array) => {
				array.set([
					0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc, 0xde, 0xf0, 0x12, 0x34,
					0x56, 0x78, 0x9a, 0xbc, 0xde, 0xf0,
				]);
				return array;
			}),
		});

		expect(generateMessageId()).toBe(
			"12345678-9abc-4ef0-9234-56789abcdef0",
		);
	});

	it("uses a timestamp fallback only when no browser crypto API exists", () => {
		Object.defineProperty(globalThis, "crypto", {
			configurable: true,
			value: undefined,
		});
		vi.spyOn(Date, "now").mockReturnValue(1790000000000);
		vi.spyOn(Math, "random").mockReturnValue(0.5);

		expect(generateMessageId()).toBe("fallback-mubbs7i8-i");
	});
});

describe("integrateMessages", () => {
	it("preserves server sequence and local arrival order across clock skew", () => {
		const serverMessages = [
			{
				id: "user-1",
				conversation_id: "conversation-1",
				role: "user",
				content: "Hello",
				sequence: 1,
				created_at: "2026-01-01T00:00:20Z",
			},
			{
				id: "assistant-1",
				conversation_id: "conversation-1",
				role: "assistant",
				content: "Hi",
				sequence: 2,
				created_at: "2026-01-01T00:00:10Z",
			},
		] as Parameters<typeof integrateMessages>[0];
		const localMessages = [
			{
				id: "tool-1",
				conversation_id: "conversation-1",
				role: "tool_call",
				content: null,
				sequence: Date.now(),
				created_at: "2026-01-01T00:00:05Z",
			},
		] as Parameters<typeof integrateMessages>[1];

		expect(
			integrateMessages(serverMessages, localMessages).map(
				(message) => message.id,
			),
		).toEqual(["user-1", "assistant-1", "tool-1"]);
	});
});
